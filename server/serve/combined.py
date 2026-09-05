"""Compose the dynamic altermagnet site and its OPTIMADE services together."""

import logging
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from httk.core import report
from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.optimade import OptimadeIndexConfig, create_index_asgi_app
from httk.serve.web import create_asgi_app as create_web_asgi_app
from starlette.applications import Starlette

from .dsp import DSP_MOUNT, build_dsp_app
from .service import (
    AMDB_DESCRIPTION,
    AMDB_NAME,
    AMDB_PROVIDER,
    INDEX_DESCRIPTION,
    INDEX_NAME,
    build_service_app,
)

logger = report.context_logger(logging.getLogger("httk.altermagnets.combined"), "combined")

ROOT = Path(__file__).resolve().parents[2]

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


def _default_amdb_app(
    public_origin: str = DEFAULT_COMBINED_PUBLIC_BASE_URL, *, require_prebuilt: bool = False
) -> Starlette:
    amdb_url = _public_url(public_origin, AMDB_PATH)
    index_url = _public_url(public_origin, INDEX_PATH)
    return build_service_app(
        public_base_url=amdb_url,
        root_link_target=index_url,
        root_link_id="index",
        root_link_name=INDEX_NAME,
        root_link_description=INDEX_DESCRIPTION,
        root_link_homepage=public_origin,
        require_prebuilt=require_prebuilt,
    )


def _close_created_app(app: Starlette, operation_error: BaseException) -> None:
    """Close a factory-created child app without hiding another failure."""

    engine = getattr(app.state, "engine", None)
    close = getattr(engine, "close", None)
    if callable(close):
        try:
            close()
        except BaseException as cleanup_error:
            operation_error.add_note(f"Additional httk-serve web cleanup failure: {cleanup_error!r}")
    if getattr(app.state, "owns_entry_store", False):
        database = getattr(app.state, "entry_database", None)
        dispose = getattr(database, "dispose", None)
        if callable(dispose):
            try:
                dispose()
            except BaseException as cleanup_error:
                operation_error.add_note(f"Additional AMDB store cleanup failure: {cleanup_error!r}")


def create_combined_app(
    *,
    web_app: Starlette | None = None,
    index_app: Starlette | None = None,
    amdb_app: Starlette | None = None,
    web_factory: Callable[[], Starlette] | None = None,
    index_factory: Callable[[], Starlette] | None = None,
    amdb_factory: Callable[[], Starlette] | None = None,
    public_base_url: str = DEFAULT_COMBINED_PUBLIC_BASE_URL,
    require_prebuilt: bool = False,
) -> Starlette:
    """Compose the website, OPTIMADE index, and AMDB at their public paths."""

    public_origin = _normalize_public_origin(public_base_url)
    if web_app is not None and web_factory is not None:
        raise ValueError("supply web_app or web_factory, not both")
    if index_app is not None and index_factory is not None:
        raise ValueError("supply index_app or index_factory, not both")
    if amdb_app is not None and amdb_factory is not None:
        raise ValueError("supply amdb_app or amdb_factory, not both")
    if require_prebuilt and (amdb_app is not None or amdb_factory is not None):
        raise ValueError("require_prebuilt needs the combined app to create its AMDB service")

    created_apps: list[Starlette] = []
    if web_app is None:
        web_app = (web_factory or _default_web_app)()
        created_apps.append(web_app)
    try:
        if index_app is None:
            index_app = (index_factory or (lambda: _default_index_app(public_origin)))()
            created_apps.append(index_app)
        if amdb_app is None:
            amdb_app = (
                amdb_factory()
                if amdb_factory is not None
                else _default_amdb_app(public_origin, require_prebuilt=require_prebuilt)
            )
            created_apps.append(amdb_app)
        mounts = [ASGIAppMount(INDEX_PATH, index_app), ASGIAppMount(AMDB_PATH, amdb_app)]
        if public_origin.startswith("https://"):
            mounts.append(ASGIAppMount(DSP_MOUNT, build_dsp_app(public_origin)))
        else:
            logger.info("DSP catalogue not mounted: a non-HTTPS public origin (%s) cannot host DSP", public_origin)
        return compose_asgi_apps(mounts, root=ASGIAppMount("/", web_app))
    except BaseException as exc:
        for app in reversed(created_apps):
            _close_created_app(app, exc)
        raise
