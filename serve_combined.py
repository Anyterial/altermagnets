"""Serve the dynamic altermagnet site and its OPTIMADE API as one ASGI app."""

import argparse
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from httk.serve.web import create_asgi_app as create_web_asgi_app
from httk.serve.web.runtime.devserver import run_dev_server
from starlette.applications import Starlette
from starlette.routing import Mount

from optimade_service import build_service_app  # isort: skip
from serve_optimade import build_providers  # isort: skip

ROOT = Path(__file__).resolve().parent


def _default_web_app() -> Starlette:
    return create_web_asgi_app(ROOT / "src", config_name="config_combined")


DEFAULT_COMBINED_PUBLIC_BASE_URL = "http://127.0.0.1:8080/optimade"


def _default_optimade_app(public_base_url: str = DEFAULT_COMBINED_PUBLIC_BASE_URL) -> Starlette:
    records: dict[str, object] = {}
    providers = build_providers(public_base_url=public_base_url, material_records=records)
    return build_service_app(public_base_url=public_base_url, providers=providers, dataset=records)


def _close_created_web_app(app: Starlette, operation_error: BaseException) -> None:
    """Close a newly-created httk-serve web engine without hiding another failure."""

    engine = getattr(app.state, "engine", None)
    close = getattr(engine, "close", None)
    if not callable(close):
        return
    try:
        close()
    except BaseException as cleanup_error:
        operation_error.add_note(f"Additional httk-serve web cleanup failure: {cleanup_error!r}")


@asynccontextmanager
async def _combined_lifespan(app: Starlette) -> AsyncIterator[None]:
    """Enter mounted child lifespans once, unwinding them in reverse order."""

    optimade_app: Starlette = app.state.optimade_app
    web_app: Starlette = app.state.web_app
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(web_app.router.lifespan_context(web_app))
        await stack.enter_async_context(optimade_app.router.lifespan_context(optimade_app))
        yield


def create_combined_app(
    *,
    web_app: Starlette | None = None,
    optimade_app: Starlette | None = None,
    web_factory: Callable[[], Starlette] | None = None,
    optimade_factory: Callable[[], Starlette] | None = None,
    public_base_url: str = DEFAULT_COMBINED_PUBLIC_BASE_URL,
) -> Starlette:
    """Create the opt-in combined app with OPTIMADE mounted before web routes.

    Callers may inject already-owned child apps for tests or embedding.  Apps
    built by the supplied/default factories are owned during construction so a
    later construction failure can close a newly-created httk-serve web engine.
    """

    if web_app is not None and web_factory is not None:
        raise ValueError("supply web_app or web_factory, not both")
    if optimade_app is not None and optimade_factory is not None:
        raise ValueError("supply optimade_app or optimade_factory, not both")

    created_web_app = web_app is None
    if web_app is None:
        web_app = (web_factory or _default_web_app)()
    try:
        if optimade_app is None:
            factory = optimade_factory or (lambda: _default_optimade_app(public_base_url))
            optimade_app = factory()
    except BaseException as exc:
        if created_web_app:
            _close_created_web_app(web_app, exc)
        raise

    try:
        app = Starlette(
            lifespan=_combined_lifespan,
            routes=[Mount("/optimade", optimade_app), Mount("/", web_app)],
        )
        app.state.optimade_app = optimade_app
        app.state.web_app = web_app
        return app
    except BaseException as exc:
        if created_web_app:
            _close_created_web_app(web_app, exc)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the altermagnets website and OPTIMADE API together.")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind when serving")
    parser.add_argument("--port", type=int, default=8080, help="port to bind when serving")
    parser.add_argument(
        "--public-base-url",
        help="public OPTIMADE base URL; required when binding 0.0.0.0 or ::",
    )
    args = parser.parse_args(argv)
    if args.public_base_url is None:
        if args.host in {"0.0.0.0", "::"}:
            parser.error("--public-base-url is required when binding a wildcard host")
        public_host = f"[{args.host}]" if ":" in args.host else args.host
        args.public_base_url = f"http://{public_host}:{args.port}/optimade"
    run_dev_server(
        app=create_combined_app(public_base_url=args.public_base_url),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
