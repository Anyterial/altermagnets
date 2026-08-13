"""Compose the altermagnets OPTIMADE API and its figure-byte route."""

import logging
import mimetypes
import sys
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from httk.core import report
from httk.serve.optimade import OptimadeConfig, adapter_from_providers
from httk.serve.optimade import create_asgi_app as create_optimade_asgi_app
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

FUNCTIONS_ROOT = Path(__file__).resolve().parent / "src" / "functions"
if str(FUNCTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_ROOT))

import material_store

logger = report.context_logger(logging.getLogger("httk.altermagnets.optimade"), "optimade")

MAX_FIGURE_BYTES = 8 * 1024 * 1024
DARK_CACHE_MAX_ENTRIES = 32
DARK_CACHE_MAX_BYTES = 64 * 1024 * 1024


def figure_file_is_servable(size: int | None) -> bool:
    """Return whether recorded figure metadata permits serving the file."""
    return size is not None and 0 <= size <= MAX_FIGURE_BYTES


@dataclass(frozen=True)
class _StoredFile:
    """The figure metadata needed after the store connection is closed."""

    url: str
    name: str
    size: int | None
    media_type: str | None


@dataclass(frozen=True)
class _StoredFigure:
    """A whitelisted light/dark figure pair."""

    light: _StoredFile
    dark: _StoredFile | None


def _copy_file(file: Any) -> _StoredFile:
    return _StoredFile(
        url=str(file.url),
        name=str(file.name),
        size=file.size if isinstance(file.size, int) else None,
        media_type=file.media_type if isinstance(file.media_type, str) else None,
    )


def _figure_index(dataset: Mapping[str, Any]) -> dict[str, tuple[str, tuple[_StoredFigure, ...]]]:
    """Copy figure metadata into an alias-indexed, connection-independent map."""
    result: dict[str, tuple[str, tuple[_StoredFigure, ...]]] = {}
    for material_id, record in dataset.items():
        canonical_id = str(getattr(record, "id", material_id))
        figures = tuple(
            _StoredFigure(_copy_file(figure.light), _copy_file(figure.dark) if figure.dark is not None else None)
            for figure in getattr(record, "figures", ())
        )
        for alias in material_store.material_id_aliases(canonical_id):
            result[alias] = (canonical_id, figures)
    return result


def _load_figure_index() -> dict[str, tuple[str, tuple[_StoredFigure, ...]]]:
    """Load and detach figure metadata using the store's normal runtime loader."""
    opened = material_store.open_material_store(
        data_dir=material_store.resolve_data_dir(),
        details_dir=material_store.resolve_details_dir(),
    )
    if opened is None:
        return {}
    records: dict[str, Any] = {}
    try:
        searcher = opened.store.searcher()
        material = searcher.variable(material_store.MaterialRecord)
        for result in searcher.results(material=material):
            record = result["material"]
            records[record.id] = record
        return _figure_index(records)
    finally:
        material_store.cleanup_material_store({"materials_database": opened.database})


def _media_type(file: _StoredFile) -> str:
    """Return a safe content type for a metadata-whitelisted image filename."""
    if file.media_type in {"image/png", "image/svg+xml"}:
        return file.media_type
    return mimetypes.guess_type(file.name)[0] or "application/octet-stream"


def _safe_path(file: _StoredFile, details_root: Path) -> Path | None:
    """Resolve a recorded path while rejecting absolute paths and symlink escapes."""
    relative = Path(file.url)
    if relative.is_absolute():
        return None
    root = details_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _read_file(file: _StoredFile, details_root: Path) -> bytes | None:
    """Read one stored image under both its recorded size and the hard cap."""
    path = _safe_path(file, details_root)
    if path is None:
        return None
    actual_size = path.stat().st_size
    if not figure_file_is_servable(file.size) or actual_size > MAX_FIGURE_BYTES:
        return None
    if file.size is not None and actual_size > file.size:
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _dark_svg(svg: bytes) -> bytes | None:
    """Apply the existing dark SVG transformation without importing the legacy detail renderer."""
    from re import compile, sub

    text = svg.decode("utf-8", errors="replace")
    white_patterns = (
        (compile(r'(?i)\bfill\s*=\s*"(?:#ffffff|#fff|white)"'), 'fill="none"'),
        (compile(r"(?i)\bfill\s*=\s*'(?:#ffffff|#fff|white)'"), "fill='none'"),
        (compile(r"(?i)\bfill\s*:\s*(?:#ffffff|#fff|white)\b"), "fill: none"),
    )
    black_patterns = (
        compile(r"(?i)(?<![0-9a-f])#000000(?![0-9a-f])"),
        compile(r"(?i)(?<![0-9a-f])#000(?![0-9a-f])"),
        compile(r"(?i)(?<![0-9a-f])#000000ff(?![0-9a-f])"),
        compile(r"(?i)(?<![0-9a-f])#000f(?![0-9a-f])"),
        compile(r"(?i)\brgb\(\s*0%\s*,\s*0%\s*,\s*0%\s*\)\b"),
        compile(r"(?i)\brgb\(\s*0\s*,\s*0\s*,\s*0\s*\)\b"),
        compile(r"(?i)\brgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*1(?:\.0+)?\s*\)\b"),
        compile(r"(?i)\bblack\b"),
        compile(r"(?i)(?<![0-9a-f])#262626(?![0-9a-f])"),
        compile(r"(?i)(?<![0-9a-f])#1f1f1f(?![0-9a-f])"),
        compile(r"(?i)(?<![0-9a-f])#333333(?![0-9a-f])"),
    )
    style = (
        '<style id="httk-dark-svg-text">'
        'g[id^="text_"] path, g[id^="text_"] use, text, tspan {'
        "fill: #f2f5fb !important; color: #f2f5fb !important;} "
        'g[id^="legend_"] g[id^="patch_"] path[style*="opacity: 0.8"], '
        'g[id^="legend_"] g[id^="patch_"] path[style*="opacity:0.8"] {'
        "fill: rgba(28, 33, 40, 0.88) !important; stroke: #7e8793 !important; opacity: 1 !important;}"
        "</style>"
    )
    for pattern, replacement in white_patterns:
        text = pattern.sub(replacement, text)
    for pattern in black_patterns:
        text = pattern.sub("#f2f5fb", text)
    if 'id="httk-dark-svg-text"' not in text:
        text = sub(r"(<svg\b[^>]*>)", r"\1" + style, text, count=1)
    result = text.encode("utf-8")
    return result if len(result) <= MAX_FIGURE_BYTES else None


def _find_figure(figures: tuple[_StoredFigure, ...], filename: str) -> tuple[_StoredFile, bool] | None:
    """Return a recorded file and whether it is a generated dark SVG."""
    for figure in figures:
        if filename == figure.light.name and figure.light.name.endswith((".png", ".svg")):
            return figure.light, False
        if figure.dark is not None and filename == figure.dark.name and figure.dark.name.endswith((".png", ".svg")):
            return figure.dark, False
        if figure.dark is None and figure.light.name.endswith(".svg") and filename == f"dark--{figure.light.name}":
            return figure.light, True
    return None


def build_service_app(
    *,
    public_base_url: str,
    cors_origins: Iterable[str] = (),
    providers: Sequence[Any] | None = None,
    dataset: Mapping[str, Any] | None = None,
    details_root: Path | None = None,
) -> Starlette:
    """Build the shared OPTIMADE-plus-figures ASGI application."""
    if providers is None:
        from serve_optimade import build_providers

        records: dict[str, Any] = {}
        providers = build_providers(public_base_url=public_base_url, material_records=records)
        dataset = records if dataset is None else dataset
    if dataset is None:
        index = _load_figure_index()
    else:
        index = _figure_index(dataset)
    resolved_details_root = (details_root or material_store.resolve_details_dir()).resolve()
    dark_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
    dark_cache_bytes = 0

    async def figure_response(request: Request) -> Response:
        nonlocal dark_cache_bytes
        material_id = request.path_params["material_id"]
        filename = request.path_params["filename"]
        match = index.get(material_id)
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

    from serve_optimade import SORTABLE_PROPERTIES

    optimade_app = create_optimade_asgi_app(
        adapter_from_providers(providers, sortable=SORTABLE_PROPERTIES),
        OptimadeConfig(
            license="https://altermagnets.anyterial.se/about#legal",
            available_licenses=[],
            available_licenses_for_entries=["CC-BY-NC-4.0"],
            cors_origins=tuple(cors_origins),
        ),
        baseurl=None,
    )
    return Starlette(
        routes=[
            Route("/figures/{material_id}/{filename}", figure_response, methods=["GET", "HEAD"]),
            Mount("", optimade_app),
        ]
    )
