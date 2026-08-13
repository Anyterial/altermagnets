"""Serve the dynamic altermagnet site and its OPTIMADE services together."""

import argparse
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.optimade import OptimadeIndexConfig, create_index_asgi_app
from httk.serve.web import create_asgi_app as create_web_asgi_app
from httk.serve.web.runtime.devserver import run_dev_server
from optimade_service import (
    AMDB_DESCRIPTION,
    AMDB_NAME,
    AMDB_PROVIDER,
    INDEX_DESCRIPTION,
    INDEX_NAME,
    build_service_app,
)  # isort: skip
from starlette.applications import Starlette

from serve_optimade import build_providers  # isort: skip

ROOT = Path(__file__).resolve().parent

DEFAULT_COMBINED_PUBLIC_BASE_URL = "http://127.0.0.1:8080"
INDEX_PATH = "/optimade/index"
AMDB_PATH = "/optimade/amdb"


def _default_web_app() -> Starlette:
    return create_web_asgi_app(ROOT / "src", config_name="config_combined")


def _normalize_public_origin(value: object) -> str:
    """Validate and normalize the public origin used by the combined app."""
    if not isinstance(value, str) or not value:
        raise ValueError("public_base_url must be a non-empty HTTP(S) origin")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _port = parsed.port
    except ValueError as exc:
        raise ValueError("public_base_url must be a valid HTTP(S) origin") from exc
    if (
        any(character.isspace() for character in value)
        or "?" in value
        or "#" in value
        or parsed.scheme.lower() not in {"http", "https"}
        or hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("public_base_url must be an HTTP(S) origin without credentials, path, query, or fragment")
    return f"{parsed.scheme.lower()}://{parsed.netloc}"


def _public_url(public_origin: str, path: str) -> str:
    return public_origin + path


def _index_link(
    *, link_id: str, name: str, description: str, base_url: str, homepage: str, link_type: str
) -> dict[str, str]:
    return {
        "id": link_id,
        "name": name,
        "description": description,
        "base_url": base_url,
        "homepage": homepage.rstrip("/"),
        "link_type": link_type,
    }


def _default_index_app(public_origin: str = DEFAULT_COMBINED_PUBLIC_BASE_URL) -> Starlette:
    index_url = _public_url(public_origin, INDEX_PATH)
    amdb_url = _public_url(public_origin, AMDB_PATH)
    config = OptimadeIndexConfig(
        provider=dict(AMDB_PROVIDER),
        links=[
            _index_link(
                link_id="index",
                name=INDEX_NAME,
                description=INDEX_DESCRIPTION,
                base_url=index_url,
                homepage=public_origin,
                link_type="root",
            ),
            _index_link(
                link_id="amdb",
                name=AMDB_NAME,
                description=AMDB_DESCRIPTION,
                base_url=amdb_url,
                homepage=public_origin,
                link_type="child",
            ),
        ],
        default_link_id="amdb",
    )
    return create_index_asgi_app(config, baseurl=index_url)


def _default_amdb_app(public_origin: str = DEFAULT_COMBINED_PUBLIC_BASE_URL) -> Starlette:
    amdb_url = _public_url(public_origin, AMDB_PATH)
    index_url = _public_url(public_origin, INDEX_PATH)
    records: dict[str, object] = {}
    providers = build_providers(public_base_url=amdb_url, material_records=records)
    return build_service_app(
        public_base_url=amdb_url,
        providers=providers,
        dataset=records,
        root_link_target=index_url,
        root_link_id="index",
        root_link_name=INDEX_NAME,
        root_link_description=INDEX_DESCRIPTION,
        root_link_homepage=public_origin,
    )


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


def create_combined_app(
    *,
    web_app: Starlette | None = None,
    index_app: Starlette | None = None,
    amdb_app: Starlette | None = None,
    web_factory: Callable[[], Starlette] | None = None,
    index_factory: Callable[[], Starlette] | None = None,
    amdb_factory: Callable[[], Starlette] | None = None,
    public_base_url: str = DEFAULT_COMBINED_PUBLIC_BASE_URL,
) -> Starlette:
    """Compose the website, OPTIMADE index, and AMDB at their public paths."""

    public_origin = _normalize_public_origin(public_base_url)
    if web_app is not None and web_factory is not None:
        raise ValueError("supply web_app or web_factory, not both")
    if index_app is not None and index_factory is not None:
        raise ValueError("supply index_app or index_factory, not both")
    if amdb_app is not None and amdb_factory is not None:
        raise ValueError("supply amdb_app or amdb_factory, not both")

    created_web_app = web_app is None
    if web_app is None:
        web_app = (web_factory or _default_web_app)()
    try:
        if index_app is None:
            index_app = (index_factory or (lambda: _default_index_app(public_origin)))()
        if amdb_app is None:
            amdb_app = (amdb_factory or (lambda: _default_amdb_app(public_origin)))()
        return compose_asgi_apps(
            [ASGIAppMount(INDEX_PATH, index_app), ASGIAppMount(AMDB_PATH, amdb_app)],
            root=ASGIAppMount("/", web_app),
        )
    except BaseException as exc:
        if created_web_app:
            _close_created_web_app(web_app, exc)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the altermagnets website and OPTIMADE services together.")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind when serving")
    parser.add_argument("--port", type=int, default=8080, help="port to bind when serving")
    parser.add_argument(
        "--public-base-url",
        help="public HTTP(S) origin; OPTIMADE services are appended at /optimade/index and /optimade/amdb",
    )
    args = parser.parse_args(argv)
    if args.public_base_url is None:
        if args.host in {"0.0.0.0", "::"}:
            parser.error("--public-base-url is required when binding a wildcard host")
        public_host = f"[{args.host}]" if ":" in args.host else args.host
        args.public_base_url = f"http://{public_host}:{args.port}"
    run_dev_server(
        app=create_combined_app(public_base_url=args.public_base_url),
        host=args.host,
        port=args.port,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
