"""Stored records and persistent/in-memory loaders for the altermagnets site.

Runtime prefers an offline-built DuckDB file, but can seed the same
``SqlStore`` schema into an in-memory SQLite database from the three source
tables when the persistent store is absent or unusable. The record classes are
deliberately ordinary frozen dataclasses: the schema is declared with
httk-core's storage markers and implemented by ``httk.data.db.SqlStore``.
"""

import csv
import hashlib
import math
import os
import re
import tempfile
from collections.abc import Iterable, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar

from httk.core import Indexed, StorageInfo, Unique
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
    "STORE_PATH_ENVIRONMENT",
    "MagndataRecord",
    "MaterialMagndataLink",
    "MaterialRecord",
    "OpenedMaterialStore",
    "SymmetryVariant",
    "build_material_records",
    "build_store",
    "cleanup_material_store",
    "default_data_dir",
    "default_store_path",
    "open_in_memory_store",
    "open_material_store",
    "open_prebuilt_store",
    "resolve_data_dir",
    "resolve_store_path",
]

ELEMENT_PATTERN = re.compile(r"[A-Z][a-z]?")
SCREENING_RESULTS_FILENAME = "high_throughput_screening_results_fixed.csv"
MAGNDATA_COLLINEAR_FILENAME = "altermagnets_collinear.csv"
MAGNDATA_NONCOLLINEAR_FILENAME = "altermagnets_noncollinear.csv"
AMDB_ID_COLUMN = "AMDBId"
AMDB_DATASET = "1"
STORE_PATH_ENVIRONMENT = "ALTERMAGNETS_STORE_PATH"

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
    elements: tuple[str, ...]
    magnetic_phases: tuple[str, ...]
    wave_classes: tuple[str, ...]
    parent_spacegroups: tuple[str, ...]
    parent_spacegroups_latex: tuple[str, ...]
    icsd_ids: tuple[str, ...]
    dois: tuple[str, ...]
    search_text: str


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
    """The checked-in CSV directory used by the explicit offline builder."""
    return Path(__file__).resolve().parents[2] / "data" / "tables"


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


def resolve_store_path(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the explicit persistent-store target/runtime input path."""
    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get(STORE_PATH_ENVIRONMENT, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_store_path()


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


def _load_source_materials(data_dir: Path) -> tuple[MaterialRecord, ...]:
    screening_path, collinear_path, noncollinear_path = _source_table_paths(data_dir)
    return build_material_records(
        _load_csv_rows(screening_path, delimiter=";"),
        _load_csv_rows(collinear_path),
        _load_csv_rows(noncollinear_path),
    )


def _source_revision(data_dir: Path) -> str:
    digest = hashlib.sha256(b"altermagnets-memory-store-v1\0")
    for path in _source_table_paths(data_dir):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
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
                        magnetic_phases=_dedupe(_clean_display_text(row.get("MagneticPhaseShort", "")) for row in variant_rows),
                        wave_classes=_dedupe(_clean_display_text(row.get("WaveClass", "")) for row in variant_rows),
                        parent_spacegroups=_dedupe(
                            _clean_display_text(row.get("ParentSpacegroup", "")) for row in variant_rows
                        ),
                        parent_spacegroups_latex=_dedupe(
                            _clean_latex_text(row.get("ParentSpacegroup", "")) for row in variant_rows
                        ),
                        bns_mcif_labels=_dedupe(_clean_display_text(row.get("BNSmcif", "")) for row in variant_rows),
                        bns_mcif_labels_latex=_dedupe(_clean_latex_text(row.get("BNSmcif", "")) for row in variant_rows),
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
                            (value for value in (_parse_float(row.get("SpinAngleMismatch", "")) for row in variant_rows) if value is not None),
                            default=None,
                        ),
                        spin_length_mismatch=max(
                            (value for value in (_parse_float(row.get("SpinLengthMismatch", "")) for row in variant_rows) if value is not None),
                            default=None,
                        ),
                        icsd_ids=_dedupe(_clean_display_text(row.get("ICSDId", "")) for row in variant_rows),
                        reference_dois=_dedupe(_clean_display_text(row.get("ReferenceDOI", "")) for row in variant_rows),
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
        identifier: MagndataRecord(identifier, tuple(variants))
        for identifier, variants in grouped_variants.items()
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
                elements=elements,
                magnetic_phases=phases,
                wave_classes=waves,
                parent_spacegroups=spacegroups,
                parent_spacegroups_latex=spacegroups_latex,
                icsd_ids=icsd_ids,
                dois=dois,
                search_text=search_text,
            )
        )

    return tuple(materials)


def build_store(
    target: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Build a fresh store next to ``target`` and atomically replace it.

    The caller never sees a partially written target: the DuckDB connection is
    disposed before :func:`os.replace` commits the completed temporary file.
    """
    resolved_target = resolve_store_path(target)
    source_dir = resolve_data_dir(data_dir)
    materials = _load_source_materials(source_dir)

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
        store = SqlStore(created_database)
        store.ensure_tables(MaterialRecord)
        with store.transaction():
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

    This deliberately never creates tables or a missing file.  Runtime callers
    can therefore keep the existing unavailable-page behavior when deployments
    omit, corrupt, or accidentally ship an empty store.
    """
    store_path = resolve_store_path(path)
    database: Database | None = None
    try:
        if not store_path.is_file() or store_path.stat().st_size == 0:
            return None
        opened_database = Database.duckdb(store_path)
        database = opened_database
        store = SqlStore(opened_database, create_tables=False)
        searcher = store.searcher()
        searcher.variable(MaterialRecord)
        material_count = searcher.count()
        if material_count <= 0:
            opened_database.dispose()
            return None
        return OpenedMaterialStore(
            opened_database,
            store,
            material_count,
            mode="persistent",
            revision=_persistent_revision(store_path),
            source_path=store_path,
        )
    except Exception:  # noqa: BLE001 - a corrupt or unsupported external store is unavailable to the site.
        if database is not None:
            database.dispose()
        return None


def open_in_memory_store(data_dir: str | os.PathLike[str] | None = None) -> OpenedMaterialStore | None:
    """Seed an in-memory SQLite store from the source tables when available."""

    source_dir = resolve_data_dir(data_dir)
    database: Database | None = None
    try:
        materials = _load_source_materials(source_dir)
        if not materials:
            return None
        opened_database = Database.sqlite()
        database = opened_database
        store = SqlStore(opened_database)
        store.ensure_tables(MaterialRecord)
        with store.transaction():
            for material in materials:
                store.save(material)
        return OpenedMaterialStore(
            opened_database,
            store,
            len(materials),
            mode="memory",
            revision=_source_revision(source_dir),
            source_path=source_dir,
        )
    except Exception:  # noqa: BLE001 - malformed or unavailable migration inputs leave the site data-less.
        if database is not None:
            database.dispose()
        return None


def open_material_store(
    path: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
) -> OpenedMaterialStore | None:
    """Prefer the scalable persistent store, falling back to in-memory seeding."""

    persistent = open_prebuilt_store(path)
    if persistent is not None:
        return persistent
    return open_in_memory_store(data_dir)


def cleanup_material_store(global_data: MutableMapping[str, Any]) -> None:
    """Dispose the locally owned runtime database, safely more than once.

    Site startup registers this helper with httk-web's resource lifecycle; it
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
