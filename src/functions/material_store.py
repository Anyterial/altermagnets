"""Stored records and persistent/in-memory loaders for the altermagnets site.

Runtime prefers an offline-built DuckDB file, but can seed the same
``SqlStore`` schema into an in-memory SQLite database from the three source
tables when the persistent store is absent or unusable. The record classes are
deliberately ordinary frozen dataclasses: the schema is declared with
httk-core's storage markers and implemented by ``httk.store.backend.sql.SqlStore``.
"""

import bz2
import csv
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field, replace
from functools import cache
from pathlib import Path
from typing import Annotated, Any, ClassVar, cast

from httk.atomistic import (
    CartesianSiteMoments,
    Cell,
    Sites,
    UnitcellStructure,
    UnitcellStructureView,
    conventional_cell,
    primitive_cell,
)
from httk.atomistic.entries.structures import StructureEntry
from httk.atomistic.storage.records import (
    CellRecord,
    NormalizedCompositionAmountRecord,
    NormalizedCompositionRecord,
    SitesRecord,
    SpeciesRecord,
    UnitcellStructureRecord,
)
from httk.atomistic.storage.stored_properties import unitcell_structure_properties
from httk.core import (
    DataRecord,
    EntryTypeDefinition,
    File,
    FileRecord,
    IdentitySkip,
    Indexed,
    PropertyDefinition,
    Skip,
    StorageInfo,
    Unique,
    load,
    register_definition_prefix,
    report,
    standard_entry_type,
)
from httk.core.data_records import DataRecordEntry
from httk.core.files import FileEntry
from httk.core.provenance import Run, RunEntry
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import (
    QueryLiteralError,
    StoredPropertyProjection,
    WeakLink,
    content_id,
    project_storage_record,
    stored_property,
)
from httk.store import Backend, EntryIdScheme, SqlStore

__all__ = [
    "AMDB_DATASET",
    "AMDB_ID_COLUMN",
    "MAGNDATA_COLLINEAR_FILENAME",
    "MAGNDATA_NONCOLLINEAR_FILENAME",
    "SCREENING_RESULTS_FILENAME",
    "STORE_LAYOUT_VERSION",
    "STORE_PATH_ENVIRONMENT",
    "AltermagnetReferenceEntry",
    "AltermagnetReferenceRecord",
    "AltermagnetStructureEntry",
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
    "default_runs_dir",
    "default_store_path",
    "details_dir_for_material",
    "details_raw_path",
    "load_material_structure",
    "material_id_aliases",
    "material_structure",
    "open_in_memory_store",
    "open_material_store",
    "open_prebuilt_store",
    "parse_magnetization_moments",
    "resolve_data_dir",
    "resolve_details_dir",
    "resolve_runs_dir",
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
STORE_LAYOUT_VERSION = 9  # bump: produced_by weak link + _httk_custom_total_energy replace provenance snapshot
RELAXED_STRUCTURE_PRECISION = 5e-4  # Cartesian Å; relaxed-DFT coordinate precision, so symmetry tolerance is realistic (not ~machine epsilon from full-precision CONTCAR digits)

ELEMENT_PATTERN = re.compile(r"[A-Z][a-z]?")
SCREENING_RESULTS_FILENAME = "high_throughput_screening_results_fixed.csv"
MAGNDATA_COLLINEAR_FILENAME = "altermagnets_collinear.csv"
MAGNDATA_NONCOLLINEAR_FILENAME = "altermagnets_noncollinear.csv"
AMDB_ID_COLUMN = "AMDBId"
AMDB_DATASET = "1"
STORE_PATH_ENVIRONMENT = "ALTERMAGNETS_STORE_PATH"
RUNS_PATH_ENVIRONMENT = "ALTERMAGNETS_RUNS_DIR"
COUPLING_FILENAME = "amdb_run_content_ids.csv"
DETAILS_PATH_ENVIRONMENT = "ALTERMAGNETS_DETAILS_DIR"
MATERIAL_ID_PATTERN = re.compile(r"^(?:anyt[:.])?(?P<family>am|amdb)-(?P<series>[A-Za-z0-9]+)-(?P<number>\d+)$")
LEGACY_MATERIAL_ID_PATTERN = re.compile(r"^(?:anyt:)?amdb-(?P<number>\d+)$")
PLOT_FILENAMES: tuple[tuple[str, str], ...] = (
    ("band", "band.svg"),
    ("structure", "structure.svg"),
    ("bz", "bz.svg"),
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
        # The one run that produced this material serves as the OPTIMADE
        # ``_httk_runs`` relationship (live, replacing the old denormalized
        # provenance snapshot). Every coupled run's structure edge appears on
        # both the artifact and output side, so one declaration covers it.
        links=(
            WeakLink(
                "produced_by",
                Run,
                exposed_relationship=True,
                role="artifact+output",
                description="The workflow run that produced this material.",
            ),
        ),
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
    magndata_links: tuple[MaterialMagndataLink, ...]
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
    # The coupled run's total-energy DataRecord value, served symmetrically as the
    # material-level ``_httk_custom_total_energy`` scalar beside the live run link.
    total_energy: float | None = None
    # Entry-id fields per the store contract; id is always set to the amdb
    # public id at construction, immutable_id is minted by the store.
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)

    @stored_property
    def magndata_sort_key(self) -> str | None:
        """Scalar mirror of the served ``_httk_magndata_ids`` list for sorting.

        The served value is a list, which the store cannot sort on directly.
        This stored column holds exactly the string the search-table cell
        displays (the linked MAGNDATA ids joined with ``", "`` in stored order),
        or ``None`` when the material has no links, so lexicographic sorting on
        it matches what the reader sees. Join-string order equals Python list
        order only while every id character sorts above the ``","`` separator,
        so ids violating that (or empty ids) are rejected at build time rather
        than silently diverging the store path from the provider path. A
        linkless material serves ``None`` here while the provider path would
        serve ``[]``; unreachable today (every material has at least one link).

        :return: The joined id string, or ``None`` when there are no links.
        :raises ValueError: If an id is empty or contains a character that
            sorts at or below the ``","`` separator.
        """
        ids = [link.record.id for link in self.magndata_links]
        for one in ids:
            if not one or any(ch <= "," for ch in one):
                raise ValueError(f"MAGNDATA id {one!r} breaks join-key sort parity with list ordering")
        return ", ".join(ids) or None


@dataclass(frozen=True)
class AltermagnetReferenceRecord:
    """One DOI-backed OPTIMADE reference stored beside the material entries."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="altermagnets_references",
        indexes=(("public_id",), ("doi",)),
    )

    public_id: Annotated[str, Unique(), Indexed()]
    doi: Annotated[str, Unique(), Indexed()]
    # Entry-id fields per the store contract; id is always set to the stable
    # public reference id at construction, immutable_id is minted by the store.
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class AltermagnetStructureEntry:
    """The store-native, Anyterial-extended OPTIMADE structures family."""

    type = "structures"
    definition_id = "https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/structures"

    @classmethod
    def entry_type_definition(cls) -> EntryTypeDefinition:
        """Return the atomistic structure definition plus Anyterial properties."""
        return StructureEntry.entry_type_definition().extended(_optimade_definitions())


class AltermagnetReferenceEntry:
    """The store-native OPTIMADE references family used by AMDB."""

    type = "references"
    definition_id = "https://schemas.optimade.org/defs/v1.2/entrytypes/optimade/references"

    @classmethod
    def entry_type_definition(cls) -> EntryTypeDefinition:
        """Return the standard reference definition plus its private public-ID field."""
        return standard_entry_type("references").extended(
            {"_httk_custom_public_id": _private_id_definition("references")}
        )


ANYTERIAL_DEFS_BASE = "https://schemas.anyterial.se/defs/v0.1/properties"
ANYTERIAL_DEFINITION_PATHS = {
    "_anyterial_formula": "formula.json",
    "_anyterial_elements": "elements_present.json",
    "_anyterial_space_group": "space_group.json",
    "_anyterial_space_group_search": "space_group_search.json",
    "_anyterial_classification": "classification.json",
    "_anyterial_magnetic_phases": "magnetic_phases.json",
    "_anyterial_wave_classes": "wave_classes.json",
    "_anyterial_parent_spacegroups": "parent_spacegroups.json",
    "_anyterial_icsd_ids": "icsd_ids.json",
    "_anyterial_search_text": "search_text.json",
    "_anyterial_magndata_variants": "magndata_variants.json",
    "_anyterial_avg_spin_splitting": "avg_spin_splitting.json",
    "_anyterial_max_spin_splitting": "max_spin_splitting.json",
    "_anyterial_spin_splitting_fraction": "spin_splitting_fraction.json",
    "_anyterial_magnetic_phase": "magnetic_phase.json",
    "_anyterial_wave_class": "wave_class.json",
    "_anyterial_electronic_type": "electronic_type.json",
    "_anyterial_min_crustal_abundance": "min_crustal_abundance.json",
    "_anyterial_screening_rank": "screening_rank.json",
}
HTTK_DEFINITION_PATHS = {
    "_httk_dft_band_gap": "electronic/dft_band_gap.json",
    "_httk_magnetic_space_group_bns": "magnetism/magnetic_space_group_bns.json",
    "_httk_magndata_ids": "magnetism/magndata_ids.json",
}
ANYTERIAL_DEFS_DIR = Path(__file__).resolve().parents[2] / (
    "dependencies/submodules/anyterial-schemas/defs/v0.1/properties/altermagnets"
)
HTTK_DEFS_DIR = Path(__file__).resolve().parents[2] / "dependencies/submodules/httk-schemas/defs/v0.1/properties"


def _private_id_definition(entry_type: str) -> PropertyDefinition:
    document = PropertyDefinition.from_simple(
        "_httk_custom_public_id",
        description=f"The deployment-owned public identifier for one {entry_type} entry.",
    ).as_optimade()
    document["x-optimade-requirements"] = {
        "support": "may",
        "query-support": "none",
        "response-level": "must not",
    }
    return PropertyDefinition.from_optimade("_httk_custom_public_id", document)


def _local_figure_definition() -> PropertyDefinition:
    document = PropertyDefinition.from_simple(
        "_httk_custom_figures",
        description=(
            "Data-only figure metadata for this deployment's fixed band-structure, "
            "crystal-structure, and Brillouin-zone figures."
        ),
        fulltype="list of dict",
        unit="inapplicable",
    ).as_optimade()
    document["x-optimade-requirements"] = {
        "support": "may",
        "query-support": "none",
        "response-level": "should not",
    }
    return PropertyDefinition.from_optimade("_httk_custom_figures", document)


def _local_total_energy_definition() -> PropertyDefinition:
    document = PropertyDefinition.from_simple(
        "_httk_custom_total_energy",
        description="Total energy (eV) of the workflow run that produced this material.",
        fulltype="float",
        unit="eV",
    ).as_optimade()
    document["x-optimade-requirements"] = {
        "support": "may",
        "query-support": "none",
        "response-level": "should not",
    }
    return PropertyDefinition.from_optimade("_httk_custom_total_energy", document)


def _private_reference_ids_definition() -> PropertyDefinition:
    document = PropertyDefinition.from_simple(
        "_httk_custom_reference_ids",
        description="Private reference identifiers used to construct the OPTIMADE relationships block.",
        fulltype="list of string",
    ).as_optimade()
    document["x-optimade-requirements"] = {
        "support": "may",
        "query-support": "none",
        "response-level": "must not",
    }
    return PropertyDefinition.from_optimade("_httk_custom_reference_ids", document)


@cache
def _optimade_definitions() -> dict[str, PropertyDefinition]:
    """Load the curated AMDB definitions used by both stored schema and docs tests."""
    register_definition_prefix("_anyterial_", ANYTERIAL_DEFS_BASE)
    definitions: dict[str, PropertyDefinition] = {}
    for base, paths in ((ANYTERIAL_DEFS_DIR, ANYTERIAL_DEFINITION_PATHS), (HTTK_DEFS_DIR, HTTK_DEFINITION_PATHS)):
        for served_name, relative_path in paths.items():
            path = base / relative_path
            if not path.is_file():
                raise RuntimeError(
                    f"Property definition file {path} is missing; initialize the schema submodules "
                    "via `git submodule update --init` or `make update_schemas`."
                )
            definitions[served_name] = PropertyDefinition.from_optimade(
                served_name, json.loads(path.read_text(encoding="utf-8"))
            )
    definitions["_httk_custom_figures"] = _local_figure_definition()
    definitions["_httk_custom_total_energy"] = _local_total_energy_definition()
    definitions["_httk_custom_public_id"] = _private_id_definition("structure")
    definitions["_httk_custom_reference_ids"] = _private_reference_ids_definition()
    return definitions


def _reference_id(doi: str) -> str:
    """Return a stable public reference ID without depending on row order."""
    # The DOI digest is rendered decimal so the id conforms to the recommended
    # <base>-<series>-<number> entry-id syntax while staying row-order independent.
    return f"anyt.ref-1-{int(hashlib.sha256(doi.encode('utf-8')).hexdigest()[:16], 16)}"


class _NestedQueryContext:
    """Re-root a neutral query context at one stored reference field."""

    def __init__(self, root: Any, scope: Any) -> None:
        self._root = root
        self._scope = scope

    def field(self, name: str) -> Any:
        return self._scope.field(name)

    def scope(self, name: str) -> Any:
        return self._scope.scope(name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._root, name)


def _nested_structure_projection(projection: StoredPropertyProjection) -> StoredPropertyProjection:
    def response(record: object) -> object:
        material = cast(MaterialRecord, record)
        return None if material.structure is None else projection.response(material.structure)

    def query(context: Any, operator: str, literal: object) -> Any:
        assert projection.query is not None
        return projection.query(cast(Any, _NestedQueryContext(context, context.scope("structure"))), operator, literal)

    def sort(context: Any) -> Any:
        assert projection.sort is not None
        return projection.sort(cast(Any, _NestedQueryContext(context, context.scope("structure"))))

    return StoredPropertyProjection(
        response=response,
        query=query if projection.query is not None else None,
        sort=sort if projection.sort is not None else None,
    )


def _scalar_query(field: str, *, literal_transform: Any = None):
    def query(context: Any, operator: str, literal: object) -> Any:
        value = context.field(field)
        if operator == "IS_UNKNOWN":
            return context.is_null(value)
        if operator == "IS_KNOWN":
            return context.not_(context.is_null(value))
        mapped = literal_transform(literal) if literal_transform is not None else literal
        return context.compare(value, operator, context.constant(mapped))

    return query


def _list_query(field: str):
    def query(context: Any, operator: str, literal: object) -> Any:
        values = context.scope(field)
        if operator == "IS_UNKNOWN":
            return context.equal(context.count(values), context.constant(0))
        if operator == "IS_KNOWN":
            return context.compare(context.count(values), ">", context.constant(0))
        literals = literal if isinstance(literal, tuple) else (literal,)
        predicates = []
        for item in literals:
            candidate = context.scope(field)
            predicates.append(
                context.exists(candidate, context.equal(candidate.field("value"), context.constant(item)))
            )
        if operator in {"HAS", "HAS_ALL"}:
            return context.and_(*predicates)
        if operator == "HAS_ANY":
            return context.or_(*predicates)
        if operator == "HAS_ONLY":
            return context.and_(
                context.equal(context.count(values), context.constant(len(set(literals)))),
                context.and_(*predicates),
            )
        raise QueryLiteralError(f"unsupported list operator {operator!r}")

    return query


def _list_scalar_query(field: str, *, literals: Mapping[object, object] | None = None):
    def query(context: Any, operator: str, literal: object) -> Any:
        values = context.scope(field)
        known = context.compare(context.count(values), ">", context.constant(0))
        if operator == "IS_UNKNOWN":
            return context.not_(known)
        if operator == "IS_KNOWN":
            return known
        if operator not in {"=", "!="}:
            raise QueryLiteralError("scalar list projections support equality only")
        mapped = literals.get(literal, literal) if literals is not None else literal
        candidate = context.scope(field)
        matches = context.exists(candidate, context.equal(candidate.field("value"), context.constant(mapped)))
        result = context.when_known(known, matches)
        return result if operator == "=" else context.not_(result)

    return query


def _provider_property(record: object, name: str) -> object:
    # This compatibility projector is pure and does not enumerate the store.
    # A root-relative base is made absolute by the thin service adapter.
    from optimade.dataset import _material_properties

    return _material_properties(cast(MaterialRecord, record), "")[name]


def _provider_response(property_name: str) -> Callable[[object], object]:
    def response(record: object) -> object:
        return _provider_property(record, property_name)

    return response


def _field_sort(field_name: str) -> Callable[[Any], Any]:
    def sort(context: Any) -> Any:
        return context.field(field_name)

    return sort


def _fraction_literal_to_percent(value: object) -> object:
    return cast(Any, value) * 100


def _material_public_id(record: object) -> str:
    public_id = cast(MaterialRecord, record).id
    assert public_id is not None  # always set to the amdb id at construction
    return public_id


def _material_reference_ids(record: object) -> list[str]:
    return [_reference_id(doi) for doi in cast(MaterialRecord, record).dois]


def _reference_doi(record: object) -> str:
    return cast(AltermagnetReferenceRecord, record).doi


def _reference_public_id(record: object) -> str:
    return cast(AltermagnetReferenceRecord, record).public_id


def _material_projections() -> dict[str, StoredPropertyProjection]:
    projections = {
        name: _nested_structure_projection(projection) for name, projection in unitcell_structure_properties().items()
    }
    direct: dict[str, tuple[str, bool]] = {
        "_anyterial_formula": ("formula", True),
        "_anyterial_space_group": ("space_group", True),
        "_anyterial_space_group_search": ("space_group_search", False),
        "_anyterial_classification": ("classification", True),
        "_anyterial_search_text": ("search_text", False),
        "_anyterial_max_spin_splitting": ("max_ss", True),
        "_anyterial_avg_spin_splitting": ("avg_ss", True),
        "_httk_dft_band_gap": ("bandgap", True),
        "_anyterial_electronic_type": ("electronic_type", False),
        "_anyterial_min_crustal_abundance": ("min_abund_ppm", True),
        # Canonical ids are unpadded, so a lexicographic id sort no longer
        # equals screening order; the site's default sort uses this rank.
        "_anyterial_screening_rank": ("screening_rank", True),
    }
    for name, (column, sortable) in direct.items():
        projections[name] = StoredPropertyProjection(
            response=_provider_response(name),
            query=_scalar_query(column),
            sort=_field_sort(column) if sortable else None,
        )
    for name, column in {
        "_anyterial_elements": "elements",
        "_anyterial_magnetic_phases": "magnetic_phases",
        "_anyterial_wave_classes": "wave_classes",
        "_anyterial_parent_spacegroups": "parent_spacegroups",
        "_anyterial_icsd_ids": "icsd_ids",
    }.items():
        projections[name] = StoredPropertyProjection(
            response=_provider_response(name),
            query=_list_query(column),
        )
    projections["_anyterial_spin_splitting_fraction"] = StoredPropertyProjection(
        response=_provider_response("_anyterial_spin_splitting_fraction"),
        query=_scalar_query("fdelta_pct", literal_transform=_fraction_literal_to_percent),
        sort=_field_sort("fdelta_pct"),
    )
    projections["_anyterial_magnetic_phase"] = StoredPropertyProjection(
        response=_provider_response("_anyterial_magnetic_phase"),
        query=_list_scalar_query(
            "magnetic_phases",
            literals={"altermagnet": "AM", "compensated ferrimagnet": "FiM"},
        ),
    )
    projections["_anyterial_wave_class"] = StoredPropertyProjection(
        response=_provider_response("_anyterial_wave_class"),
        query=_list_scalar_query("wave_classes"),
    )
    for name in (
        "_anyterial_magndata_variants",
        "_httk_custom_figures",
        "_httk_custom_total_energy",
        "_httk_magnetic_space_group_bns",
    ):
        projections[name] = StoredPropertyProjection(response=_provider_response(name))
    # The served list is sorted via its scalar mirror stored column (see
    # MaterialRecord.magndata_sort_key); there is no queryable list column here.
    projections["_httk_magndata_ids"] = StoredPropertyProjection(
        response=_provider_response("_httk_magndata_ids"),
        sort=_field_sort("magndata_sort_key"),
    )
    projections["_httk_custom_public_id"] = StoredPropertyProjection(
        response=_material_public_id,
        query=_scalar_query("id"),
        sort=_field_sort("id"),
    )
    projections["_httk_custom_reference_ids"] = StoredPropertyProjection(response=_material_reference_ids)
    return projections


cast(Any, MaterialRecord).__httk_stored_properties__ = _material_projections()
cast(Any, AltermagnetReferenceRecord).__httk_stored_properties__ = {
    "doi": StoredPropertyProjection(
        response=_reference_doi,
        query=_scalar_query("doi"),
        sort=_field_sort("doi"),
    ),
    "_httk_custom_public_id": StoredPropertyProjection(
        response=_reference_public_id,
        query=_scalar_query("public_id"),
        sort=_field_sort("public_id"),
    ),
}

register_entry_family(
    name="altermagnets-structures",
    family=f"{__name__}:AltermagnetStructureEntry",
    definition_id=AltermagnetStructureEntry.definition_id,
)
register_entry_record(
    name="altermagnets-structure",
    family="altermagnets-structures",
    record=f"{__name__}:MaterialRecord",
)
register_entry_family(
    name="altermagnets-references",
    family=f"{__name__}:AltermagnetReferenceEntry",
    definition_id=AltermagnetReferenceEntry.definition_id,
)
register_entry_record(
    name="altermagnets-reference",
    family="altermagnets-references",
    record=f"{__name__}:AltermagnetReferenceRecord",
)


@dataclass(frozen=True)
class OpenedMaterialStore:
    """The explicitly owned runtime database/store pair and its material count."""

    database: Backend
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


def default_runs_dir() -> Path:
    """The conventional imported httk v1 result tree.

    The whole ten-project tree is the boundary: ``collect_finished_tree`` walks
    every ``ht.task.*`` directory beneath it, so the authoritative AMDB-id ↔
    run-path mapping can resolve runs in any project, not just project ``1``.
    """
    return Path(__file__).resolve().parents[2] / "data" / "raw_httk_v1"


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


def resolve_runs_dir(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the imported v1 result tree."""
    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get(RUNS_PATH_ENVIRONMENT, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return default_runs_dir()


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
    number = str(int(digits))
    aliases = (
        cleaned,
        f"anyt.am-{series}-{number}",
        # Legacy spellings, in each digit form (as given, 4-padded, unpadded),
        # so unpadded canonical ids still resolve padded on-disk shard names.
        *(
            f"{prefix}-{series}-{form}"
            for form in dict.fromkeys((digits, number.zfill(4), number))
            for prefix in ("anyt:am", "am", "anyt:amdb", "amdb")
        ),
    )
    return tuple(dict.fromkeys(aliases))


def details_dir_for_material(details_root: Path, material_id: str) -> Path | None:
    """Resolve the existing canonical/legacy detail shard for one material."""

    parsed = _parsed_material_id(material_id)
    if parsed is None:
        return None
    series, digits = parsed
    padded_digits = str(int(digits)).zfill(4)
    shard_roots = (
        details_root / f"am-{series}" / padded_digits[:1] / padded_digits[:2] / padded_digits[:3],
        details_root / f"amdb-{series}" / padded_digits[:1] / padded_digits[:2] / padded_digits[:3],
    )
    candidates = tuple(shard_root / alias for shard_root in shard_roots for alias in material_id_aliases(material_id))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return candidates[0] if candidates else None


def details_raw_path(details_root: Path, material_id: str) -> str:
    """Return the curated v1 run path recorded in a material's detail shard.

    :param details_root: root of the generated detail-asset tree.
    :param material_id: canonical or legacy AMDB identifier.
    :returns: the ``raw_path`` string (runs-root-relative POSIX path) written in
        the shard's ``<name>.json``, or ``""`` when the shard, its JSON file, the
        ``raw_path`` key, or a readable string value is absent. Never raises on a
        malformed details tree.
    """
    details_dir = details_dir_for_material(details_root, material_id)
    if details_dir is None or not details_dir.is_dir():
        # No shard is the normal "no details for this material" case, not a defect.
        logger.debug("No details shard for %s under %s", material_id, details_root)
        return ""
    document = details_dir / f"{details_dir.name}.json"
    # A present-but-unusable shard silently reverting to formula guessing is a
    # defect worth surfacing, so it warns while still honouring the never-raises
    # contract (the caller falls back to name matching either way).
    try:
        payload = json.loads(document.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("Details raw_path for %s unreadable (%s): %s", material_id, document, error)
        return ""
    raw_path = payload.get("raw_path") if isinstance(payload, dict) else None
    if not isinstance(raw_path, str):
        logger.warning("Details raw_path for %s malformed or missing in %s", material_id, document)
        return ""
    return raw_path.strip()


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
        structure = load(str(contcar), precision=RELAXED_STRUCTURE_PRECISION)
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

    # This site holds a record by construction; the kind hint selects the record
    # backend directly instead of probing the raw-input backends.
    return None if record.structure is None else UnitcellStructureView(record.structure, kind="record")


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
    return f"anyt.am-{AMDB_DATASET}-{index}"


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


def _load_source_materials(
    data_dir: Path, *, details_dir: Path, legacy_structures: bool = True
) -> tuple[MaterialRecord, ...]:
    screening_path, collinear_path, noncollinear_path = _source_table_paths(data_dir)
    return build_material_records(
        _load_csv_rows(screening_path, delimiter=";"),
        _load_csv_rows(collinear_path),
        _load_csv_rows(noncollinear_path),
        details_dir=details_dir,
        load_details_structures=legacy_structures,
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
    load_details_structures: bool = True,
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
        loaded_structure = (
            None
            if details_dir is None or not load_details_structures
            else load_material_structure(details_dir, material_id)
        )
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
                magndata_links=links,
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
    if load_details_structures and details_dir is not None and materials and with_structures == 0:
        logger.warning(
            "No material got a structure: check that %s holds the detail shard tree "
            "and that the CONTCAR reader is importable (httk-atomistic)",
            details_dir,
        )
    return tuple(materials)


@dataclass(frozen=True)
class _RunObservation:
    material: str
    run_id: str
    structure_id: str
    structure: Any
    raw_path: str
    item: Any


def _run_observations(items: Iterable[Any]) -> tuple[_RunObservation, ...]:
    observations: list[_RunObservation] = []
    from httk.workflow.compat.v1.reader import parse_v1_task_name

    for item in items:
        if getattr(item, "missing_collector", None) is not None:
            continue
        outputs = getattr(item, "outputs", {})
        relaxed = outputs.get("relaxed_structure") if isinstance(outputs, Mapping) else None
        run = getattr(item, "run", None)
        if relaxed is None or run is None:
            continue
        parsed = parse_v1_task_name(item.record.payload_path.name)
        task_id = item.record.payload_path.name if parsed is None else parsed["task_id"]
        observations.append(
            _RunObservation(
                task_id.removesuffix("_SCF"),
                # Collected runs have never passed a store, so Run.id is the
                # store-minted field (None here); pin the content identity.
                content_id(run),
                relaxed.id,
                relaxed,
                str(item.record.payload_path),
                item,
            )
        )
    return tuple(sorted(observations, key=lambda item: (item.material, item.run_id)))


def _coupling_row(
    amdb_id: str,
    run_material: str,
    *,
    raw_path: str = "",
    structure_id: str = "",
    run_id: str = "",
    status: str,
) -> dict[str, str]:
    return {
        "AMDBId": amdb_id,
        "run_material": run_material,
        "raw_path": raw_path,
        "structure_content_id": structure_id,
        "run_content_id": run_id,
        "status": status,
    }


def _write_coupling(path: Path, rows: Iterable[Mapping[str, str]]) -> None:
    fields = ("AMDBId", "run_material", "raw_path", "structure_content_id", "run_content_id", "status")
    rows = list(rows)
    keys: set[tuple[str, str]] = set()
    for row in rows:
        if set(row) != set(fields):
            raise ValueError(f"{path}: invalid coupling row columns")
        key = (row["AMDBId"], row["run_material"])
        if key in keys:
            raise ValueError(f"{path}: duplicate coupling row {key[0]}/{key[1]}")
        keys.add(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";", lineterminator="\n")
            writer.writeheader()
            writer.writerows(cast(Any, rows))
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _is_run_material(material: str, csv_material: str) -> bool:
    return material == csv_material or re.fullmatch(rf"{re.escape(csv_material)}-\d+", material) is not None


def _build_coupling(
    data_dir: Path,
    observations: tuple[_RunObservation, ...],
    *,
    details_dir: Path,
    runs_root: Path | None = None,
    collected: int = 0,
    refresh_coupling: bool = False,
) -> tuple[dict[str, _RunObservation], dict[str, int]]:
    """Build and verify the deterministic AMDB-to-v1 coupling document.

    :param data_dir: directory holding the screening CSV and the coupling document.
    :param observations: relaxed-structure observations collected from the v1 tree.
    :param details_dir: root of the detail-asset tree, whose per-material JSONs
        carry the authoritative ``raw_path`` mapping.
    :param runs_root: the runs root the observations were collected from, named in
        the diagnostic raised when a document's ``raw_path`` values all miss.
    :param collected: total tasks collected from the tree (before 3a filtering), so a
        wrong runs root (many collected, none usable) is told apart from an empty tree.
    :param refresh_coupling: rewrite derived content-ids (and fill/promote from the
        details ``raw_path``) instead of raising on a stale pin.
    :returns: the ``AMDBId``-keyed coupled observations and the status counts.
    """
    screening = _load_csv_rows(data_dir / SCREENING_RESULTS_FILENAME, delimiter=";")
    source_materials: dict[str, str] = {}
    materials_to_amdb: dict[str, list[str]] = {}
    for source_row in screening:
        amdb_id = (source_row.get(AMDB_ID_COLUMN) or "").strip()
        material = (source_row.get("Material") or "").strip()
        if not amdb_id or not material:
            raise ValueError(f"{data_dir / SCREENING_RESULTS_FILENAME}: missing AMDBId or Material")
        if amdb_id in source_materials:
            raise ValueError(f"duplicate canonical material ID '{amdb_id}'")
        source_materials[amdb_id] = material
        materials_to_amdb.setdefault(material, []).append(amdb_id)
    coupling_path = data_dir / COUPLING_FILENAME
    previous = _load_csv_rows(coupling_path, delimiter=";") if coupling_path.is_file() else []
    # raw_path is optional in older documents; every other column is fixed. Tolerate
    # a missing raw_path column, but reject unexpected extra columns so a curator's
    # hand-added column is not silently discarded on the next rewrite.
    required = {"AMDBId", "run_material", "structure_content_id", "run_content_id", "status"}
    allowed = required | {"raw_path"}
    for row in previous:
        missing = required - set(row)
        if missing:
            raise ValueError(f"{coupling_path}: missing columns {sorted(missing)!r}")
        extra = set(row) - allowed - {None}
        if extra:
            raise ValueError(f"{coupling_path}: unexpected columns {sorted(extra)!r}")

    # The authoritative AMDB-id -> run path mapping from the details tree, read once.
    details_paths = {amdb_id: details_raw_path(details_dir, amdb_id) for amdb_id in source_materials}
    # Runs authoritatively owned by some material's details JSON. A name match must
    # never steal one of these, including on the refresh path before pass 1 runs.
    claimed = {path for path in details_paths.values() if path}

    by_material: dict[str, list[_RunObservation]] = {}
    by_raw_path: dict[str, _RunObservation] = {}
    for obs in observations:
        by_material.setdefault(obs.material, []).append(obs)
        by_raw_path[obs.raw_path] = obs
    rows: dict[tuple[str, str], dict[str, str]] = {}
    coupled: dict[str, _RunObservation] = {}

    statuses = {"auto", "ambiguous", "curated"}
    row_keys: set[tuple[str, str]] = set()
    raw_path_rows = 0
    resolved_raw_path_rows = 0
    for row in previous:
        amdb_id = row[AMDB_ID_COLUMN].strip()
        material = row["run_material"].strip()
        status = row["status"].strip()
        raw_path_field = (row.get("raw_path") or "").strip()
        structure_id = row["structure_content_id"].strip()
        run_id = row["run_content_id"].strip()
        key = (amdb_id, material)
        if amdb_id not in source_materials:
            raise ValueError(f"coupling row {amdb_id}/{material}: AMDBId is absent from the CSV")
        if not _is_run_material(material, source_materials[amdb_id]):
            # A raw_path row is authoritative and one legitimately disagrees with
            # the CSV formula (e.g. Cu2H3ClO3 vs the Cu2O3Cl run); only name-only
            # rows treat a mismatch as a hard error.
            message = f"coupling row {amdb_id}/{material}: material does not match the CSV row"
            if raw_path_field:
                logger.warning("%s (authoritative raw_path)", message)
            else:
                raise ValueError(message)
        if status not in statuses:
            raise ValueError(f"coupling row {amdb_id}/{material}: invalid status {status!r}")
        if key in row_keys:
            raise ValueError(f"duplicate coupling row {amdb_id}/{material}")
        row_keys.add(key)
        if not refresh_coupling and status == "ambiguous" and (structure_id or run_id):
            raise ValueError(f"coupling row {amdb_id}/{material}: ambiguous rows must have empty content-ids")
        # A name-only active row has nothing to resolve against, so it must carry ids
        # in both modes. A row with a raw_path may omit them: when the run is present
        # the resolve logic fills (refresh) or verifies (plain) the ids, and when it
        # is absent the row is a legal pending curation whose status is kept intact.
        if status in {"auto", "curated"} and not (structure_id and run_id) and not raw_path_field:
            raise ValueError(f"coupling row {amdb_id}/{material}: active rows require content-ids")

        if raw_path_field:
            raw_path_rows += 1
            # Rule 1/3: a raw_path row is matched only to that exact run path.
            observation = by_raw_path.get(raw_path_field)
            if observation is None:
                # A transferred partial tree may not contain this run yet.
                logger.warning("Coupled run for %s not collected in this build: %s", amdb_id, raw_path_field)
                rows[key] = _coupling_row(
                    amdb_id, material, raw_path=raw_path_field, structure_id=structure_id, run_id=run_id, status=status
                )
            else:
                resolved_raw_path_rows += 1
                if refresh_coupling:
                    new_status = "auto" if status == "ambiguous" else status
                    rows[key] = _coupling_row(
                        amdb_id,
                        material,
                        raw_path=raw_path_field,
                        structure_id=observation.structure_id,
                        run_id=observation.run_id,
                        status=new_status,
                    )
                    if new_status in {"auto", "curated"}:
                        coupled[amdb_id] = observation
                elif status == "ambiguous":
                    rows[key] = _coupling_row(amdb_id, material, raw_path=raw_path_field, status="ambiguous")
                else:
                    if (structure_id, run_id) != (observation.structure_id, observation.run_id):
                        raise ValueError(f"Coupling row {amdb_id}/{material} does not match ingested content-ids")
                    rows[key] = _coupling_row(
                        amdb_id,
                        material,
                        raw_path=raw_path_field,
                        structure_id=observation.structure_id,
                        run_id=observation.run_id,
                        status=status,
                    )
                    coupled[amdb_id] = observation
        elif refresh_coupling:
            # Rule E: fill an empty raw_path from the details tree, else name match
            # over runs no other material's details JSON authoritatively owns.
            eff = details_paths.get(amdb_id, "")
            observation = by_raw_path.get(eff) if eff else None
            if observation is None:
                actuals = by_material.get(material, ())
                candidate = actuals[0] if len(actuals) == 1 else None
                if (
                    candidate is not None
                    and len(materials_to_amdb.get(material, ())) == 1
                    and (candidate.raw_path not in claimed or candidate.raw_path == eff)
                ):
                    observation = candidate
                    eff = candidate.raw_path
            if observation is None:
                rows[key] = _coupling_row(amdb_id, material, structure_id=structure_id, run_id=run_id, status=status)
            else:
                new_status = "auto" if status == "ambiguous" else status
                rows[key] = _coupling_row(
                    amdb_id,
                    material,
                    raw_path=eff,
                    structure_id=observation.structure_id,
                    run_id=observation.run_id,
                    status=new_status,
                )
                if new_status in {"auto", "curated"}:
                    coupled[amdb_id] = observation
        else:
            # Rule 2: name-only rows keep the historic matching behavior exactly.
            rows[key] = _coupling_row(amdb_id, material, structure_id=structure_id, run_id=run_id, status=status)
            actuals = by_material.get(material, ())
            if not actuals or status == "ambiguous":
                continue
            if len(actuals) != 1:
                raise ValueError(f"coupling row {amdb_id}/{material}: run material is not unique in this build")
            actual = actuals[0]
            if (structure_id, run_id) != (actual.structure_id, actual.run_id):
                raise ValueError(f"Coupling row {amdb_id}/{material} does not match ingested content-ids")
            if status in {"auto", "curated"}:
                coupled[amdb_id] = actual

    # A run backs exactly one material. Two previous rows resolving to one run would
    # each publish that run's content ids as their own provenance; catch it here,
    # since _write_coupling dedups on (AMDBId, run_material) and active_ids per id.
    path_to_ids: dict[str, list[str]] = {}
    for aid, obs in coupled.items():
        path_to_ids.setdefault(obs.raw_path, []).append(aid)
    shared = {path: ids for path, ids in path_to_ids.items() if len(ids) > 1}
    if shared:
        path, ids = min(shared.items())
        raise ValueError(
            f"coupling document couples one run to multiple materials: raw_path {path} shared by {', '.join(sorted(ids))}"
        )

    # A misconfigured runs root makes every raw_path miss. Discriminate on tasks
    # COLLECTED (before 3a filtering), not usable observations: a wrong root such as
    # <root>/1/Runs collects tasks whose one-part payloads are all rejected by 3a,
    # leaving observations empty but collected > 0. An absent or empty tree collects
    # nothing and stays silent; a wrong root collects yet resolves none, and raises.
    if collected and raw_path_rows and resolved_raw_path_rows == 0:
        raise ValueError(
            f"no coupling raw_path resolved against the runs collected from {runs_root}; "
            "check --runs-dir / ALTERMAGNETS_RUNS_DIR points at the raw_httk_v1 root"
        )

    # Authoritative claims (previous rows) are already in coupled; passes 1 and 2
    # extend this set so name matching never reuses an already-coupled run.
    coupled_paths = set(path_to_ids)

    # Rule 4, pass 1: authoritative details raw_paths for AMDB ids with no prior row.
    for amdb_id, material in sorted(source_materials.items()):
        if any(existing[0] == amdb_id for existing in rows):
            continue
        details_path = details_paths.get(amdb_id, "")
        if not details_path:
            continue
        observation = by_raw_path.get(details_path)
        if observation is None:
            logger.warning("Details run for %s not collected in this build: %s", amdb_id, details_path)
            continue
        if observation.raw_path in coupled_paths:
            logger.warning("Details run for %s already coupled elsewhere: %s", amdb_id, details_path)
            continue
        rows[(amdb_id, observation.material)] = _coupling_row(
            amdb_id,
            observation.material,
            raw_path=details_path,
            structure_id=observation.structure_id,
            run_id=observation.run_id,
            status="auto",
        )
        coupled[amdb_id] = observation
        coupled_paths.add(observation.raw_path)

    # Rule 4, pass 2: name matching for materials without a details raw_path, over
    # only the runs not already claimed by an authoritative coupling.
    for amdb_id, material in sorted(source_materials.items()):
        if any(existing[0] == amdb_id for existing in rows):
            continue
        exact = [item for item in by_material.get(material, []) if item.raw_path not in coupled_paths]
        variants = [
            item
            for item in observations
            if _is_run_material(item.material, material)
            and item.material != material
            and item.raw_path not in coupled_paths
        ]
        candidates = exact + variants
        if len(exact) == 1 and not variants and len(materials_to_amdb[material]) == 1:
            observation = exact[0]
            rows[(amdb_id, material)] = _coupling_row(
                amdb_id,
                material,
                structure_id=observation.structure_id,
                run_id=observation.run_id,
                status="auto",
            )
            coupled[amdb_id] = observation
            coupled_paths.add(observation.raw_path)
        elif candidates:
            logger.warning("Ambiguous run match for %s (%s)", amdb_id, material)
            run_material = material if exact else min(item.material for item in variants)
            rows[(amdb_id, run_material)] = _coupling_row(amdb_id, run_material, status="ambiguous")
        else:
            logger.warning("No ingested run for CSV material %s (%s)", amdb_id, material)

    # Enforce one active row per AMDB id. A pending curated row (raw_path, empty ids,
    # run not yet present) is left exactly as the curator wrote it: its status is
    # never rewritten, so a human's decision cannot be silently erased by a refresh.
    active_ids: set[str] = set()
    for row in rows.values():
        if row["status"] not in {"auto", "curated"}:
            continue
        if row[AMDB_ID_COLUMN] in active_ids:
            raise ValueError(
                f"coupling row {row[AMDB_ID_COLUMN]}/{row['run_material']}: multiple active rows for AMDBId"
            )
        active_ids.add(row[AMDB_ID_COLUMN])

    # Runs collected that belong to no CSV material (suffix variants included).
    csv_materials = set(materials_to_amdb)
    orphans = sum(
        1
        for obs in observations
        if obs.material not in csv_materials and re.sub(r"-\d+$", "", obs.material) not in csv_materials
    )
    if orphans:
        logger.info("%d ingested runs have no CSV material row", orphans)

    ordered = sorted(rows.values(), key=lambda row: (row[AMDB_ID_COLUMN], row["run_material"]))
    _write_coupling(coupling_path, ordered)
    counts: dict[str, int] = {}
    for row in ordered:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    logger.info("Wrote %s coupling rows: %s", coupling_path, counts)
    return coupled, counts


def _entry_records_layout() -> dict[type, type | tuple[type, ...]]:
    """Mirror workflow ``--into`` with the altermagnets structure families."""
    return {
        AltermagnetStructureEntry: MaterialRecord,
        AltermagnetReferenceEntry: AltermagnetReferenceRecord,
        RunEntry: Run,
        DataRecordEntry: DataRecord,
        FileEntry: FileRecord,
    }


#: spglib magnetic-symmetry tolerance (Å), matched to the relaxed-DFT coordinate
#: noise like :data:`RELAXED_STRUCTURE_PRECISION` rather than machine epsilon. It
#: is reused as the fractional-coordinate dedup tolerance when folding onto the
#: primitive cell, and as the µB threshold for the collinear (zero x/y) check.
_MAGNETIC_SYMPREC = 1e-3


def _magnetic_alternative_cell(structure: UnitcellStructure, kind: str) -> UnitcellStructure:
    """Derive a magnetic conventional/primitive cell via spglib's MSG dataset.

    httk-atomistic's exact :func:`conventional_cell`/:func:`primitive_cell` use
    nuclear symmetry only and refuse when the magnetic order breaks a nuclear
    translation. This fallback standardizes on the *magnetic* space group via
    ``spglib.get_magnetic_symmetry_dataset`` and returns the result as a plain
    :class:`~httk.atomistic.UnitcellStructure` under the same ``kind`` --
    including when the cell is identical to the input.

    The structure must carry collinear VASP moments as
    :class:`~httk.atomistic.CartesianSiteMoments` with zero x/y on every site
    (the loader's ``(0, 0, m)`` contract); the scalar z components are spglib's
    magmoms. ``"conventional"`` uses the dataset's magnetic standardized cell
    directly; ``"primitive"`` folds that cell onto ``primitive_lattice``.

    :param structure: The moment-carrying structure to standardize.
    :param kind: ``"conventional"`` or ``"primitive"``.
    :return: The magnetic conventional or primitive cell.
    :raises ValueError: If the moments are not collinear along z, spglib returns
        no dataset, or a primitive fold yields inconsistent moments/site counts.
    :raises ImportError: If spglib is not installed.
    """
    import numpy as np
    import spglib

    moments = structure.site_moments
    if not isinstance(moments, CartesianSiteMoments):
        raise ValueError("magnetic alternative cell requires CartesianSiteMoments")
    cartesian = moments.cartesian_moments.to_floats()
    if any(abs(row[0]) > _MAGNETIC_SYMPREC or abs(row[1]) > _MAGNETIC_SYMPREC for row in cartesian):
        raise ValueError("magnetic alternative cell requires collinear moments along z (zero x/y components)")
    magmoms = [float(row[2]) for row in cartesian]

    # Mirror httk-atomistic's own spglib bridge (recognition._find_symmetry).
    names = sorted(set(structure.species_at_sites))
    lattice = structure.cell.basis.to_floats()
    positions = structure.sites.reduced_coords.to_floats()
    numbers = [names.index(name) + 1 for name in structure.species_at_sites]

    dataset = spglib.get_magnetic_symmetry_dataset((lattice, positions, numbers, magmoms), symprec=_MAGNETIC_SYMPREC)
    if dataset is None:
        raise ValueError(f"spglib found no magnetic symmetry dataset at symprec={_MAGNETIC_SYMPREC}")

    std_lattice = np.asarray(dataset.std_lattice, dtype=float)
    std_positions = np.asarray(dataset.std_positions, dtype=float)
    std_types = [int(number) for number in dataset.std_types]
    std_tensors = [float(tensor) for tensor in np.ravel(np.asarray(dataset.std_tensors, dtype=float))]
    if len(std_tensors) != len(std_positions):
        raise ValueError("magnetic std_tensors are not collinear scalars")
    # std_rotation_matrix is deliberately not applied: a collinear (0, 0, m) moment is a
    # frame-free scalar convention, so the standardized scalar tensors carry through as-is,
    # matching httk's own collinear-moment carry-through.

    # Snap 6 orders below the dedup tolerance, then wrap into [0, 1): spglib's std_positions
    # can be a hair negative (e.g. -1.6e-32) and a raw ``% 1.0`` fold can emit exactly 1.0.
    if kind == "conventional":
        cell_rows = std_lattice.tolist()
        coords = (np.round(std_positions, 9) % 1.0).tolist()
        types = std_types
        tensors = std_tensors
    elif kind == "primitive":
        prim_lattice = np.asarray(dataset.primitive_lattice, dtype=float)
        cell_rows = prim_lattice.tolist()
        # spglib's row-vector convention: cart = frac @ lattice, so frac_prim = cart @ inv(prim_lattice).
        fractional = np.round((std_positions @ std_lattice) @ np.linalg.inv(prim_lattice), 9) % 1.0
        coords = []
        types = []
        tensors = []
        for point, number, tensor in zip(fractional.tolist(), std_types, std_tensors):
            duplicate = False
            for index, kept in enumerate(coords):
                delta = [(point[axis] - kept[axis] + 0.5) % 1.0 - 0.5 for axis in range(3)]
                if types[index] == number and all(abs(component) < _MAGNETIC_SYMPREC for component in delta):
                    if not math.isclose(tensors[index], tensor, rel_tol=1e-3, abs_tol=1e-3):
                        raise ValueError("magnetic primitive fold maps sites with different moments together")
                    duplicate = True
                    break
            if not duplicate:
                coords.append(point)
                types.append(number)
                tensors.append(tensor)
        expected = round(len(std_positions) * abs(np.linalg.det(prim_lattice)) / abs(np.linalg.det(std_lattice)))
        if len(coords) != expected:
            raise ValueError(f"magnetic primitive fold gave {len(coords)} sites, expected {expected}")
    else:
        raise ValueError(f"unknown magnetic alternative kind: {kind!r}")

    species_at_sites = [names[number - 1] for number in types]
    return UnitcellStructure(
        Cell(cell_rows, precision=RELAXED_STRUCTURE_PRECISION),
        Sites(coords, precision=RELAXED_STRUCTURE_PRECISION),
        structure.species,
        species_at_sites,
        site_moments=CartesianSiteMoments([[0.0, 0.0, tensor] for tensor in tensors]),
    )


#: The derived-cell alternatives stored beside each main material record.
_ALTERNATIVE_CELLS: tuple[tuple[str, Callable[[UnitcellStructure], Any]], ...] = (
    ("conventional", conventional_cell),
    ("primitive", primitive_cell),
)


def _save_alternative_cells(store: SqlStore, materials: Iterable[MaterialRecord]) -> tuple[int, int]:
    """Derive and store the conventional/primitive alternatives of each material.

    Bulk ingest is mains-only, so alternatives are saved with ordinary
    :meth:`SqlStore.save` after the bulk context finalizes: each shares its
    main's public id, carries the same scalar metadata, and gets its own
    lineage under an immutable ``<id>~<kind>~<n>``. The exact nuclear-symmetry
    derivation raises :class:`ValueError` for the whole moment-related refusal
    family -- both a magnetic supercell (the magnetic order breaks a nuclear
    translation) and a site-moment correspondence failure. The fallback catches
    that family as a whole (not one specific message): whenever the structure
    carries site moments, the kind is instead derived from spglib's magnetic
    (MSG) symmetry dataset via :func:`_magnetic_alternative_cell` and stored
    under the same kind -- including when it equals the input cell. The skip
    warning fires only when that magnetic fallback also fails, or when the
    structure has no moments to fall back on.

    :return: The ``(derived, skipped)`` counts across every material and kind.
    """
    derived = skipped = 0
    for material in materials:
        structure = material_structure(material)
        if structure is None:
            continue
        assert material.id is not None  # always set to the amdb id at construction
        for kind, derive in _ALTERNATIVE_CELLS:
            derived_from_magnetic = False
            try:
                cell = derive(structure).structure
            except ValueError as error:
                if structure.site_moments is None:
                    skipped += 1
                    logger.warning("Skipping %s alternative for %s: %s", kind, material.id, error)
                    continue
                try:
                    cell = _magnetic_alternative_cell(structure, kind)
                except (ValueError, ImportError) as fallback_error:
                    skipped += 1
                    logger.warning(
                        "Skipping %s alternative for %s: %s (magnetic-symmetry fallback failed: %s)",
                        kind,
                        material.id,
                        error,
                        fallback_error,
                    )
                    continue
                derived_from_magnetic = True
            store.save(
                replace(material, structure=_material_structure_record(cell)),
                alternative_of=material.id,
                alternative_kind=kind,
            )
            derived += 1
            if derived_from_magnetic:
                logger.info(
                    "Derived %s alternative for %s from magnetic symmetry (spglib MSG dataset)",
                    kind,
                    material.id,
                )
    logger.info("Stored %d alternative cell records, skipped %d derivations", derived, skipped)
    return derived, skipped


def _coupled_total_energy(observation: _RunObservation) -> float | None:
    """The coupled run's ``total_energy`` DataRecord value, served as a scalar."""
    outputs = getattr(observation.item, "outputs", {})
    energy = outputs.get("total_energy") if isinstance(outputs, Mapping) else None
    value = getattr(energy, "value", None)
    return float(value) if isinstance(value, (int, float)) else None


def _link_material_runs(
    store: SqlStore, materials: Iterable[MaterialRecord], coupled: Mapping[str, _RunObservation]
) -> int:
    """Assert the ``produced_by`` weak link from each coupled material to its run.

    Links are refused inside a bulk-ingest context, so this runs after the bulk
    stream finalizes (beside :func:`_save_alternative_cells`). Only runs whose
    item was actually saved (a non-``missing_collector`` collection) are linked,
    and one run backs one material (enforced in :func:`_build_coupling`), so this
    asserts exactly one link per coupled material.

    :return: The number of ``(material, run)`` links asserted.
    """
    by_id = {material.id: material for material in materials}
    linked = 0
    for amdb_id, observation in coupled.items():
        material = by_id.get(amdb_id)
        run = getattr(observation.item, "run", None)
        if material is None or run is None or getattr(observation.item, "missing_collector", None) is not None:
            continue
        store.link(material, "produced_by", run)
        linked += 1
    logger.info("Linked %d coupled materials to their producing runs", linked)
    return linked


def build_store(
    target: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    details_dir: str | os.PathLike[str] | None = None,
    runs_dir: str | os.PathLike[str] | None = None,
    legacy: bool = False,
    refresh_coupling: bool = False,
    timings: MutableMapping[str, float] | None = None,
) -> Path:
    """Build a fresh store next to ``target`` and atomically replace it.

    The caller never sees a partially written target: the DuckDB connection is
    disposed before :func:`os.replace` commits the completed temporary file.

    When ``timings`` is supplied it is populated with the wall-clock seconds of
    the ``load`` (source parsing), ``write`` (the bulk-ingest context) and
    ``finalize`` (dispose plus atomic replace) phases, and the ``total``.
    """
    total_started = time.perf_counter()
    resolved_target = resolve_store_path(target)
    source_dir = resolve_data_dir(data_dir)
    resolved_details_dir = resolve_details_dir(details_dir)
    resolved_runs_dir = resolve_runs_dir(runs_dir)
    load_started = time.perf_counter()
    items: list[Any] = []
    observations: tuple[_RunObservation, ...] = ()
    coupled: dict[str, _RunObservation] = {}
    coupling_counts: dict[str, int] = {}
    if not legacy:
        from httk.workflow.compat.v1 import collect_finished_tree

        if resolved_runs_dir.is_dir():
            items = list(
                collect_finished_tree(
                    resolved_runs_dir,
                    workflow_dir=Path(__file__).resolve().parents[2] / "workflows" / "relax_and_scf_httk_v1",
                )
            )
        else:
            logger.warning("No v1 runs directory at %s; building the partial tabular store", resolved_runs_dir)
        observations = _run_observations(items)
        coupled, coupling_counts = _build_coupling(
            source_dir,
            observations,
            details_dir=resolved_details_dir,
            runs_root=resolved_runs_dir,
            collected=len(items),
            refresh_coupling=refresh_coupling,
        )
    materials = _load_source_materials(
        source_dir,
        details_dir=resolved_details_dir,
        legacy_structures=legacy,
    )
    if coupled:
        materials = tuple(
            replace(
                material,
                structure=_material_structure_record(coupled[material.id].structure.unwrap()),
                # Symmetric scalar beside the live _httk_runs weak link asserted
                # after the bulk context finalizes (_link_material_runs).
                total_energy=_coupled_total_energy(coupled[material.id]),
            )
            if material.id in coupled
            else material
            for material in materials
        )
    if not legacy:
        # Safety net: the non-legacy build must be at least as complete as the
        # legacy build for BOTH structure presence and site moments. A coupled run
        # whose OUTCAR magnetization is degraded yields a moment-free structure;
        # fall back to the details CONTCAR+MAGN, which the legacy build trusts.
        no_structure = 0
        lost_moments = 0

        def _with_details_fallback(material: MaterialRecord) -> MaterialRecord:
            nonlocal no_structure, lost_moments
            current = material.structure
            if current is not None and current.site_moments is not None:
                return material
            assert material.id is not None  # always set to the amdb id at construction
            details_structure = load_material_structure(resolved_details_dir, material.id)
            if details_structure is None:
                return material
            if current is None:
                no_structure += 1
                logger.debug("Details CONTCAR fallback (no structure) for %s", material.id)
                return replace(material, structure=_material_structure_record(details_structure))
            if details_structure.site_moments is None:
                # The details build lacks moments too; nothing to recover.
                return material
            lost_moments += 1
            logger.debug("Details CONTCAR fallback (missing moments) for %s", material.id)
            return replace(material, structure=_material_structure_record(details_structure))

        materials = tuple(_with_details_fallback(material) for material in materials)
        if no_structure:
            logger.info("Applied details CONTCAR fallback for %d materials without a structure", no_structure)
        if lost_moments:
            logger.warning(
                "Recovered site moments from details for %d coupled runs with degraded OUTCAR magnetization",
                lost_moments,
            )
        with_structures = sum(1 for material in materials if material.structure is not None)
        logger.info(
            "Store contains %d material records, %d with structures (after coupling and fallback)",
            len(materials),
            with_structures,
        )
    load_elapsed = time.perf_counter() - load_started
    logger.info(
        "Collected %d v1 tasks, %d yielded a relaxed structure, %d coupled to materials, coupling rows %s",
        len(items),
        len(observations),
        len(coupled),
        coupling_counts,
    )

    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{resolved_target.name}.",
        suffix=".tmp",
        dir=resolved_target.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    temporary_path.unlink()
    database: Backend | None = None
    try:
        created_database = Backend.duckdb(temporary_path)
        database = created_database
        store = SqlStore(
            created_database,
            entry_records={} if legacy else _entry_records_layout(),
            # Mints entry ids for run/data/file rows saved without one; material
            # and reference entries carry their public ids explicitly.
            entry_ids=EntryIdScheme("anyt", "am", type_in_base=True),
        )
        # Bulk ingestion creates the tables index-less and builds the indexes
        # once the stream completes, so no separate ensure_tables/transaction.
        write_started = time.perf_counter()
        bulk_context = store.bulk_ingest() if legacy else store.bulk_ingest(finalize="parity")
        with bulk_context as bulk:
            bulk.save(StoreLayout(STORE_LAYOUT_VERSION))
            if not legacy:
                # Only coupled runs back the 180 served materials; saving the whole
                # 645-task tree would flood the OPTIMADE run/record/file families.
                # One run backs one material (enforced in _build_coupling), so the
                # coupled items are already distinct.
                for item in [observation.item for observation in coupled.values()]:
                    if getattr(item, "missing_collector", None) is None:
                        for value in item.outputs.values():
                            bulk.save(value)
                        bulk.save(item.run)
                        for product in item.products:
                            bulk.save(product)
            for material in materials:
                bulk.save(material)
            for doi in dict.fromkeys(doi for material in materials for doi in material.dois):
                bulk.save(AltermagnetReferenceRecord((rid := _reference_id(doi)), doi, id=rid))
        if not legacy:
            # Alternatives and weak links are asserted after the mains-only bulk
            # context finalizes (both are refused inside bulk ingest).
            _save_alternative_cells(store, materials)
            _link_material_runs(store, materials, coupled)
        write_elapsed = time.perf_counter() - write_started
        finalize_started = time.perf_counter()
        created_database.dispose()
        database = None
        os.replace(temporary_path, resolved_target)
        finalize_elapsed = time.perf_counter() - finalize_started
    except BaseException:
        if database is not None:
            database.dispose()
        temporary_path.unlink(missing_ok=True)
        raise
    if timings is not None:
        timings["load"] = load_elapsed
        timings["write"] = write_elapsed
        timings["finalize"] = finalize_elapsed
        timings["total"] = time.perf_counter() - total_started
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
    database: Backend | None = None
    try:
        if not store_path.is_file():
            logger.info("No prebuilt store at %s", store_path)
            return None
        if store_path.stat().st_size == 0:
            logger.info("Prebuilt store %s is empty; rebuild with `make build_store`", store_path)
            return None
        opened_database = Backend.duckdb(store_path)
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
            logger.info("Prebuilt store %s holds no materials; rebuild with `make build_store`", store_path)
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
    database: Backend | None = None
    try:
        logger.info(
            "Seeding an in-memory store from %s (details: %s) — slow; prefer `make build_store`",
            source_dir,
            resolved_details_dir,
        )
        materials = _load_source_materials(source_dir, details_dir=resolved_details_dir)
        if not materials:
            return None
        opened_database = Backend.sqlite()
        database = opened_database
        store = SqlStore(
            opened_database,
            entry_records=_entry_records_layout(),
            entry_ids=EntryIdScheme("anyt", "am", type_in_base=True),
        )
        # Bulk ingestion creates the tables and their indexes itself.
        with store.bulk_ingest() as bulk:
            for material in materials:
                bulk.save(material)
            for doi in dict.fromkeys(doi for material in materials for doi in material.dois):
                bulk.save(AltermagnetReferenceRecord((rid := _reference_id(doi)), doi, id=rid))
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
    if isinstance(database, Backend):
        database.dispose()
