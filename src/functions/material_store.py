"""Stored records and persistent/in-memory loaders for the altermagnets site.

Runtime prefers an offline-built DuckDB file, but can seed the same
``SqlStore`` schema into an in-memory SQLite database from the three source
tables when the persistent store is absent or unusable. The record classes are
deliberately ordinary frozen dataclasses: the schema is declared with
httk-core's storage markers and implemented by ``httk.data.db.SqlStore``.
"""

import bz2
import csv
import hashlib
import logging
import math
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar, cast

from httk.atomistic import (
    CartesianSiteMoments,
    UnitcellStructure,
    UnitcellStructureView,
)
from httk.atomistic.storage.records import (
    CellRecord,
    NormalizedCompositionAmountRecord,
    NormalizedCompositionRecord,
    SitesRecord,
    SpeciesRecord,
    UnitcellStructureRecord,
)
from httk.core import File, Indexed, Skip, StorageInfo, Unique, load, report
from httk.core.storage import project_storage_record
from httk.data.db import Database, SqlStore

__all__ = [
    "AMDB_DATASET",
    "AMDB_ID_COLUMN",
    "CLASSIFICATION_LABELS",
    "ELECTRONIC_TYPE_LABELS",
    "MAGNDATA_COLLINEAR_FILENAME",
    "MAGNDATA_NONCOLLINEAR_FILENAME",
    "PAPER_PICKED_MATERIALS",
    "SCREENING_RESULTS_FILENAME",
    "STORE_LAYOUT_VERSION",
    "STORE_PATH_ENVIRONMENT",
    "MagndataRecord",
    "MaterialFigure",
    "MaterialMagndataLink",
    "MaterialRecord",
    "OpenedMaterialStore",
    "PlotFile",
    "StoreLayout",
    "SymmetryVariant",
    "build_material_records",
    "build_store",
    "cleanup_material_store",
    "default_data_dir",
    "default_details_dir",
    "default_store_path",
    "details_dir_for_material",
    "load_material_structure",
    "material_id_aliases",
    "material_structure",
    "open_in_memory_store",
    "open_material_store",
    "open_prebuilt_store",
    "parse_magnetization_moments",
    "resolve_data_dir",
    "resolve_details_dir",
    "resolve_store_path",
]

# Site diagnostics ride the unified httk reporting channel (httk.core.report):
# emission is plain stdlib logging under the "httk" hierarchy, tagged with the
# "altermagnets" context. `httk.core.report.configure_reporting(level="info")`
# (or `context_levels={"altermagnets": "debug"}`) turns them on.
logger = report.context_logger(logging.getLogger("httk.altermagnets.material_store"), "altermagnets")

#: The record-schema generation of the prebuilt store. Stamped by :func:`build_store`
#: and required by :func:`open_prebuilt_store`, so a store built before a schema
#: change is treated as stale (falling back to in-memory seeding) instead of being
#: silently adopted with missing child tables reading as ``None``. Bump on every
#: stored-record schema change.
STORE_LAYOUT_VERSION = 2

ELEMENT_PATTERN = re.compile(r"[A-Z][a-z]?")
SCREENING_RESULTS_FILENAME = "high_throughput_screening_results_fixed.csv"
MAGNDATA_COLLINEAR_FILENAME = "altermagnets_collinear.csv"
MAGNDATA_NONCOLLINEAR_FILENAME = "altermagnets_noncollinear.csv"
AMDB_ID_COLUMN = "AMDBId"
AMDB_DATASET = "1"
STORE_PATH_ENVIRONMENT = "ALTERMAGNETS_STORE_PATH"
DETAILS_PATH_ENVIRONMENT = "ALTERMAGNETS_DETAILS_DIR"
MATERIAL_ID_PATTERN = re.compile(r"^(?:anyt:)?(?P<family>am|amdb)-(?P<series>[A-Za-z0-9]+)-(?P<number>\d+)$")
LEGACY_MATERIAL_ID_PATTERN = re.compile(r"^(?:anyt:)?amdb-(?P<number>\d+)$")
PLOT_FILENAMES: tuple[tuple[str, str], ...] = (
    ("band", "band.svg"),
    ("structure", "structure.svg"),
    ("bz", "bz.svg"),
)

CLASSIFICATION_LABELS = {
    "collinear": "Collinear",
    "noncollinear-derived": "Based on noncollinear",
    "mixed": "Both",
    "unclassified": "Not classified yet",
}

ELECTRONIC_TYPE_LABELS = {
    "metallic": "Metallic",
    "semiconducting": "Semiconducting",
    "unknown": "KS gap unavailable",
}

PAPER_PICKED_MATERIALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("UCr2Si2C", ("ucr2si2c",)),
    ("NbMnP", ("nbmnp", "mnnbp")),
    ("YRuO3", ("yruo3",)),
)


@dataclass(frozen=True)
class StoreLayout:
    """The prebuilt store's record-schema generation, stamped at build time.

    A missing row (older store) or a mismatched version marks the store stale;
    see :data:`STORE_LAYOUT_VERSION`.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_store_layout")

    version: int


@dataclass(frozen=True)
class SymmetryVariant:
    """One source-kind/symprec presentation of a MAGNDATA record."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="altermagnets_symmetry_variants",
        indexes=(("source_kind", "symprec"),),
    )

    source_kind: Annotated[str, Indexed()]
    formula: str
    symprec: float | None
    symprec_variants: int
    magnetic_phases: tuple[str, ...]
    wave_classes: tuple[str, ...]
    parent_spacegroups: tuple[str, ...]
    parent_spacegroups_latex: tuple[str, ...]
    bns_mcif_labels: tuple[str, ...]
    bns_mcif_labels_latex: tuple[str, ...]
    bns_labels: tuple[str, ...]
    bns_labels_latex: tuple[str, ...]
    effective_bns_labels: tuple[str, ...]
    effective_bns_labels_latex: tuple[str, ...]
    g_laue_classes: tuple[str, ...]
    h_laue_classes: tuple[str, ...]
    connecting_elements: tuple[str, ...]
    connecting_elements_latex: tuple[str, ...]
    spin_angle_mismatch: float | None
    spin_length_mismatch: float | None
    icsd_ids: tuple[str, ...]
    reference_dois: tuple[str, ...]
    warnings: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class MagndataRecord:
    """A MAGNDATA identifier and its ordered source variants.

    ``id`` is intentionally an opaque string.  In particular, values such as
    ``"0.800"`` must never be parsed as floating-point numbers.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_magndata_records")

    id: Annotated[str, Unique(), Indexed()]
    variants: tuple[SymmetryVariant, ...]


@dataclass(frozen=True)
class MaterialMagndataLink:
    """An ordered material-to-MAGNDATA relation."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_material_magndata_links")

    ordinal: Annotated[int, Indexed()]
    record: MagndataRecord


@dataclass(frozen=True)
class PlotFile(File):
    """A stored OPTIMADE File entry for one generated plot asset.

    The current SQL mapper does not yet persist mapping-valued fields, so the
    optional checksum mapping remains part of the File API but is deliberately
    skipped here. Plot bytes stay in the mounted detail tree; ``url`` is the
    root-relative, containment-checked path to those bytes.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_plot_files")

    url: Annotated[str, Indexed()]
    checksums: Annotated[Mapping[str, str] | None, Skip()] = None


@dataclass(frozen=True)
class MaterialFigure:
    """One named plot and its preferred light/dark File entries."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_material_figures")

    key: Annotated[str, Indexed()]
    light: PlotFile
    dark: PlotFile | None


@dataclass(frozen=True)
class MaterialRecord:
    """One screened material and its ordered MAGNDATA relationships."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="altermagnets_material_records",
        indexes=(
            ("classification", "screening_rank"),
            ("electronic_type", "screening_rank"),
            ("space_group", "screening_rank"),
            ("space_group_search", "screening_rank"),
            ("max_ss", "screening_rank"),
            ("avg_ss", "screening_rank"),
            ("bandgap", "screening_rank"),
            ("min_abund_ppm", "max_ss", "screening_rank"),
        ),
    )

    id: Annotated[str, Unique(), Indexed()]
    screening_rank: Annotated[int, Indexed()]
    formula: str
    space_group: Annotated[str, Indexed()]
    # This is a justified query normalization rather than a display mirror:
    # the legacy UI defines this filter as a case-insensitive substring test.
    space_group_search: Annotated[str, Indexed()]
    classification: Annotated[str, Indexed()]
    electronic_type: Annotated[str, Indexed()]
    max_ss: Annotated[float | None, Indexed()]
    avg_ss: Annotated[float | None, Indexed()]
    fdelta_pct: Annotated[float | None, Indexed()]
    bandgap: Annotated[float | None, Indexed()]
    min_abund_ppm: Annotated[float | None, Indexed()]
    links: tuple[MaterialMagndataLink, ...]
    figures: tuple[MaterialFigure, ...]
    elements: tuple[str, ...]
    magnetic_phases: tuple[str, ...]
    wave_classes: tuple[str, ...]
    parent_spacegroups: tuple[str, ...]
    parent_spacegroups_latex: tuple[str, ...]
    icsd_ids: tuple[str, ...]
    dois: tuple[str, ...]
    search_text: str
    structure: UnitcellStructureRecord | None = None


@dataclass(frozen=True)
class OpenedMaterialStore:
    """The explicitly owned runtime database/store pair and its material count."""

    database: Database
    store: SqlStore
    material_count: int
    mode: str
    revision: str
    source_path: Path


def default_data_dir() -> Path:
    """The conventional source-table directory."""
    return Path(__file__).resolve().parents[2] / "data" / "tables"


def default_details_dir() -> Path:
    """The conventional generated-detail asset directory."""
    return Path(__file__).resolve().parents[2] / "data" / "details"


def default_store_path() -> Path:
    """The checked-in-location persistent site store (normally git-ignored)."""
    return Path(__file__).resolve().parents[2] / "data" / "altermagnets.duckdb"


def resolve_data_dir(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve builder CSV input, retaining the existing data-dir override."""
    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get("ALTERMAGNETS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_data_dir()


def resolve_details_dir(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve generated plot assets for persistent builds and memory seeding."""

    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get(DETAILS_PATH_ENVIRONMENT, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_details_dir()


def resolve_store_path(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the explicit persistent-store target/runtime input path."""
    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get(STORE_PATH_ENVIRONMENT, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_store_path()


def _parsed_material_id(material_id: str) -> tuple[str, str] | None:
    cleaned = material_id.strip()
    match = MATERIAL_ID_PATTERN.fullmatch(cleaned)
    if match is not None:
        return match.group("series"), match.group("number")
    legacy_match = LEGACY_MATERIAL_ID_PATTERN.fullmatch(cleaned)
    if legacy_match is None:
        return None
    return "1", legacy_match.group("number")


def material_id_aliases(material_id: str) -> tuple[str, ...]:
    """Return canonical and legacy material-ID spellings in priority order."""

    cleaned = material_id.strip()
    parsed = _parsed_material_id(cleaned)
    if parsed is None:
        return (cleaned,) if cleaned else ()

    series, digits = parsed
    aliases = (
        cleaned,
        f"anyt:am-{series}-{digits}",
        f"am-{series}-{digits}",
        f"anyt:amdb-{series}-{digits}",
        f"amdb-{series}-{digits}",
    )
    return tuple(dict.fromkeys(aliases))


def details_dir_for_material(details_root: Path, material_id: str) -> Path | None:
    """Resolve the existing canonical/legacy detail shard for one material."""

    parsed = _parsed_material_id(material_id)
    if parsed is None:
        return None
    series, digits = parsed
    padded_digits = digits.zfill(3)
    shard_roots = (
        details_root / f"am-{series}" / padded_digits[:1] / padded_digits[:2] / padded_digits[:3],
        details_root / f"amdb-{series}" / padded_digits[:1] / padded_digits[:2] / padded_digits[:3],
    )
    candidates = tuple(shard_root / alias for shard_root in shard_roots for alias in material_id_aliases(material_id))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0] if candidates else None


def parse_magnetization_moments(text: str) -> list[float]:
    """Parse per-ion total moments from a VASP ``magnetization (x)`` block."""

    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if "magnetization (x)" in line.lower()]
    if not starts:
        raise ValueError("No magnetization (x) section found")

    start = starts[-1]
    header = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip().lower().startswith("# of ion")),
        None,
    )
    if header is None:
        raise ValueError("Magnetization section has no ion header")
    separator = next(
        (index for index in range(header + 1, len(lines)) if re.fullmatch(r"-{4,}", lines[index].strip())),
        None,
    )
    if separator is None:
        raise ValueError("Magnetization section has no opening separator")

    moments: list[float] = []
    closing_separator = None
    for index in range(separator + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped:
            continue
        if re.fullmatch(r"-{4,}", stripped):
            closing_separator = index
            break
        parts = stripped.split()
        if len(parts) < 3:
            raise ValueError(f"Malformed magnetization ion row: {lines[index]!r}")
        try:
            ion = int(parts[0])
            values = [float(value) for value in parts[1:]]
        except ValueError as error:
            raise ValueError(f"Malformed magnetization ion row: {lines[index]!r}") from error
        if ion <= 0 or not values or not all(math.isfinite(value) for value in values):
            raise ValueError(f"Malformed magnetization ion row: {lines[index]!r}")
        moments.append(values[-1])

    if closing_separator is None or not moments:
        raise ValueError("Magnetization section has no complete ion table")
    if not any(lines[index].strip().lower().startswith("tot") for index in range(closing_separator + 1, len(lines))):
        raise ValueError("Magnetization section has no total row")
    return moments


def load_material_structure(details_root: Path, material_id: str) -> UnitcellStructure | None:
    """Load a material's exact structure and optional VASP z-axis moments.

    VASP collinear moments are quantization-axis projections, so served moments
    are represented as Cartesian vectors ``(0, 0, m)`` in µB.
    """

    details_dir = details_dir_for_material(details_root, material_id)
    if details_dir is None or not details_dir.is_dir():
        logger.debug("No details directory for %s under %s", material_id, details_root)
        return None
    contcar = details_dir / "CONTCAR.bz2"
    if not contcar.is_file():
        logger.debug("No CONTCAR.bz2 for %s in %s", material_id, details_dir)
        return None
    try:
        structure = load(str(contcar))
    except Exception as error:
        # A malformed CONTCAR must not sink the dataset, but the failure must be
        # visible: an environmental cause (e.g. the CONTCAR reader missing) fails
        # here for EVERY material, which the summary log then makes obvious.
        logger.warning("Could not load %s: %s", contcar, error)
        return None
    logger.debug("Loaded structure for %s: %d sites", material_id, len(structure.sites))

    magn = details_dir / "MAGN.bz2"
    try:
        with bz2.open(magn, "rt", encoding="utf-8") as handle:
            moments = parse_magnetization_moments(handle.read())
    except (OSError, UnicodeError, ValueError) as error:
        logger.warning("No usable MAGN moments for %s: %s", material_id, error)
        return structure
    if len(moments) != len(structure.sites):
        logger.warning(
            "Ignoring MAGN moments for %s: %d rows for %d sites",
            material_id,
            len(moments),
            len(structure.sites),
        )
        return structure

    return UnitcellStructure(
        structure.cell,
        structure.sites,
        structure.species,
        structure.species_at_sites,
        site_moments=CartesianSiteMoments([[0.0, 0.0, moment] for moment in moments]),
        molecular=structure.molecular,
        assemblies=structure.assemblies,
        symmetry=structure.symmetry,
        chemical_composition=structure.chemical_composition,
        chemical_formula_descriptive=structure.chemical_formula_descriptive,
        chemical_formula_hill=structure.chemical_formula_hill,
        optimization_type=structure.optimization_type,
        immutable_id=structure.immutable_id,
        last_modified=structure.last_modified,
    )


def _material_structure_record(structure: UnitcellStructure) -> UnitcellStructureRecord:
    """Project a live structure using httk-atomistic's nested record idiom."""

    projected = dict(project_storage_record(UnitcellStructureRecord, structure))
    projected["cell"] = CellRecord(**project_storage_record(CellRecord, structure.cell))  # type: ignore[arg-type]
    projected["sites"] = SitesRecord(**project_storage_record(SitesRecord, structure.sites))  # type: ignore[arg-type]
    projected["species"] = tuple(
        SpeciesRecord(**project_storage_record(SpeciesRecord, species))  # type: ignore[arg-type]
        for species in structure.species
    )
    normalized = project_storage_record(NormalizedCompositionRecord, structure.composition)
    normalized_amounts = cast(Any, normalized["amounts"])
    projected["normalized_composition"] = NormalizedCompositionRecord(
        tuple(NormalizedCompositionAmountRecord(*amount) for amount in normalized_amounts),
        structure.composition.complete,
    )
    return UnitcellStructureRecord(**projected)  # type: ignore[arg-type]


def material_structure(record: MaterialRecord) -> UnitcellStructure | None:
    """Reconstruct the live structure stored on a material record."""

    return None if record.structure is None else UnitcellStructureView(record.structure)


def _plot_file(path: Path, *, details_root: Path, key: str, theme: str) -> PlotFile:
    resolved_root = details_root.resolve()
    resolved_path = path.resolve()
    relative = resolved_path.relative_to(resolved_root).as_posix()
    media_type = "image/png" if path.suffix.lower() == ".png" else "image/svg+xml"
    return PlotFile(
        url=relative,
        name=path.name,
        size=path.stat().st_size,
        media_type=media_type,
        description=f"{key} plot ({theme} theme)",
    )


def _material_figures(details_root: Path, material_id: str) -> tuple[MaterialFigure, ...]:
    details_dir = details_dir_for_material(details_root, material_id)
    if details_dir is None:
        return ()

    figures: list[MaterialFigure] = []
    for key, svg_name in PLOT_FILENAMES:
        svg_path = details_dir / svg_name
        png_path = svg_path.with_suffix(".png")
        dark_png_path = png_path.with_name(f"{png_path.stem}_dark{png_path.suffix}")
        if png_path.is_file() and dark_png_path.is_file():
            figures.append(
                MaterialFigure(
                    key,
                    _plot_file(png_path, details_root=details_root, key=key, theme="light"),
                    _plot_file(
                        dark_png_path,
                        details_root=details_root,
                        key=key,
                        theme="dark",
                    ),
                )
            )
        elif svg_path.is_file():
            figures.append(
                MaterialFigure(
                    key,
                    _plot_file(svg_path, details_root=details_root, key=key, theme="light"),
                    None,
                )
            )
    return tuple(figures)


def _default_material_id(index: int) -> str:
    return f"anyt:am-{AMDB_DATASET}-{index:04d}"


def _parse_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text or text in {".", "?"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _clean_display_text(value: str) -> str:
    text = (value or "").strip()
    if not text or text in {".", "?"}:
        return ""
    text = text.replace("\\allowbreak", "")
    text = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", text)
    text = re.sub(r"\\overline\{([^}]*)\}", lambda match: f"-{match.group(1)}", text)
    text = re.sub(r"_\{([^}]*)\}", r"_\1", text)
    text = re.sub(r"\^\{\\prime\}", "′", text)
    text = text.replace("\\prime", "′")
    text = text.replace("$", "").replace("{", "").replace("}", "").replace("\\", "")
    return re.sub(r"\s+", " ", text).strip()


def _clean_latex_text(value: str) -> str:
    text = (value or "").strip()
    return "" if not text or text in {".", "?"} else text


def _split_magndata_ids(value: str) -> list[str]:
    # MAGNDATA identifiers are opaque strings, not numeric values.
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _extract_elements(formula: str) -> list[str]:
    seen: set[str] = set()
    elements: list[str] = []
    for token in ELEMENT_PATTERN.findall(formula or ""):
        if token not in seen:
            seen.add(token)
            elements.append(token)
    return elements


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return tuple(deduped)


def _load_csv_rows(path: Path, *, delimiter: str = ",") -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f"required altermagnets source table is missing: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def _source_table_paths(data_dir: Path) -> tuple[Path, Path, Path]:
    return (
        data_dir / SCREENING_RESULTS_FILENAME,
        data_dir / MAGNDATA_COLLINEAR_FILENAME,
        data_dir / MAGNDATA_NONCOLLINEAR_FILENAME,
    )


def _load_source_materials(data_dir: Path, *, details_dir: Path) -> tuple[MaterialRecord, ...]:
    screening_path, collinear_path, noncollinear_path = _source_table_paths(data_dir)
    return build_material_records(
        _load_csv_rows(screening_path, delimiter=";"),
        _load_csv_rows(collinear_path),
        _load_csv_rows(noncollinear_path),
        details_dir=details_dir,
    )


def _source_revision(data_dir: Path, *, details_dir: Path) -> str:
    digest = hashlib.sha256(b"altermagnets-memory-store-v2\0")
    for path in _source_table_paths(data_dir):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    if details_dir.is_dir():
        for path in sorted(
            (path for path in details_dir.rglob("*") if path.is_file() and path.suffix.lower() in {".svg", ".png"}),
            key=lambda path: path.as_posix(),
        ):
            metadata = path.stat()
            digest.update(path.relative_to(details_dir).as_posix().encode("utf-8"))
            digest.update(f"\0{metadata.st_size}\0{metadata.st_mtime_ns}\0".encode())
    return f"memory-{digest.hexdigest()[:24]}"


def _persistent_revision(path: Path) -> str:
    metadata = path.stat()
    return f"duckdb-{metadata.st_size:x}-{metadata.st_mtime_ns:x}"


def summarize_symmetry_rows(rows: list[dict[str, str]], *, source_kind: str) -> list[tuple[str, SymmetryVariant]]:
    """Group one source table into ordered, display-preserving variants."""
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        magndata_id = (row.get("MAGNDATAId") or "").strip()
        if magndata_id:
            grouped.setdefault(magndata_id, []).append(row)

    summaries: list[tuple[str, SymmetryVariant]] = []
    for magndata_id, group_rows in grouped.items():
        rows_by_symprec: dict[float | None, list[dict[str, str]]] = {}
        for row in group_rows:
            rows_by_symprec.setdefault(_parse_float(row.get("Symprec", "")), []).append(row)

        for symprec, variant_rows in rows_by_symprec.items():
            summaries.append(
                (
                    magndata_id,
                    SymmetryVariant(
                        source_kind=source_kind,
                        formula=_clean_display_text(
                            next(
                                (row.get("ChemicalFormula", "") for row in variant_rows if row.get("ChemicalFormula")),
                                "",
                            )
                        ),
                        symprec=symprec,
                        symprec_variants=len(variant_rows),
                        magnetic_phases=_dedupe(
                            _clean_display_text(row.get("MagneticPhaseShort", "")) for row in variant_rows
                        ),
                        wave_classes=_dedupe(_clean_display_text(row.get("WaveClass", "")) for row in variant_rows),
                        parent_spacegroups=_dedupe(
                            _clean_display_text(row.get("ParentSpacegroup", "")) for row in variant_rows
                        ),
                        parent_spacegroups_latex=_dedupe(
                            _clean_latex_text(row.get("ParentSpacegroup", "")) for row in variant_rows
                        ),
                        bns_mcif_labels=_dedupe(_clean_display_text(row.get("BNSmcif", "")) for row in variant_rows),
                        bns_mcif_labels_latex=_dedupe(
                            _clean_latex_text(row.get("BNSmcif", "")) for row in variant_rows
                        ),
                        bns_labels=_dedupe(_clean_display_text(row.get("BNS", "")) for row in variant_rows),
                        bns_labels_latex=_dedupe(_clean_latex_text(row.get("BNS", "")) for row in variant_rows),
                        effective_bns_labels=_dedupe(
                            _clean_display_text(row.get("EffectiveBNS", "")) for row in variant_rows
                        ),
                        effective_bns_labels_latex=_dedupe(
                            _clean_latex_text(row.get("EffectiveBNS", "")) for row in variant_rows
                        ),
                        g_laue_classes=_dedupe(
                            _clean_display_text(row.get("GMagneticSystemLaueClass", "")) for row in variant_rows
                        ),
                        h_laue_classes=_dedupe(
                            _clean_display_text(row.get("HHalvingSubgroupLaueClass", "")) for row in variant_rows
                        ),
                        connecting_elements=_dedupe(
                            _clean_display_text(row.get("AGenopConnectingElement", "")) for row in variant_rows
                        ),
                        connecting_elements_latex=_dedupe(
                            _clean_latex_text(row.get("AGenopConnectingElement", "")) for row in variant_rows
                        ),
                        spin_angle_mismatch=max(
                            (
                                value
                                for value in (_parse_float(row.get("SpinAngleMismatch", "")) for row in variant_rows)
                                if value is not None
                            ),
                            default=None,
                        ),
                        spin_length_mismatch=max(
                            (
                                value
                                for value in (_parse_float(row.get("SpinLengthMismatch", "")) for row in variant_rows)
                                if value is not None
                            ),
                            default=None,
                        ),
                        icsd_ids=_dedupe(_clean_display_text(row.get("ICSDId", "")) for row in variant_rows),
                        reference_dois=_dedupe(
                            _clean_display_text(row.get("ReferenceDOI", "")) for row in variant_rows
                        ),
                        warnings=_dedupe(_clean_display_text(row.get("Warnings", "")) for row in variant_rows),
                        notes=_dedupe(_clean_display_text(row.get("Notes", "")) for row in variant_rows),
                    ),
                )
            )

    return sorted(
        summaries,
        key=lambda item: (
            item[0],
            1 if item[1].symprec is None else 0,
            float(item[1].symprec or 0.0),
        ),
    )


def _classification_from_sources(has_collinear: bool, has_noncollinear: bool) -> str:
    if has_collinear and has_noncollinear:
        return "mixed"
    if has_collinear:
        return "collinear"
    if has_noncollinear:
        return "noncollinear-derived"
    return "unclassified"


def build_material_records(
    screening_rows: list[dict[str, str]],
    collinear_rows: list[dict[str, str]],
    noncollinear_rows: list[dict[str, str]],
    *,
    details_dir: Path | None = None,
) -> tuple[MaterialRecord, ...]:
    """Normalize the three current CSVs into the persistent object graph."""
    grouped_variants: dict[str, list[SymmetryVariant]] = {}
    for magndata_id, variant in sorted(
        summarize_symmetry_rows(collinear_rows, source_kind="collinear")
        + summarize_symmetry_rows(noncollinear_rows, source_kind="noncollinear-derived"),
        key=lambda item: (
            item[0],
            item[1].source_kind,
            1 if item[1].symprec is None else 0,
            float(item[1].symprec or 0.0),
        ),
    ):
        grouped_variants.setdefault(magndata_id, []).append(variant)

    magndata_records = {
        identifier: MagndataRecord(identifier, tuple(variants)) for identifier, variants in grouped_variants.items()
    }
    materials: list[MaterialRecord] = []
    seen_material_ids: set[str] = set()

    for index, row in enumerate(screening_rows, start=1):
        material_id = (row.get(AMDB_ID_COLUMN) or "").strip() or _default_material_id(index)
        if material_id in seen_material_ids:
            raise ValueError(f"duplicate canonical material ID {material_id!r} in screening row {index}")
        seen_material_ids.add(material_id)

        magndata_ids = _split_magndata_ids(row.get("MAGNDATA ID", ""))
        links = tuple(
            MaterialMagndataLink(
                ordinal=ordinal,
                record=magndata_records.setdefault(magndata_id, MagndataRecord(magndata_id, ())),
            )
            for ordinal, magndata_id in enumerate(magndata_ids, start=1)
        )
        linked_variants = [variant for link in links for variant in link.record.variants]
        has_collinear = any(variant.source_kind == "collinear" for variant in linked_variants)
        has_noncollinear = any(variant.source_kind == "noncollinear-derived" for variant in linked_variants)
        classification = _classification_from_sources(has_collinear, has_noncollinear)
        formula = (row.get("Material") or "").strip()
        bandgap = _parse_float(row.get("Bandgap", ""))
        electronic_type = "unknown" if bandgap is None else "semiconducting" if bandgap > 0 else "metallic"

        phases = _dedupe(phase for variant in linked_variants for phase in variant.magnetic_phases)
        waves = _dedupe(wave for variant in linked_variants for wave in variant.wave_classes)
        spacegroups = _dedupe(spacegroup for variant in linked_variants for spacegroup in variant.parent_spacegroups)
        spacegroups_latex = _dedupe(
            spacegroup for variant in linked_variants for spacegroup in variant.parent_spacegroups_latex
        )
        icsd_ids = _dedupe(identifier for variant in linked_variants for identifier in variant.icsd_ids)
        dois = _dedupe(doi for variant in linked_variants for doi in variant.reference_dois)
        elements = tuple(_extract_elements(formula))
        material_space_group = (row.get("Space group") or "").strip()
        search_text = " ".join(
            (
                formula.lower(),
                " ".join(identifier.lower() for identifier in magndata_ids),
                material_space_group.lower(),
                " ".join(element.lower() for element in elements),
                " ".join(spacegroup.lower() for spacegroup in spacegroups),
                " ".join(phase.lower() for phase in phases),
                " ".join(wave.lower() for wave in waves),
                classification,
            )
        ).strip()
        loaded_structure = None if details_dir is None else load_material_structure(details_dir, material_id)
        materials.append(
            MaterialRecord(
                id=material_id,
                screening_rank=index,
                formula=formula,
                space_group=material_space_group,
                space_group_search=material_space_group.lower(),
                classification=classification,
                electronic_type=electronic_type,
                max_ss=_parse_float(row.get("MaxSS", "")),
                avg_ss=_parse_float(row.get("AvgSS", "")),
                fdelta_pct=_parse_float(row.get("FdeltaPct", "")),
                bandgap=bandgap,
                min_abund_ppm=_parse_float(row.get("MinAbundPpm", "")),
                links=links,
                figures=_material_figures(details_dir, material_id) if details_dir is not None else (),
                elements=elements,
                magnetic_phases=phases,
                wave_classes=waves,
                parent_spacegroups=spacegroups,
                parent_spacegroups_latex=spacegroups_latex,
                icsd_ids=icsd_ids,
                dois=dois,
                search_text=search_text,
                structure=None if loaded_structure is None else _material_structure_record(loaded_structure),
            )
        )

    with_structures = sum(1 for material in materials if material.structure is not None)
    logger.info(
        "Built %d material records, %d with structures (details dir: %s)",
        len(materials),
        with_structures,
        details_dir,
    )
    if details_dir is not None and materials and with_structures == 0:
        logger.warning(
            "No material got a structure: check that %s holds the detail shard tree "
            "and that the CONTCAR reader is importable (httk-io)",
            details_dir,
        )
    return tuple(materials)


def build_store(
    target: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    details_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Build a fresh store next to ``target`` and atomically replace it.

    The caller never sees a partially written target: the DuckDB connection is
    disposed before :func:`os.replace` commits the completed temporary file.
    """
    resolved_target = resolve_store_path(target)
    source_dir = resolve_data_dir(data_dir)
    resolved_details_dir = resolve_details_dir(details_dir)
    materials = _load_source_materials(source_dir, details_dir=resolved_details_dir)

    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved_target.name}.",
        suffix=".tmp",
        dir=resolved_target.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    database: Database | None = None
    try:
        created_database = Database.duckdb(temporary_path)
        database = created_database
        # This is a private, custom-record store rather than an OPTIMADE entry
        # store.  Declare that fact when creating its versioned layout.
        store = SqlStore(created_database, entry_records={})
        store.ensure_tables(MaterialRecord)
        with store.transaction():
            store.save(StoreLayout(STORE_LAYOUT_VERSION))
            for material in materials:
                store.save(material)
        created_database.dispose()
        database = None
        os.replace(temporary_path, resolved_target)
    except BaseException:
        if database is not None:
            database.dispose()
        temporary_path.unlink(missing_ok=True)
        raise
    return resolved_target


def open_prebuilt_store(path: str | os.PathLike[str] | None = None) -> OpenedMaterialStore | None:
    """Open a nonempty prebuilt store, returning ``None`` for unavailable data.

    This deliberately never creates tables, metadata, or a missing file.  The
    store is opened in layout-verification mode, so an old/unversioned prebuilt
    database is unavailable rather than silently adopted or altered. Runtime
    callers can therefore keep the existing unavailable-page behavior when
    deployments omit, corrupt, ship an empty store, or ship an incompatible
    store.
    """
    store_path = resolve_store_path(path)
    database: Database | None = None
    try:
        if not store_path.is_file() or store_path.stat().st_size == 0:
            logger.info("No prebuilt store at %s", store_path)
            return None
        opened_database = Database.duckdb(store_path)
        database = opened_database
        store = SqlStore(opened_database)
        # The layout stamp is the authoritative staleness check: a store built
        # before a schema change lacks the row (or carries an older version).
        # Reads treat a missing child table as None rather than erroring, so
        # merely touching new record fields cannot detect such a store.
        layout_searcher = store.searcher()
        layout = layout_searcher.variable(StoreLayout)
        layout_row = layout_searcher.results(layout=layout).first()
        stamped = None if layout_row is None else layout_row["layout"].version
        if stamped != STORE_LAYOUT_VERSION:
            logger.info(
                "Prebuilt store %s is stale (layout %s, need %d); rebuild with `make build_store`",
                store_path,
                stamped,
                STORE_LAYOUT_VERSION,
            )
            opened_database.dispose()
            return None
        searcher = store.searcher()
        material = searcher.variable(MaterialRecord)
        material_count = searcher.count()
        if material_count <= 0:
            logger.info("Prebuilt store %s holds no materials", store_path)
            opened_database.dispose()
            return None
        sample = searcher.results(material=material).first()
        if sample is None:
            opened_database.dispose()
            return None
        # Counting only touches the root table. Reconstruct one record as a
        # lightweight schema probe so drifted child tables fail here rather
        # than at first use.
        tuple(sample["material"].figures)
        _ = sample["material"].structure
        logger.info("Opened prebuilt store %s: %d materials", store_path, material_count)
        return OpenedMaterialStore(
            opened_database,
            store,
            material_count,
            mode="persistent",
            revision=_persistent_revision(store_path),
            source_path=store_path,
        )
    except Exception as error:
        logger.warning("Prebuilt store %s is unusable: %s", store_path, error)
        if database is not None:
            database.dispose()
        return None


def open_in_memory_store(
    data_dir: str | os.PathLike[str] | None = None,
    *,
    details_dir: str | os.PathLike[str] | None = None,
) -> OpenedMaterialStore | None:
    """Seed an in-memory SQLite store from the source tables when available."""

    source_dir = resolve_data_dir(data_dir)
    resolved_details_dir = resolve_details_dir(details_dir)
    database: Database | None = None
    try:
        logger.info(
            "Seeding an in-memory store from %s (details: %s) — slow; prefer `make build_store`",
            source_dir,
            resolved_details_dir,
        )
        materials = _load_source_materials(source_dir, details_dir=resolved_details_dir)
        if not materials:
            return None
        opened_database = Database.sqlite()
        database = opened_database
        # The in-memory fallback is another fresh private/custom store.
        store = SqlStore(opened_database, entry_records={})
        store.ensure_tables(MaterialRecord)
        with store.transaction():
            for material in materials:
                store.save(material)
        return OpenedMaterialStore(
            opened_database,
            store,
            len(materials),
            mode="memory",
            revision=_source_revision(source_dir, details_dir=resolved_details_dir),
            source_path=source_dir,
        )
    except Exception:
        if database is not None:
            database.dispose()
        return None


def open_material_store(
    path: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    details_dir: str | os.PathLike[str] | None = None,
) -> OpenedMaterialStore | None:
    """Prefer the scalable persistent store, falling back to in-memory seeding."""

    persistent = open_prebuilt_store(path)
    if persistent is not None:
        return persistent
    return open_in_memory_store(data_dir, details_dir=details_dir)


def cleanup_material_store(global_data: MutableMapping[str, Any]) -> None:
    """Dispose the locally owned runtime database, safely more than once.

    Site startup registers this helper with httk-serve's resource lifecycle; it
    remains useful to local runners and tests as an explicit idempotent seam.
    """
    global_data.pop("materials_store", None)
    database = global_data.pop("materials_database", None)
    global_data.pop("materials_store_path", None)
    global_data.pop("materials_store_mode", None)
    global_data.pop("materials_store_source", None)
    global_data.pop("materials_store_revision", None)
    if isinstance(database, Database):
        database.dispose()
