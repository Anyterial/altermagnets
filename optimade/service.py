"""Compose the altermagnets OPTIMADE API and its figure-byte route."""

import logging
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import material_store
from httk.core import report
from httk.serve.optimade import OptimadeConfig, adapter_from_providers
from httk.serve.optimade import create_asgi_app as create_optimade_asgi_app
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.routing import Mount, Route

from .adapter import SORTABLE_PROPERTIES, AltermagnetStoreAdapter
from .files import (
    DARK_CACHE_MAX_BYTES,
    DARK_CACHE_MAX_ENTRIES,
    STRUCTURE_DOWNLOADS,
    _dark_svg,
    _figure_index,
    _find_figure,
    _media_type,
    _read_file,
    _stored_figure_match,
    _stored_structure_record,
    resolve_locator_path,
    stored_file_locator,
    structure_download_body,
    structure_download_filename,
)

logger = report.context_logger(logging.getLogger("httk.altermagnets.optimade"), "optimade")

AMDB_PROVIDER = {
    "name": "Anyterial",
    "description": "The Anyterial collection of materials databases.",
    "prefix": "anyt",
}
AMDB_NAME = "Anyterial Altermagnets Database"
AMDB_DESCRIPTION = "A database of materials computationally predicted to exhibit altermagnetism."
AMDB_HOMEPAGE = "https://altermagnets.anyterial.se"
INDEX_NAME = "Anyterial OPTIMADE Index"
INDEX_DESCRIPTION = "Index meta-database for the Anyterial collection of materials databases."


def _service_config(
    *,
    public_base_url: str,
    root_link_target: str | None,
    root_link_id: str | None,
    root_link_name: str,
    root_link_description: str,
    root_link_homepage: str,
    cors_origins: Iterable[str],
) -> OptimadeConfig:
    """Build the AMDB metadata, including its one configured root link."""
    public_base_url = public_base_url.rstrip("/")
    target = (root_link_target or public_base_url).rstrip("/")
    link_id = root_link_id or ("amdb" if root_link_target is None else "index")
    return OptimadeConfig(
        provider=dict(AMDB_PROVIDER),
        links=[
            {
                "id": link_id,
                "name": root_link_name,
                "description": root_link_description,
                "base_url": target,
                "homepage": root_link_homepage.rstrip("/"),
                "link_type": "root",
            }
        ],
        license="https://altermagnets.anyterial.se/about#legal",
        available_licenses=[],
        available_licenses_for_entries=["CC-BY-4.0"],
        # 180-entry dataset fits on one page, so 500 makes the search widget's 100/500 page-size
        # options genuinely functional (httk-serve default is 50).
        page_limit_max=500,
        cors_origins=tuple(cors_origins),
    )


def build_service_app(
    *,
    public_base_url: str,
    cors_origins: Iterable[str] = (),
    providers: Sequence[Any] | None = None,
    store: Any | None = None,
    dataset: Mapping[str, Any] | None = None,
    details_root: Path | None = None,
    runs_root: Path | None = None,
    root_link_target: str | None = None,
    root_link_id: str | None = None,
    root_link_name: str = AMDB_NAME,
    root_link_description: str = AMDB_DESCRIPTION,
    root_link_homepage: str = AMDB_HOMEPAGE,
) -> Starlette:
    """Build the shared OPTIMADE-plus-figures ASGI application.

    The service advertises one configured root link. In standalone use it points
    to itself as ``amdb``; a composed deployment can point it to its parent
    index by supplying the ``root_link_*`` parent metadata.
    """
    if providers is not None and store is not None:
        raise ValueError("supply providers or store, not both")
    owned_database = None
    if providers is None and store is None:
        opened = material_store.open_material_store(
            data_dir=material_store.resolve_data_dir(),
            details_dir=material_store.resolve_details_dir(),
        )
        if opened is None:
            raise RuntimeError("no altermagnets material store is available")
        if opened.mode == "memory":
            logger.warning(
                "Serving from an in-memory SQLite store seeded from the source tables, not the "
                "prebuilt read-only store; rebuild with `make build_store` for a persistent store"
            )
        store = opened.store
        owned_database = opened.database
    index = _figure_index(dataset) if dataset is not None else None
    resolved_details_root = (details_root or material_store.resolve_details_dir()).resolve()
    resolved_runs_root = (runs_root or material_store.resolve_runs_dir()).resolve()
    dark_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
    dark_cache_bytes = 0

    async def figure_response(request: Request) -> Response:
        nonlocal dark_cache_bytes
        material_id = request.path_params["material_id"]
        filename = request.path_params["filename"]
        download = STRUCTURE_DOWNLOADS.get(filename)
        if download is not None:
            # Structure files are generated on request from the DATABASE structure main
            # (never the detail tree), keyed on the structure id (anyt.am.structure-1-N).
            # Requires a live store.
            record = _stored_structure_record(store, material_id) if store is not None else None
            if record is None:
                return Response(status_code=404)
            body = structure_download_body(record, download)
            if body is None:
                return Response(status_code=404)
            return Response(
                body,
                media_type=download.content_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{structure_download_filename(record.id, download)}"',
                    "Cache-Control": "public, max-age=3600",
                    "X-Content-Type-Options": "nosniff",
                    "Access-Control-Allow-Origin": "*",
                },
            )
        match = (
            index.get(material_id)
            if index is not None
            else _stored_figure_match(store, material_id)
            if store is not None
            else None
        )
        if match is None:
            return Response(status_code=404)
        canonical_id, figures = match
        if material_store.details_dir_for_material(resolved_details_root, material_id) is None:
            return Response(status_code=404)
        selected = _find_figure(figures, filename)
        if selected is None:
            return Response(status_code=404)
        file, generated_dark = selected
        cache_key = (canonical_id, filename)
        cached = dark_cache.pop(cache_key, None) if generated_dark else None
        if cached is not None:
            dark_cache[cache_key] = cached
            body = cached
        else:
            raw = _read_file(file, resolved_details_root)
            if raw is None:
                logger.warning("Missing or oversized figure file for %s/%s", material_id, filename)
                return Response(status_code=404)
            generated_body = _dark_svg(raw) if generated_dark else raw
            if generated_body is None:
                logger.warning("Generated dark figure exceeds the size cap for %s/%s", material_id, filename)
                return Response(status_code=404)
            body = generated_body
            if generated_dark:
                dark_cache[cache_key] = body
                dark_cache_bytes += len(body)
                while len(dark_cache) > DARK_CACHE_MAX_ENTRIES or dark_cache_bytes > DARK_CACHE_MAX_BYTES:
                    _, evicted = dark_cache.popitem(last=False)
                    dark_cache_bytes -= len(evicted)
        return Response(
            body,
            media_type="image/svg+xml" if generated_dark else _media_type(file),
            headers={
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Access-Control-Allow-Origin": "*",
            },
        )

    async def file_entry_response(request: Request) -> Response:
        # Stream one served FileRecord's bytes on demand from the imported-runs tree.
        # Requires a live store; the id is the store-minted anyt.am.files-1-N.
        if store is None:
            return Response(status_code=404)
        located = stored_file_locator(store, request.path_params["id"])
        if located is None:
            return Response(status_code=404)
        path = resolve_locator_path(located.locator, resolved_runs_root)
        if path is None:
            return Response(status_code=404)
        # Content-Disposition uses the locator basename (compressed, e.g. .bz2), not
        # FileRecord.name (which strips .bz2). No size cap: OUTCARs exceed the figure cap.
        return FileResponse(
            path,
            media_type=located.media_type or "application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{Path(located.locator).name}"',
                "Cache-Control": "public, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Access-Control-Allow-Origin": "*",
            },
        )

    adapter = (
        adapter_from_providers(providers, sortable=SORTABLE_PROPERTIES)
        if providers is not None
        else AltermagnetStoreAdapter(store, public_base_url)
    )
    optimade_app = create_optimade_asgi_app(
        adapter,
        _service_config(
            public_base_url=public_base_url,
            root_link_target=root_link_target,
            root_link_id=root_link_id,
            root_link_name=root_link_name,
            root_link_description=root_link_description,
            root_link_homepage=root_link_homepage,
            cors_origins=cors_origins,
        ),
        baseurl=None,
    )

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        try:
            yield
        finally:
            if owned_database is not None:
                owned_database.dispose()

    app = Starlette(
        routes=[
            Route("/extensions/files/entry/{id}", file_entry_response, methods=["GET", "HEAD"]),
            Route("/extensions/files/{material_id}/{filename}", figure_response, methods=["GET", "HEAD"]),
            Mount("", optimade_app),
        ],
        lifespan=lifespan,
    )
    app.state.entry_store = store
    app.state.owns_entry_store = owned_database is not None
    return app
