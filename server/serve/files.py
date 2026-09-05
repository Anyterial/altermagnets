"""Figure-byte machinery for the altermagnets OPTIMADE figure route."""

import mimetypes
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import material_store
from httk.core import save

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
        material = searcher.variable(material_store.AltermagnetScreeningResult)
        for result in searcher.results(material=material):
            record = result["material"]
            records[record.id] = record
        return _figure_index(records)
    finally:
        material_store.cleanup_material_store({"materials_database": opened.database})


@dataclass(frozen=True)
class _FileLocator:
    """The FileRecord fields the byte route needs after the store connection closes."""

    locator: str
    media_type: str | None


def stored_file_locator(store: Any, file_id: str) -> _FileLocator | None:
    """Return one served FileRecord's tree-relative locator and media type, or None.

    :param store: The live entry store to read.
    :param file_id: The store-minted ``anyt.am.files-1-N`` id to fetch.
    :return: The locator/media-type pair, or ``None`` when no such file exists.
    """
    if not file_id:
        return None
    searcher = store.searcher()
    record = searcher.variable(material_store.FileRecord)
    searcher.add(record.id == file_id)
    row = searcher.results(record=record).first()
    if row is None:
        return None
    file = row["record"]
    return _FileLocator(str(file.url), file.media_type if isinstance(file.media_type, str) else None)


def resolve_locator_path(locator: str, runs_root: Path) -> Path | None:
    """Resolve a tree-relative file locator under ``runs_root``, guarding traversal.

    Absolute locators and any path that resolves (through ``..`` or a symlink)
    outside ``runs_root`` are rejected, mirroring :func:`_safe_path`.

    :param locator: The stored tree-relative locator (``FileRecord.url``).
    :param runs_root: The imported-runs root the locator is relative to.
    :return: The contained regular file, or ``None`` when absent or escaping.
    """
    relative = Path(locator)
    if relative.is_absolute():
        return None
    root = runs_root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _stored_material_record(store: Any, material_id: str) -> Any | None:
    """Fetch the AltermagnetScreeningResult for one public result ID from the store, or None."""
    aliases = material_store.material_id_aliases(material_id)
    if not aliases:
        return None
    searcher = store.searcher()
    material = searcher.variable(material_store.AltermagnetScreeningResult)
    predicate = material.id == aliases[0]
    for alias in aliases[1:]:
        predicate = predicate | (material.id == alias)
    searcher.add(predicate)
    row = searcher.results(material=material).first()
    return None if row is None else row["material"]


def _stored_structure_record(store: Any, structure_id: str) -> Any | None:
    """Fetch one slim ``structures`` main (``UnitcellStructureRecord``) by its stamped id.

    The CIF/POSCAR download routes are keyed on the structure id (``anyt.am.structure-1-N``)
    now that the crystal structure is a separate standard entry.

    :param store: The live entry store to read.
    :param structure_id: The stamped structure id to fetch.
    :return: The structure record, or ``None`` when absent.
    """
    if not structure_id:
        return None
    from httk.atomistic.storage.records import UnitcellStructureRecord

    searcher = store.searcher()
    structure = searcher.variable(UnitcellStructureRecord)
    searcher.add(structure.id == structure_id)
    row = searcher.results(structure=structure).first()
    return None if row is None else row["structure"]


def _stored_figure_match(store: Any, material_id: str) -> tuple[str, tuple[_StoredFigure, ...]] | None:
    """Read current figure metadata for one public material ID from the store."""
    record = _stored_material_record(store, material_id)
    if record is None:
        return None
    indexed = _figure_index({record.id: record})
    return indexed.get(material_id) or indexed.get(record.id)


@dataclass(frozen=True)
class _StructureDownload:
    """A fixed, generated structure-file endpoint (no path-traversal surface)."""

    format: str  # httk-core save() format tag
    content_type: str
    suffix: str  # attachment-name suffix; "" ⇒ the fixed name POSCAR


#: Fixed-name whitelist served from the DATABASE structure via httk-atomistic
#: writers (never the detail tree). Keyed by the request filename.
STRUCTURE_DOWNLOADS: dict[str, _StructureDownload] = {
    "structure.cif": _StructureDownload("cif", "chemical/x-cif", ".cif"),
    "structure.mcif": _StructureDownload("mcif", "chemical/x-cif", ".mcif"),
    "POSCAR": _StructureDownload("vasp-poscar", "text/plain", ""),
}


def structure_download_filename(material_id: str, download: _StructureDownload) -> str:
    """Return the Content-Disposition attachment filename for a download."""
    return f"{material_id}{download.suffix}" if download.suffix else "POSCAR"


def structure_download_body(record: Any, download: _StructureDownload) -> bytes | None:
    """Serialize a slim ``structures`` main to the download format, or None.

    ``record`` is a ``UnitcellStructureRecord`` (the screened crystal, now a separate
    standard entry). ``None`` means the format cannot represent it (e.g. POSCAR partial
    occupancy, or a magnetic CIF for a structure whose collinear magnetic symmetry cannot
    be found) or the generated file exceeds the size cap. httk-core ``save`` selects the
    writer by ``format`` and writes to a path, so a throwaway temp file is the cleanly
    supported buffer.

    The magnetic CIF is derived on the fly: the served record stores a plain unit cell with
    moments, so ``mcif`` first detects the magnetic space group with
    :func:`~httk.atomistic.symmetry.find_magnetic_symmetry` (which may collapse to P1 for a
    structure whose relaxed coordinates are too noisy at the default tolerance -- still a
    valid magnetic CIF). A finder failure (no moments, non-collinear, or spglib absent) is
    caught and yields ``None`` (a 404), leaving the standard CIF and POSCAR unaffected.
    """
    from httk.atomistic import UnitcellStructureView

    structure: Any = UnitcellStructureView(record, kind="record")
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "structure"
        try:
            if download.format == "mcif":
                from httk.atomistic.symmetry import find_magnetic_symmetry

                structure = find_magnetic_symmetry(structure)
            save(structure, path, format=download.format)
            text = path.read_text(encoding="utf-8")
        except (ValueError, OSError, ImportError, TypeError):
            return None
    data = text.encode("utf-8")
    return data if len(data) <= MAX_FIGURE_BYTES else None


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
