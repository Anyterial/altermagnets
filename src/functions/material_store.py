"""Stored records and persistent/in-memory loaders for the altermagnets site.

Runtime prefers an offline-built DuckDB file, but can seed the same
``SqlStore`` schema into an in-memory SQLite database from the three source
tables when the persistent store is absent or unusable. The record classes are
deliberately ordinary frozen dataclasses: the schema is declared with
httk-core's storage markers and implemented by ``httk.store.backend.sql.SqlStore``.
"""

import bz2
import csv
import datetime
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
from httk.core import (
    EntryTypeDefinition,
    File,
    FileRecord,
    IdentitySkip,
    Indexed,
    PropertyDefinition,
    Related,
    Skip,
    StorageInfo,
    Unique,
    load,
    register_definition_prefix,
    report,
    standard_entry_type,
)
from httk.core.data_records import RECORDS_DEFINITION_ID
from httk.core.files import FileEntry
from httk.core.project.sealing import SealError, SealKey, resolve_seal_keys
from httk.core.provenance import ProductLink, Run, RunEdge, RunEntry
from httk.core.register import register_entry_family, register_entry_record
from httk.core.register.schemas import load_entry_type_definition
from httk.core.storage import (
    QueryLiteralError,
    StoredPropertyProjection,
    content_id,
    project_storage_record,
    stored_property,
)
from httk.store import Backend, EntryIdScheme, IdLedger, IdLedgerError, SqlStore
from httk.store.backend.schema import SchemaError
from httk.store.backend.sql import StorageLayoutUpgradeRequiredError
from sqlalchemy.exc import SQLAlchemyError

__all__ = [
    "AMDB_DATASET",
    "AMDB_ID_COLUMN",
    "MAGNDATA_COLLINEAR_FILENAME",
    "MAGNDATA_NONCOLLINEAR_FILENAME",
    "SCREENING_RESULTS_FILENAME",
    "STORE_LAYOUT_VERSION",
    "STORE_PATH_ENVIRONMENT",
    "AltermagnetDataRecordEntry",
    "AltermagnetReferenceEntry",
    "AltermagnetReferenceRecord",
    "AltermagnetScreeningResult",
    "AltermagnetScreeningResultEntry",
    "AltermagnetStructureEntry",
    "CalculationOutputRecord",
    "MagndataRecord",
    "MaterialFigure",
    "MaterialMagndataLink",
    "OpenedMaterialStore",
    "PlotFile",
    "ScreeningResultRecord",
    "StoreLayout",
    "SymmetryVariant",
    "build_material_records",
    "build_store",
    "cleanup_material_store",
    "default_data_dir",
    "default_details_dir",
    "default_ledger_path",
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
    "resolve_ledger_path",
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
# Bump 10: the producing run's StrongLink edges replace the retired ``produced_by``
# WeakLink, so provenance is served through the run's reverse ``_httk_is_*`` blocks.
# Removing the link declaration changes the link fingerprint and RunEdge grows a
# ``(entry_type, entry_id)`` index; either forces a rebuild, and the fingerprint --
# not this version row -- is the operative staleness gate for a pre-change store.
# Bump 11: the id scheme flips to ``anyt.am``/series ``1`` (materials ``anyt.am-1-N``,
# minted types ``anyt.am.<type>-1-N``, references densely enumerated ``anyt.am.refs-1-N``),
# ``AltermagnetScreeningResult`` gains the stored ``reference_ids`` serving its references block, and
# the ``_httk_records`` family is served through the extended ``AltermagnetDataRecord``.
# The fingerprint does not see id changes, so this version row is the forcing gate.
# Bump 12: the two-record split. ``AltermagnetScreeningResult`` (served
# ``_anyterial_altermagnet_screening_results``, ids ``anyt.am-1-N``) owns the science and
# references a slim standard ``structures`` main (``UnitcellStructureRecord``, stamped ids
# ``anyt.am.structure-1-N``) that now carries the structural properties, alternatives, and
# the ``output_structure`` provenance edge; the result gains the stored ``structure_id``
# serving its ``structures`` relationship block plus an appended ``has_artifact`` run edge.
# Bump 13: the records/provenance redesign. Two typed record backings
# (``CalculationOutputRecord`` in ``altermagnets_calculation_outputs``, ``ScreeningResultRecord``
# in ``altermagnets_screening_records``) replace the one-value ``DataRecord`` served through
# ``_httk_custom_record_*``; the reconstructed run's ``outputs`` and ``artifacts`` are the same
# paired edge tuple and the appended run->result ``has_artifact`` edge is gone (the result is a
# curated collection entry, not a run product); the result gains the typed ``calculation_output``
# and ``screening_record`` references and loses its ``total_energy`` scalar
# (``_httk_custom_total_energy``). New tables and a dispatch table force the rebuild.
# Bump 14: the AMDB polish batch. The result gains the stamped ``run_id`` column (the
# material's coupled-run ledger id, served ONLY through the private, never-response
# ``_httk_custom_run_id`` projection feeding the adapter's ``_httk_runs`` envelope
# injection); the reconstructed run's ``artifacts`` is now always ``()`` (no
# sub-workflows in this deployment, so artifacts only duplicated ``outputs`` -- restore
# by passing the same rewritten tuple to ``artifacts=``); the former relaxed-structure
# output/edge label is renamed ``output_structure`` (the collecting workflow itself is
# renamed ``scf-httk-v1``, no run/edge content changes); and the served entry type is
# pluralized to ``_anyterial_altermagnet_screening_results``. None of this changes any
# ledger key, so ids are unaffected; the schema/column changes force the rebuild.
STORE_LAYOUT_VERSION = 14
RELAXED_STRUCTURE_PRECISION = 5e-4  # Cartesian Å; relaxed-DFT coordinate precision, so symmetry tolerance is realistic (not ~machine epsilon from full-precision CONTCAR digits)

ELEMENT_PATTERN = re.compile(r"[A-Z][a-z]?")
SCREENING_RESULTS_FILENAME = "high_throughput_screening_results_fixed.csv"
MAGNDATA_COLLINEAR_FILENAME = "altermagnets_collinear.csv"
MAGNDATA_NONCOLLINEAR_FILENAME = "altermagnets_noncollinear.csv"
AMDB_ID_COLUMN = "AMDBId"
AMDB_DATASET = "1"
STORE_PATH_ENVIRONMENT = "ALTERMAGNETS_STORE_PATH"
RUNS_PATH_ENVIRONMENT = "ALTERMAGNETS_RUNS_DIR"
DETAILS_PATH_ENVIRONMENT = "ALTERMAGNETS_DETAILS_DIR"
LEDGER_PATH_ENVIRONMENT = "ALTERMAGNETS_LEDGER_PATH"

#: The sealed id ledger lives on the untracked data volume (``data/amdb_ids.sqlite``),
#: independent of the git-tracked ``tables/`` curation directory (see the ledger
#: relocation design): it maps stable amdb source keys to entry ids so a rebuilt
#: store keeps its ids and content changes become revisions, and it is machine-grown
#: registry state rather than curated content that belongs in git. The run<->material
#: coupling that used to live in a git-tracked CSV alongside it is now expressed as
#: ledger bindings (see the run-coupling-ledger-bindings design); no coupling document
#: exists any more.
LEDGER_FILENAME = "amdb_ids.sqlite"

#: The per-family id bases the ledger mints under. These are the DEPLOYED id
#: shapes (``anyt.am.structure-1-N`` etc.) and are load-bearing: serving,
#: alternatives and provenance all assume them, so they are hand-pinned here and
#: never derived from the family types.
#: ``results`` mints the served material ids ``anyt.am-1-N`` (base ``anyt.am``,
#: distinct from the ``anyt.am.<type>`` bases and not a duplicate value); it is the
#: family the one-time seeding populates from the screening rows' magndata keys.
LEDGER_BASES = {
    "structures": "anyt.am.structure",
    "references": "anyt.am.refs",
    "runs": "anyt.am.runs",
    "records": "anyt.am.records",
    "files": "anyt.am.files",
    "results": "anyt.am",
}
LEDGER_SERIES = "1"

#: Seal-key refs the build signs the ledger with. ``identity`` is the operator's
#: default identity (this repo is NOT an httk project, so no ``project`` ref);
#: the build fails at open if none resolves rather than silently skip sealing.
#: The seal is an audit record (who signed each committed ledger, logged and
#: inspected via git history), not a build gate; the build never pins a signer.
LEDGER_SIGNER_REFS: tuple[str, ...] = ("identity",)
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
class CalculationOutputRecord:
    """The coupled DFT run's declared outputs, one row per coupled material.

    A backing of the AMDB ``_httk_records`` family: a normalized results table whose
    every field is served under its own property definition, replacing the meta-level
    one-value :class:`~httk.core.DataRecord`. It inherits the ledger id of the record
    it replaces (key ``amdb:<id>:total_energy``), so the reconstructed run's edges and
    the ProductLinks targeting it keep resolving.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_calculation_outputs")

    total_energy: float
    # Entry-id fields per the store contract; id is always the ledger id at construction.
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)
    last_modified: Annotated[datetime.datetime | None, IdentitySkip()] = field(default=None, compare=False)

    @property
    def type(self) -> str:
        """Return the internal (unprefixed) entry type name."""
        return "records"


@dataclass(frozen=True)
class ScreeningResultRecord:
    """The published screening analysis's values, one row per screened material.

    The second backing of the AMDB ``_httk_records`` family. These values come from
    the published screening CSV rather than from any run we collected, so the record
    carries NO provenance edges and no ProductLink (design Q5); it is related to its
    material solely by the result's :attr:`AltermagnetScreeningResult.screening_record`
    reference. The result keeps queryable/sortable mirrors of the same five values.
    No indexes: a few hundred rows scan faster than they index.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="altermagnets_screening_records")

    max_ss: float | None
    avg_ss: float | None
    #: Percent, exactly as the result's mirror column stores it; served as a fraction.
    fdelta_pct: float | None
    bandgap: float | None
    electronic_type: str
    # Entry-id fields per the store contract; id is always the ledger id at construction.
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)
    last_modified: Annotated[datetime.datetime | None, IdentitySkip()] = field(default=None, compare=False)

    @property
    def type(self) -> str:
        """Return the internal (unprefixed) entry type name."""
        return "records"


@dataclass(frozen=True)
class AltermagnetScreeningResult:
    """The AMDB main entity: one screened material's screening science and figures.

    Served under the provider-specific ``_anyterial_altermagnet_screening_results``
    entry type. It owns the science, figures, MAGNDATA variants, DOIs, and the primary
    ids (``anyt.am-1-N``); the screened crystal structure is a separate standard
    ``structures`` main referenced through :attr:`structure`, and the authoritative
    record values through :attr:`calculation_output` / :attr:`screening_record`.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="altermagnets_screening_results",
        # The result is NOT a provenance node: it is a curated collection entry, so no
        # run edge targets it. Provenance runs result -> injected ``_httk_runs``
        # relationship (stamped ``run_id``) -> run -> forward ``_httk_has_output``. The
        # science columns below stay as the queryable/sortable mirrors of the records.
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
    # The screened crystal structure, demoted to a separate standard ``structures``
    # main (``UnitcellStructureRecord``) and referenced here. The store auto-registers
    # the depth-1 ``structures.id HAS`` filter handler off this typed reference field;
    # the build carries the SAME structure instance (id=None) that content-id dedup
    # unifies with the stamped structure main saved just before this result row.
    structure: UnitcellStructureRecord | None = None
    # The two typed ``_httk_records`` references, stamped at build time onto the saved
    # record mains (content dedup unifies them, exactly as ``structure`` does). Both
    # serve under the one ``_httk_records`` relationship key, told apart by the
    # ``Related`` role, and they make depth-1 ``_httk_records.<property>`` filters work
    # on the results endpoint with the machinery that already exists.
    calculation_output: Annotated[
        CalculationOutputRecord | None,
        Related(role="calculation_output_record", description="The coupled DFT run's declared outputs."),
    ] = None
    screening_record: Annotated[
        ScreeningResultRecord | None,
        Related(
            role="screening_result_record",
            description="The published screening analysis's values for this material.",
        ),
    ] = None
    # The densely enumerated ``anyt.am.refs-1-N`` ids of this material's DOIs, aligned
    # with ``dois``, stamped at build/seed time (the enumeration needs the whole
    # deployment's DOI order). Serving reads them straight off the row, so the
    # doi->relationship->include chain resolves without re-deriving the global order.
    reference_ids: tuple[str, ...] = ()
    # The stamped ``anyt.am.structure-1-N`` id of this result's structure main (or
    # ``None`` when it has no structure), stamped at build/seed time from the
    # material->structure-id map. The stored route serves the ``structures`` block
    # natively off the typed ``structure`` reference (E3), so this scalar is no longer
    # a served property; it drives the build and the in-memory dataset provider's
    # ``structures`` relationship (``server/serve/dataset.py``).
    structure_id: str | None = None
    # The stamped ``anyt.am.runs-1-N`` id of this material's coupled run (or ``None``
    # when it has none), stamped at build time from ``ledger.lookup(_run_key(material.id))``
    # -- the binding already exists at that point (:func:`_couple_runs` runs before
    # stamping). Served ONLY through the private, never-response ``_httk_custom_run_id``
    # projection (the ``reference_ids``/``_httk_custom_reference_ids`` pattern); the
    # adapter injects the served ``_httk_runs`` relationship block off it (the
    # ``references`` pattern). Upgrade note: the envelope injection is ALREADY
    # include-hydratable (the collector falls back to the block key as the type);
    # only depth-1 filtering/include *through* ``_httk_runs`` would need a typed
    # reference field here instead of this private scalar.
    run_id: str | None = None
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


ANYTERIAL_ENTRYTYPES_DIR = Path(__file__).resolve().parents[2] / (
    "dependencies/submodules/anyterial-schemas/defs/v0.1/entrytypes"
)
#: The IRI base covering EVERY published Anyterial definition (properties AND entry
#: types), re-registered for the ``_anyterial_`` prefix so ``served_form()`` prefixes
#: the ``altermagnet_screening_results`` entry-type name whose ``$id`` lives under
#: ``.../defs/v0.1/entrytypes/`` (the narrower ``.../properties`` base does not cover
#: it). Additive re-registration keeps the ``.../properties`` synthesis base intact.
ANYTERIAL_DEFS_ID_BASE = "https://schemas.anyterial.se/defs/"
#: The vendored entry-type definition IRI of the AMDB main entity.
ALTERMAGNET_SCREENING_RESULT_DEFINITION_ID = (
    "https://schemas.anyterial.se/defs/v0.1/entrytypes/altermagnet_screening_results"
)


#: The store-native slim standard OPTIMADE ``structures`` family. Demoted from the AMDB
#: main entity to a standard ``structures`` type, it serves only the standard structural
#: properties (including the CrysViz detail fields and ``_httk_site_moments``) of the
#: screened crystal, projected directly off the ``UnitcellStructureRecord`` main; the
#: screening science moved to :class:`AltermagnetScreeningResultEntry`. It IS httk-atomistic's
#: ``StructureEntry`` (aliased for clarity, not re-registered): ``UnitcellStructureRecord`` is
#: already globally registered under that family, so a distinct wrapper would double-register it.
AltermagnetStructureEntry = StructureEntry


class AltermagnetScreeningResultEntry:
    """The AMDB provider-specific main entity family (screening results).

    The vendored entry-type definition (a minimal id/type/immutable_id/last_modified
    document, authored in ``anyterial-schemas-source`` and regenerated through the
    OPTIMADE meta-schema gate) is loaded via
    :meth:`EntryTypeDefinition.from_optimade` and extended with the served science,
    figure, energy, and private relationship-id property definitions. Its ``$id``
    lives under the re-registered ``_anyterial_`` base, so ``served_form()`` names it
    ``_anyterial_altermagnet_screening_results`` on the wire.
    """

    type = "altermagnet_screening_results"
    definition_id = ALTERMAGNET_SCREENING_RESULT_DEFINITION_ID

    @classmethod
    def entry_type_definition(cls) -> EntryTypeDefinition:
        """Return the vendored entry type extended with the served AMDB properties."""
        path = ANYTERIAL_ENTRYTYPES_DIR / "altermagnet_screening_results.json"
        if not path.is_file():
            raise RuntimeError(
                f"Entry-type definition file {path} is missing; regenerate it in "
                "anyterial-schemas-source (`make`) and vendor it via `make update_schemas`."
            )
        base = EntryTypeDefinition.from_optimade(cls.type, json.loads(path.read_text(encoding="utf-8")))
        # The science/figure/energy/private-id definitions, minus the standard
        # structural properties (those serve on the structures family now).
        return base.extended(_optimade_definitions())


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


#: The five served properties of :class:`ScreeningResultRecord`, mapped to the field
#: each reads. Their definitions are the vendored ones the result's mirrors already use.
_SCREENING_RECORD_FIELDS: tuple[tuple[str, str], ...] = (
    ("_anyterial_max_spin_splitting", "max_ss"),
    ("_anyterial_avg_spin_splitting", "avg_ss"),
    ("_anyterial_spin_splitting_fraction", "fdelta_pct"),
    ("_anyterial_electronic_type", "electronic_type"),
    ("_httk_dft_band_gap", "bandgap"),
)


class AltermagnetDataRecordEntry:
    """The store-native AMDB ``_httk_records`` family, backed by two normalized tables.

    :class:`CalculationOutputRecord` and :class:`ScreeningResultRecord` are the AMDB
    deployment's results tables, one per producer kind; the family's served entry type
    is the union of their property definitions, and a property a backing lacks serves
    as null (every one of them is declared nullable in its vendored document).
    """

    type = "records"
    definition_id = RECORDS_DEFINITION_ID

    @classmethod
    def entry_type_definition(cls) -> EntryTypeDefinition:
        """Return the base records definition extended with both backings' properties."""
        shared = _optimade_definitions()
        properties = {
            "_httk_total_energy": _load_property_definition(
                HTTK_DEFS_DIR / "core/total_energy.json", "_httk_total_energy"
            ),
            **{served_name: shared[served_name] for served_name, _field in _SCREENING_RECORD_FIELDS},
        }
        return load_entry_type_definition(RECORDS_DEFINITION_ID).extended(properties)


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


def _private_run_id_definition() -> PropertyDefinition:
    document = PropertyDefinition.from_simple(
        "_httk_custom_run_id",
        description="Private stamped run id used to construct the injected OPTIMADE `_httk_runs` relationship block.",
    ).as_optimade()
    document["x-optimade-requirements"] = {
        "support": "may",
        "query-support": "none",
        "response-level": "must not",
    }
    return PropertyDefinition.from_optimade("_httk_custom_run_id", document)


def _load_property_definition(path: Path, served_name: str) -> PropertyDefinition:
    """Load one vendored OPTIMADE property definition document under its served name."""
    if not path.is_file():
        raise RuntimeError(
            f"Property definition file {path} is missing; initialize the schema submodules "
            "via `git submodule update --init` or `make update_schemas`."
        )
    return PropertyDefinition.from_optimade(served_name, json.loads(path.read_text(encoding="utf-8")))


@cache
def _optimade_definitions() -> dict[str, PropertyDefinition]:
    """Load the curated AMDB definitions used by both stored schema and docs tests."""
    register_definition_prefix("_anyterial_", ANYTERIAL_DEFS_BASE)
    # Additive re-registration (the ``_httk_`` pattern): recognizes the entry-type
    # ``$id`` under ``.../defs/`` so ``served_form()`` prefixes the screening-result
    # entry-type name to ``_anyterial_altermagnet_screening_results``. Without it the
    # federation would serve the UNPREFIXED internal type name.
    register_definition_prefix("_anyterial_", ANYTERIAL_DEFS_ID_BASE)
    definitions: dict[str, PropertyDefinition] = {}
    for base, paths in ((ANYTERIAL_DEFS_DIR, ANYTERIAL_DEFINITION_PATHS), (HTTK_DEFS_DIR, HTTK_DEFINITION_PATHS)):
        for served_name, relative_path in paths.items():
            definitions[served_name] = _load_property_definition(base / relative_path, served_name)
    definitions["_httk_custom_figures"] = _local_figure_definition()
    definitions["_httk_custom_public_id"] = _private_id_definition("structure")
    definitions["_httk_custom_reference_ids"] = _private_reference_ids_definition()
    definitions["_httk_custom_run_id"] = _private_run_id_definition()
    return definitions


def _reference_key(doi: str) -> str:
    """Return the stable ledger key for a DOI (case-insensitive)."""
    return f"doi:{doi.lower()}"


def _structure_key(amdb_id: str) -> str:
    """Return the stable ledger key for a material's structure main."""
    return f"amdb:{amdb_id}:structure"


def _run_key(amdb_id: str) -> str:
    """Return the stable ledger key for a material's entity->run BINDING.

    This is the *binding* half of run coupling (see the run-coupling-ledger-bindings
    design, D3): it names which run a material is backed by, expressed as an
    ``alias`` onto the run's own id. It predates the run's *intrinsic* identity
    (:func:`_run_source_key`), so every already-deployed key of this shape is kept
    verbatim -- the migration only adds the intrinsic key as an additional alias
    of the same id, never renumbers or re-binds this one.
    """
    return f"amdb:{amdb_id}:run"


def _run_source_key(source_id: str) -> str:
    """Return the stable ledger key (family ``runs``) for a run's intrinsic identity.

    ``source_id`` is the run-native identity already carried by every collected
    :class:`~httk.core.provenance.Run` (v2: ``<workspace_id>:<job_id>``; v1:
    ``httk-v1:<manifest-hash>``). Unlike :func:`_run_key`, this key does not depend
    on which (if any) material the run backs, so it is the handle that *establishes*
    a run's ledger identity independent of coupling.
    """
    return f"run:{source_id}"


#: Matches an entity->run binding key (see :func:`_run_key`), extracting the amdb id.
#: Used to find which material (if any) a given run id is currently bound to, so a
#: name/raw_path match can never silently steal a run another material already owns.
_RUN_BINDING_KEY_PATTERN = re.compile(r"^amdb:(?P<amdb_id>.+):run$")


def _record_key(amdb_id: str, role: str) -> str:
    """Return the stable ledger key for a run output record (``role`` = output role).

    The ``total_energy`` role keys the material's :class:`CalculationOutputRecord`: the
    typed record INHERITS the id the retired one-value ``DataRecord`` held, so existing
    run edges and ProductLinks keep resolving and the content change is an ordinary
    revision rather than a supersession.
    """
    return f"amdb:{amdb_id}:{role}"


def _screening_record_key(amdb_id: str) -> str:
    """Return the stable ledger key for a material's :class:`ScreeningResultRecord`."""
    return f"amdb:{amdb_id}:screening_record"


def _file_key(amdb_id: str, relpath: str) -> str:
    """Return the stable ledger key for a run output file (``relpath`` = its locator url)."""
    return f"amdb:{amdb_id}:file:{relpath}"


def _normalize_magndata_cell(cell: str) -> str:
    """Normalize a screening row's ``MAGNDATA ID`` cell into a stable key token.

    A cell may list several MAGNDATA identifiers (comma-separated); the tokens are
    stripped, sorted, and rejoined with ``,`` so the key is order-independent. The
    result is the identity of the served material: editing the cell is an identity
    change, re-bound only through the ledger's explicit supersession ceremony, never
    silently re-minted.
    """
    return ",".join(sorted(token.strip() for token in cell.split(",") if token.strip()))


def _result_key(cell: str) -> str:
    """Return the stable ledger key (family ``results``) for a screening magndata cell."""
    return f"magndata:{_normalize_magndata_cell(cell)}"


def _seed_result_ids(ledger: IdLedger, screening_rows: list[dict[str, str]]) -> None:
    """One-time, pre-validated seeding of the ``results`` family (irreversible by convention).

    Called only when the results family is empty (the screening rows' magndata keys
    are all unassigned). The ENTIRE screening table is validated BEFORE the first
    assign, so a per-row mismatch can never leave a partially seeded ledger: the
    build's failure handler reseals whatever was assigned, so nothing may be assigned
    until the whole table is known good.

    Validation: every ``MAGNDATA ID`` cell non-empty; the normalized keys unique; the
    ``AMDBId`` column dense ``anyt.am-1-1``..``-N`` in row order; and every key still
    unassigned (the results family holds zero entries). Only then are N ids minted in
    row order, each asserted to equal that row's ``AMDBId``. After this build the
    ``AMDBId`` column is never read for identity again.

    :param ledger: The open ledger whose freshly added ``results`` family is seeded.
    :param screening_rows: The screening CSV rows in file order.
    :raises ValueError: If any validation fails; the ledger is left untouched.
    """
    keys = [_result_key(row.get("MAGNDATA ID", "")) for row in screening_rows]
    for index, (row, key) in enumerate(zip(screening_rows, keys, strict=True), start=1):
        if _normalize_magndata_cell(row.get("MAGNDATA ID", "")) == "":
            raise ValueError(f"screening row {index} has an empty MAGNDATA ID; the results ledger needs a magndata key")
    if len(set(keys)) != len(keys):
        seen: set[str] = set()
        duplicate = next(key for key in keys if key in seen or seen.add(key))  # type: ignore[func-returns-value]
        raise ValueError(f"screening magndata key {duplicate!r} is not unique; cannot seed the results ledger")
    for index, row in enumerate(screening_rows, start=1):
        expected = f"anyt.am-1-{index}"
        found = (row.get(AMDB_ID_COLUMN) or "").strip()
        if found != expected:
            raise ValueError(
                f"screening row {index}: {AMDB_ID_COLUMN} {found!r} is not the expected dense id {expected!r}; "
                "the one-time results seeding requires a dense anyt.am-1-1..-N AMDBId column in row order"
            )
    for key in keys:
        if ledger.lookup(key) is not None:
            raise ValueError(f"results ledger already binds {key!r}; refusing to re-seed a non-empty results family")
    for index, key in enumerate(keys, start=1):
        assigned = ledger.assign(key, "results")
        expected = f"anyt.am-1-{index}"
        # Pure minting after full validation, so this only trips on a code bug; the
        # documented recovery is restoring the ledger from backup (it is untracked,
        # so `git restore` cannot help -- see the ledger relocation design, §7).
        assert assigned == expected, f"results seeding row {index} minted {assigned!r}, expected {expected!r}"


def _result_ids(ledger: IdLedger, screening_rows: list[dict[str, str]]) -> dict[str, str]:
    """Resolve every screening row's ``results`` id from the ledger, seeding on first build.

    Keyed by the normalized magndata cell (:func:`_result_key`). On the first build
    the results family is empty (none of these keys is assigned) and the pre-validated
    one-time seeding runs; thereafter the ids are pure idempotent lookups. A future CSV
    that grows past the seeded set mints the new rows' keys normally (idempotent assign,
    no AMDBId assertion), matching the fallback's positional-when-absent behaviour.

    :param ledger: The open ledger (its ``results`` family added via superset-open).
    :param screening_rows: The screening CSV rows in file order.
    :return: A ``magndata key`` -> ``anyt.am-1-N`` map covering every screening row.
    """
    keys = [_result_key(row.get("MAGNDATA ID", "")) for row in screening_rows]
    if not any(ledger.lookup(key) is not None for key in keys):
        _seed_result_ids(ledger, screening_rows)
    return {key: ledger.assign(key, "results") for key in keys}


def _open_ledger(ledger_path: Path, *, create_if_missing: bool = False) -> IdLedger:
    """Open the sealed id ledger at *ledger_path*.

    The build always signs the ledger with :data:`LEDGER_SIGNER_REFS`; a missing
    signing seed fails here rather than silently skipping the seal. The signature
    is an audit record, not a build gate: an existing ledger opens without a
    pinned signer (the integrity self-check still refuses a tampered file), and
    ``IdLedger.open`` logs who signed it.

    A missing ledger file is refused by default rather than silently created fresh:
    now that the ledger lives on the untracked data volume (not git-tracked), a
    missing file is far more likely a data-loss symptom (wrong path, an un-restored
    backup after a fresh checkout/clone) than a genuine first deployment, and
    building against a fresh empty ledger would silently re-mint every public id --
    a catastrophic, silent divergence from production. Every caller (the build, the
    curation tool's write path) routes through here, so this is the one place that
    refusal is enforced.

    :param ledger_path: The resolved id ledger file path (see :func:`resolve_ledger_path`).
    :param create_if_missing: Whether a missing ledger is created fresh. Only
        :func:`build_store`'s ``initialize_ledger`` flag (the once-per-deployment
        initialization ceremony, like the results-family seeding) opts into this;
        every other caller (e.g. the curation tool) keeps the safe default.
    :return: The open, locked ledger.
    :raises SealError: If no signing seed is available.
    :raises RuntimeError: If the ledger is missing and *create_if_missing* is false.
    """
    try:
        keys: tuple[SealKey, ...] = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=ledger_path.parent).keys
    except SealError as error:
        raise SealError(
            f"cannot seal the id ledger {ledger_path}: {error}. Configure an operator identity "
            "(`httk identity`) so the build can sign the ledger; the build refuses to skip sealing."
        ) from error
    if ledger_path.exists():
        # Pass the code-side scheme so a drift between LEDGER_BASES/LEDGER_SERIES and the
        # committed file is a loud error. bases= is reconciled as a SUPERSET: a family
        # in LEDGER_BASES absent from the stored map (the "results" family on the first
        # build after the split) is added and stamped at reseal; a changed/removed
        # stored family still errors.
        return IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES)
    if not create_if_missing:
        raise RuntimeError(
            f"id ledger {ledger_path} does not exist. A missing ledger is never auto-created: building (or "
            "assigning) against a fresh empty one would silently re-mint every public id, diverging from "
            "production. Restore the ledger from backup, or -- for a genuine first-time deployment only -- "
            "pass build_store's initialize_ledger=True (tools/build_store.py's --initialize-ledger)."
        )
    return IdLedger.create(ledger_path, bases=LEDGER_BASES, series=LEDGER_SERIES, keys=keys)


def _reference_ids_by_doi(
    materials: Iterable["AltermagnetScreeningResult"], ledger: IdLedger | None = None
) -> dict[str, str]:
    """Map every DOI to its ``anyt.am.refs-1-N`` id, in first-seen order.

    With a *ledger* the ids come from stable, case-insensitive ``doi:<lowered>``
    keys, so a DOI keeps its id across rebuilds and two case variants collapse to
    one id; without one (the in-memory fallback) the ids are densely enumerated in
    first-seen order exactly as before. It is the one place the reference ids are
    minted: the save sites stamp each material's
    :attr:`AltermagnetScreeningResult.reference_ids` from this map and save the
    reference rows under the same ids, so serving never re-derives the global order.

    :param materials: The materials whose DOIs are enumerated, in deployment order.
    :param ledger: The open ledger to allocate from, or ``None`` for dense enumeration.
    :return: A DOI-to-``anyt.am.refs-1-N`` mapping keyed by every original DOI.
    """
    ordered = dict.fromkeys(doi for material in materials for doi in material.dois)
    if ledger is None:
        return {doi: f"anyt.am.refs-1-{number}" for number, doi in enumerate(ordered, 1)}
    return {doi: ledger.assign(_reference_key(doi), "references") for doi in ordered}


def _assign_structure_owner(ledger: IdLedger, key: str) -> str:
    """Return the group id for its owner key, superseding when the key was an alias.

    Detection uses only the public surface: the plain idempotent ``assign`` mints
    a new key and returns an already-assigned key's id unchanged, and raises only
    when the key currently holds an *alias* (the family is always ``structures``
    here, so that is the sole failure). That alias-that-should-assign is the split
    half of regrouping, re-bound to a fresh id with ``supersede=True``.
    """
    try:
        return ledger.assign(key, "structures")
    except IdLedgerError:
        return ledger.assign(key, "structures", supersede=True)


def _alias_structure_member(ledger: IdLedger, key: str, group_id: str) -> None:
    """Alias a non-owner key onto its group id, superseding an assigned key (merge).

    ``lookup`` tells absent/idempotent apart from a re-bind: a key that is absent
    or already resolves to *group_id* takes the plain idempotent ``alias`` (never
    ``supersede`` when intent matches state); a key resolving elsewhere is a
    currently-assigned key merging into this group, re-bound with ``supersede=True``.

    A member that was an *alias* in one shared group and migrates to a *different*
    shared group across builds (never itself an owner) is out of scope: the ledger's
    ``supersede`` re-binds an assignment, not an alias, so ``alias`` here raises
    ``already aliased to X, not Y``. Recover it manually, deliberately, in two
    append-only steps on the ledger — first ``assign(key, "structures",
    supersede=True)`` to split the stale alias into a fresh assignment, then
    ``alias(key, <new group id>, supersede=True)`` to merge that assignment onto the
    new group — rather than adding coded recovery for a case current data never hits
    (0 shared groups).
    """
    current = ledger.lookup(key)
    if current is None or current == group_id:
        ledger.alias(key, group_id)
    else:
        ledger.alias(key, group_id, supersede=True)


def _structure_mains(
    materials: Iterable["AltermagnetScreeningResult"],
    ledger: IdLedger | None = None,
) -> tuple[dict[str, str], dict[str, UnitcellStructureRecord]]:
    """Enumerate the stamped ``structures`` mains, deduplicated by structure content.

    Results whose relaxed structures are content-identical share one stamped
    structure main -- the store's content-id dedup requires it (two rows with one
    content id but different ids conflict) -- and each sharing result carries the
    SAME canonical stamped instance, so its nested reference dedups onto the main
    with identical identity-excluded metadata rather than raising a conflict.

    With a *ledger* each group's id is assigned via the SMALLEST ``amdb:<id>:structure``
    key in the group (deterministic, build-order independent) and the other members'
    keys are recorded as aliases of it; the key is the amdb id and never depends on
    whether the bytes came from the coupled run or the details-CONTCAR fallback (a
    content change is a revision, not a new id). When a later build regroups, the
    binding is reconciled against ledger state: a member that splits off into its
    own group supersedes its old alias with a freshly minted id, and a structure
    that merges into another group supersedes its old assignment with an alias onto
    the group id (both via the append-only supersession API). Without a ledger (the
    in-memory fallback) the mains are densely enumerated in first-seen order as before.
    It is the one place the structure ids are minted: the save sites save these mains
    and stamp each result's :attr:`AltermagnetScreeningResult.structure_id` /
    ``structure`` field, and the provenance retarget / alternatives re-parent through
    the returned map, so serving injects the ``structures`` relationship without
    re-deriving the global order.

    :param materials: The results whose structures are enumerated, in deployment order.
    :param ledger: The open ledger to allocate from, or ``None`` for dense enumeration.
    :return: The ``result id``-to-``structure id`` map and the stamped structure mains
        keyed by structure id.
    """
    groups: dict[str, list[tuple[str, UnitcellStructureRecord]]] = {}
    order: list[str] = []
    for material in materials:
        structure = material.structure
        if structure is None:
            continue
        assert material.id is not None  # always set to the amdb id at construction
        key = content_id(structure)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((material.id, structure))
    id_by_material: dict[str, str] = {}
    mains: dict[str, UnitcellStructureRecord] = {}
    for number, key in enumerate(order, 1):
        members = groups[key]
        if ledger is None:
            structure_id = f"anyt.am.structure-1-{number}"
            main = replace(members[0][1], id=structure_id)
        else:
            owner = min(amdb_id for amdb_id, _ in members)
            structure_id = _assign_structure_owner(ledger, _structure_key(owner))
            owner_structure = next(structure for amdb_id, structure in members if amdb_id == owner)
            main = replace(owner_structure, id=structure_id)
            for amdb_id, _ in members:
                if amdb_id != owner:
                    _alias_structure_member(ledger, _structure_key(amdb_id), structure_id)
        mains[structure_id] = main
        for amdb_id, _ in members:
            id_by_material[amdb_id] = structure_id
    return id_by_material, mains


def _stamp_material(
    material: "AltermagnetScreeningResult",
    reference_id_by_doi: Mapping[str, str],
    structure_id_by_material: Mapping[str, str],
    structure_mains: Mapping[str, UnitcellStructureRecord],
    screening_records: Mapping[str, "ScreeningResultRecord"],
    calculation_outputs: Mapping[str, "CalculationOutputRecord"],
    ledger: IdLedger | None = None,
) -> "AltermagnetScreeningResult":
    """Stamp a result's reference ids, structure id, run id, and canonical structure/record mains once.

    Repointing ``structure``, ``screening_record`` and ``calculation_output`` at the
    canonical stamped mains lets the result's nested references dedup onto the
    already-saved mains with identical metadata. The record maps are empty on the
    legacy/in-memory paths, which store no records family. ``run_id`` is looked up from
    the already-resolved entity->run ledger binding (:func:`_couple_runs` runs before
    stamping), never re-derived here; ``ledger is None`` (the legacy/in-memory paths,
    which open no ledger) stamps ``None`` -- the documented degradation already used for
    the record family above.
    """
    assert material.id is not None  # always set to the amdb id at construction
    structure_id = structure_id_by_material.get(material.id)
    return replace(
        material,
        reference_ids=tuple(reference_id_by_doi[doi] for doi in material.dois),
        structure_id=structure_id,
        structure=material.structure if structure_id is None else structure_mains.get(structure_id, material.structure),
        screening_record=screening_records.get(material.id),
        calculation_output=calculation_outputs.get(material.id),
        run_id=None if ledger is None else ledger.lookup(_run_key(material.id)),
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


def _provider_response(property_name: str) -> Callable[[object], object]:
    # Resolves directly to the per-property reader (pure, no store enumeration):
    # a response call touches only that property's underlying field(s) on the
    # lazy row instead of building every property first. A root-relative base
    # is made absolute by the thin service adapter.
    from serve.dataset import _PROPERTY_READERS

    reader = _PROPERTY_READERS[property_name]

    def response(record: object) -> object:
        return reader(cast(AltermagnetScreeningResult, record), "")

    return response


def _record_field_response(field_name: str) -> Callable[[object], object]:
    def response(record: object) -> object:
        return getattr(record, field_name)

    return response


def _field_sort(field_name: str) -> Callable[[Any], Any]:
    def sort(context: Any) -> Any:
        return context.field(field_name)

    return sort


def _fraction_literal_to_percent(value: object) -> object:
    return cast(Any, value) * 100


def _record_fraction(record: object) -> float | None:
    """The screening record's percent column served as a fraction (the result's convention)."""
    percent = cast(ScreeningResultRecord, record).fdelta_pct
    return None if percent is None else percent / 100.0


def _material_public_id(record: object) -> str:
    public_id = cast(AltermagnetScreeningResult, record).id
    assert public_id is not None  # always set to the amdb id at construction
    return public_id


def _material_reference_ids(record: object) -> list[str]:
    return list(cast(AltermagnetScreeningResult, record).reference_ids)


def _material_run_id(record: object) -> str | None:
    return cast(AltermagnetScreeningResult, record).run_id


def _reference_doi(record: object) -> str:
    return cast(AltermagnetReferenceRecord, record).doi


def _reference_public_id(record: object) -> str:
    return cast(AltermagnetReferenceRecord, record).public_id


def _material_projections() -> dict[str, StoredPropertyProjection]:
    # The screening result serves only the AMDB science; the standard structural
    # properties (formerly nested onto ``structure`` here) now serve directly off the
    # ``UnitcellStructureRecord`` structures main.
    projections: dict[str, StoredPropertyProjection] = {}
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
        "_httk_magnetic_space_group_bns",
    ):
        projections[name] = StoredPropertyProjection(response=_provider_response(name))
    # The served list is sorted via its scalar mirror stored column (see
    # AltermagnetScreeningResult.magndata_sort_key); there is no queryable list column here.
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
    projections["_httk_custom_run_id"] = StoredPropertyProjection(response=_material_run_id)
    return projections


cast(Any, AltermagnetScreeningResult).__httk_stored_properties__ = _material_projections()
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
# The two records backings each serve their own subset of the family's union entry type
# (a property the other backing lacks serves as null). ``_anyterial_spin_splitting_fraction``
# keeps the result mirror's convention exactly: the column is percent, the served value a
# fraction, so the query literal is scaled back up by 100.
cast(Any, CalculationOutputRecord).__httk_stored_properties__ = {
    "_httk_total_energy": StoredPropertyProjection(
        response=_record_field_response("total_energy"),
        query=_scalar_query("total_energy"),
    )
}
cast(Any, ScreeningResultRecord).__httk_stored_properties__ = {
    **{
        served_name: StoredPropertyProjection(
            response=_record_field_response(field_name),
            query=_scalar_query(field_name),
        )
        for served_name, field_name in _SCREENING_RECORD_FIELDS
        if served_name != "_anyterial_spin_splitting_fraction"
    },
    "_anyterial_spin_splitting_fraction": StoredPropertyProjection(
        response=_record_fraction,
        query=_scalar_query("fdelta_pct", literal_transform=_fraction_literal_to_percent),
    ),
}

register_entry_family(
    name="altermagnets-screening-results",
    family=f"{__name__}:AltermagnetScreeningResultEntry",
    definition_id=AltermagnetScreeningResultEntry.definition_id,
)
register_entry_record(
    name="altermagnets-screening-result",
    family="altermagnets-screening-results",
    record=f"{__name__}:AltermagnetScreeningResult",
)
# The slim ``structures`` family reuses httk-atomistic's already-registered
# ``StructureEntry``/``UnitcellStructureRecord`` (aliased above); no local registration.
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
register_entry_family(
    name="altermagnets-records",
    family=f"{__name__}:AltermagnetDataRecordEntry",
    definition_id=AltermagnetDataRecordEntry.definition_id,
)
register_entry_record(
    name="altermagnets-calculation-record",
    family="altermagnets-records",
    record=f"{__name__}:CalculationOutputRecord",
)
register_entry_record(
    name="altermagnets-screening-record",
    family="altermagnets-records",
    record=f"{__name__}:ScreeningResultRecord",
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


#: Where the committed, git-tracked ``tables/`` curation directory used to hold the
#: ledger before its relocation (see :func:`default_ledger_path`). ``tables/`` itself
#: is not otherwise a concept this module has any reason to resolve any more (the
#: coupling document that used to live beside the ledger there is deleted too), so
#: this is kept only as the transitional-check literal in :func:`resolve_ledger_path`,
#: not as a public resolver.
_LEGACY_TABLES_DIR = Path(__file__).resolve().parents[2] / "tables"


def default_ledger_path() -> Path:
    """The untracked data volume's default id ledger location.

    See the ledger relocation design, §7: the ledger is machine-grown registry
    state that outgrows git at real volumes, so it lives beside the mounted data
    rather than in a committed, git-tracked directory (its pre-relocation location).
    """
    return Path(__file__).resolve().parents[2] / "data" / LEDGER_FILENAME


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


#: Test guard: :mod:`conftest` sets this ``True`` so a store-building test can never
#: fall through to the production ``data/amdb_ids.sqlite`` ledger.
_GUARD_DEFAULT_LEDGER_PATH = False


def resolve_ledger_path(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the sealed id ledger's file path.

    Resolution order: an explicit *value*, then :data:`LEDGER_PATH_ENVIRONMENT`, then
    (outside pytest) :func:`default_ledger_path`.

    A narrow, deliberate transitional check guards the one real migration hazard: if
    resolution falls all the way through to the default path and no file is there yet,
    but the pre-relocation ``tables/amdb_ids.sqlite`` still exists, this raises rather
    than silently opening the old location or auto-moving the file -- the orchestrator
    performs that move once, at integration.

    :param value: An explicit ledger path, taking precedence over everything else.
    :return: The resolved ledger file path.
    :raises RuntimeError: If a pytest-only guard catches an unset default, or the
        transitional check above catches an unmigrated production ledger.
    """
    if value is not None:
        return Path(value).expanduser().resolve()
    override = os.environ.get(LEDGER_PATH_ENVIRONMENT, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _GUARD_DEFAULT_LEDGER_PATH:
        raise RuntimeError(
            "resolve_ledger_path fell through to the production data/amdb_ids.sqlite ledger under pytest; "
            "a store-building test must pass an explicit ledger_path (a tmp fixture)"
        )
    resolved = default_ledger_path()
    legacy = _LEGACY_TABLES_DIR / LEDGER_FILENAME
    if not resolved.is_file() and legacy.is_file():
        raise RuntimeError(
            f"the id ledger has moved from {legacy} to {resolved}, but it has not been moved there yet. "
            "Move the file (the orchestrator performs this once, at integration) rather than relying on "
            "an automatic fallback or auto-move."
        )
    return resolved


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


def material_structure(record: AltermagnetScreeningResult) -> UnitcellStructure | None:
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
    data_dir: Path,
    *,
    details_dir: Path,
    legacy_structures: bool = True,
    result_ids: Mapping[str, str] | None = None,
) -> tuple[AltermagnetScreeningResult, ...]:
    screening_path, collinear_path, noncollinear_path = _source_table_paths(data_dir)
    return build_material_records(
        _load_csv_rows(screening_path, delimiter=";"),
        _load_csv_rows(collinear_path),
        _load_csv_rows(noncollinear_path),
        details_dir=details_dir,
        load_details_structures=legacy_structures,
        result_ids=result_ids,
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
    result_ids: Mapping[str, str] | None = None,
) -> tuple[AltermagnetScreeningResult, ...]:
    """Normalize the three current CSVs into the persistent object graph.

    With *result_ids* (the ledger build) each row's served id is the ``results``
    family id looked up by its normalized magndata key; the ``AMDBId`` column is not
    read for identity. Without it (the in-memory fallback) the id is the ``AMDBId``
    column when present, else a positional ``anyt.am-1-<row>`` (dev-only divergence).
    """
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
    materials: list[AltermagnetScreeningResult] = []
    seen_material_ids: set[str] = set()

    for index, row in enumerate(screening_rows, start=1):
        if result_ids is not None:
            # The ledger build: the served id is the results-family id keyed by the
            # row's normalized magndata cell. A missing key means an empty MAGNDATA ID
            # (the seeding gate rejects those), so surface it rather than KeyError-ing.
            key = _result_key(row.get("MAGNDATA ID", ""))
            material_id = result_ids.get(key, "")
            if not material_id:
                raise ValueError(f"screening row {index} has no results-ledger id for magndata key {key!r}")
        else:
            # The in-memory fallback: AMDBId when present, else a positional id.
            material_id = (row.get(AMDB_ID_COLUMN) or "").strip() or f"anyt.am-1-{index}"
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
            AltermagnetScreeningResult(
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
    """One collected v1 run whose relaxed-structure output is available.

    ``material`` is the run's own name-derived candidate material (a suffix variant
    like ``CrSb-2`` is carried verbatim); ``item`` is the whole collected item, from
    which the run's intrinsic identity (``item.run.source_id``), its outputs and its
    products are all read. This carries no content-id fields: a run's ledger identity
    is its intrinsic ``source_id`` (see :func:`_run_source_key`), never a content
    fingerprint (the pre-ledger design pinned ``content_id(run)``/the relaxed
    structure's content id here; both are gone now that coupling is ledger state).
    """

    material: str
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
        relaxed = outputs.get("output_structure") if isinstance(outputs, Mapping) else None
        run = getattr(item, "run", None)
        if relaxed is None or run is None:
            continue
        parsed = parse_v1_task_name(item.record.payload_path.name)
        task_id = item.record.payload_path.name if parsed is None else parsed["task_id"]
        observations.append(
            _RunObservation(
                task_id.removesuffix("_SCF"),
                relaxed,
                str(item.record.payload_path),
                item,
            )
        )
    return tuple(sorted(observations, key=lambda item: (item.material, item.item.run.source_id)))


def _is_run_material(material: str, csv_material: str) -> bool:
    return material == csv_material or re.fullmatch(rf"{re.escape(csv_material)}-\d+", material) is not None


def _run_owners(ledger: IdLedger) -> dict[str, str]:
    """Map every run id currently bound by some material's entity->run key, to that owner.

    Used by :func:`_resolve_run_binding` to enforce the claimed-run rule: a run id
    already bound to one material's :func:`_run_key` must never be silently rebound
    (stolen) onto a different material.
    """
    owners: dict[str, str] = {}
    for key, binding in ledger.bindings().items():
        match = _RUN_BINDING_KEY_PATTERN.fullmatch(key)
        if match is not None:
            owners[binding.id] = match.group("amdb_id")
    return owners


def _resolve_run_binding(
    ledger: IdLedger,
    amdb_id: str,
    source_id: str,
    run_owners: dict[str, str],
) -> str | None:
    """Resolve one matched (material, run) pair against the open ledger.

    Implements the five-branch binding resolution matrix (see the
    run-coupling-ledger-bindings design, §2/§6): a run has intrinsic identity under
    :func:`_run_source_key`; a material's coupling is the :func:`_run_key` binding,
    always resolving to the SAME id once bound -- only the direction of the alias
    differs between a run bound before this design (whose :func:`_run_key` is the
    original *assignment*, the ``anyt.am.runs-1-N`` id itself) and a run bound after
    it (whose :func:`_run_source_key` is the assignment and whose :func:`_run_key`
    is the alias onto it).

    :param ledger: The open (write-mode) ledger.
    :param amdb_id: The matched material's canonical id.
    :param source_id: The matched run's intrinsic ``item.run.source_id``.
    :param run_owners: The current run-id -> owning-material map (see
        :func:`_run_owners`), mutated in place as each material's binding resolves so
        a later material processed in the same build sees it too.
    :return: The resolved run id, or ``None`` when the match is refused (the run is
        already claimed by a different material's binding) -- the material stays
        unbound this build rather than stealing it.
    :raises ValueError: On a genuine conflict: the material is already bound to a
        DIFFERENT run than the one this build's observation resolves to intrinsically.
        This needs a curator's explicit supersession (the curation CLI, a later
        packet), never an automatic rebind.
    """
    binding_key = _run_key(amdb_id)
    existing = ledger.lookup(binding_key)
    source_key = _run_source_key(source_id)
    intrinsic = ledger.lookup(source_key)
    if intrinsic is not None:
        owner = run_owners.get(intrinsic)
        if owner is not None and owner != amdb_id:
            logger.warning(
                "Run %s (id %s) is already bound to material %s; refusing to also bind it to %s",
                source_id,
                intrinsic,
                owner,
                amdb_id,
            )
            return None
    if existing is None and intrinsic is None:
        run_id = ledger.assign(source_key, "runs")
        ledger.alias(binding_key, run_id)
    elif existing is not None and intrinsic is None:
        # Self-migration: a pre-redesign material whose _run_key binding already owns
        # the run's id (see the design's migration, §6). Purely additive.
        ledger.alias(source_key, existing)
        run_id = existing
    elif existing is None and intrinsic is not None:
        ledger.alias(binding_key, intrinsic)
        run_id = intrinsic
    elif existing == intrinsic:
        assert existing is not None  # both branches above already ruled out either being None
        run_id = existing
    else:
        raise ValueError(
            f"material {amdb_id}: bound run {existing!r} ({binding_key!r}) conflicts with run {source_id!r}'s "
            f"intrinsic id {intrinsic!r} ({source_key!r}); re-coupling requires an explicit supersession "
            "(the curation CLI, not an automatic rebind)"
        )
    run_owners[run_id] = amdb_id
    return run_id


def _couple_runs(
    data_dir: Path,
    observations: tuple[_RunObservation, ...],
    *,
    details_dir: Path,
    ledger: IdLedger,
    runs_root: Path | None = None,
    collected: int = 0,
    result_ids: Mapping[str, str] | None = None,
) -> tuple[dict[str, _RunObservation], dict[str, int]]:
    """Match collected runs to screened materials and bind the matches in the id ledger.

    Successor of the old CSV-backed coupling document (run-coupling-ledger-bindings
    design): the matching itself -- details-tree ``raw_path`` authority, name
    matching, the one-run-one-material invariant, and the diagnostics -- is unchanged,
    but a match's disposition is now a ledger operation
    (:func:`_resolve_run_binding`) instead of a CSV row, and an ambiguous or
    unmatched material is simply left unbound this build (an ephemeral, regenerable
    work list, design D5) rather than written anywhere.

    :param data_dir: directory holding the mounted screening CSV.
    :param observations: relaxed-structure observations collected from the v1 tree.
    :param details_dir: root of the detail-asset tree, whose per-material JSONs carry
        the authoritative ``raw_path`` mapping.
    :param ledger: the open (write-mode) id ledger to resolve/record bindings against.
    :param runs_root: the runs root the observations were collected from, named in
        the diagnostic raised when collection produced tasks but no usable observations.
    :param collected: total tasks collected from the tree (before 3a filtering), so a
        wrong runs root (many collected, none usable) is told apart from an empty tree.
    :param result_ids: the ledger's ``magndata key`` -> ``anyt.am-1-N`` map. When given
        (the production build) each screening row's amdb id is the ledger id for its
        magndata key, never the ``AMDBId`` column; without it (the isolated unit
        tests) the ``AMDBId`` column supplies the id.
    :returns: the ``AMDBId``-keyed coupled observations and the status counts.
    """
    screening = _load_csv_rows(data_dir / SCREENING_RESULTS_FILENAME, delimiter=";")
    source_materials: dict[str, str] = {}
    materials_to_amdb: dict[str, list[str]] = {}
    for source_row in screening:
        material = (source_row.get("Material") or "").strip()
        if result_ids is not None:
            amdb_id = result_ids.get(_result_key(source_row.get("MAGNDATA ID", "")), "")
        else:
            amdb_id = (source_row.get(AMDB_ID_COLUMN) or "").strip()
        if not amdb_id or not material:
            raise ValueError(f"{data_dir / SCREENING_RESULTS_FILENAME}: missing AMDBId or Material")
        if amdb_id in source_materials:
            raise ValueError(f"duplicate canonical material ID '{amdb_id}'")
        source_materials[amdb_id] = material
        materials_to_amdb.setdefault(material, []).append(amdb_id)

    # The authoritative AMDB-id -> run path mapping from the details tree, read once.
    details_paths = {amdb_id: details_raw_path(details_dir, amdb_id) for amdb_id in source_materials}
    # Runs authoritatively owned by some material's details JSON. A name match must
    # never steal one of these, even for a material whose own run is not collected
    # in this build (so it never gets the chance to claim it via pass 1 first).
    claimed = {path for path in details_paths.values() if path}

    by_material: dict[str, list[_RunObservation]] = {}
    by_raw_path: dict[str, _RunObservation] = {}
    for obs in observations:
        by_material.setdefault(obs.material, []).append(obs)
        by_raw_path[obs.raw_path] = obs

    # A misconfigured runs root collects tasks whose payloads are all rejected before
    # yielding a relaxed-structure observation (e.g. <root>/1/Runs one level too deep,
    # so every task's one-part payload fails 3a filtering): collected > 0 but
    # observations is empty. An absent or empty tree collects nothing (collected == 0)
    # and stays silent; a root that is merely missing SOME materials' runs still
    # yields other observations and must not trip this (that is an ordinary partial
    # miss, handled per-material below), so the discriminator is deliberately
    # collected-vs-observations, never details_paths/by_raw_path.
    if collected and not observations:
        raise ValueError(
            f"no runs observed from {collected} tasks collected from {runs_root}; "
            "check --runs-dir / ALTERMAGNETS_RUNS_DIR points at the raw_httk_v1 root"
        )

    matched: dict[str, _RunObservation] = {}
    ambiguous: set[str] = set()
    coupled_paths: set[str] = set()

    # Pass 1: authoritative details raw_paths.
    for amdb_id in sorted(source_materials):
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
        matched[amdb_id] = observation
        coupled_paths.add(observation.raw_path)

    # Pass 2: name matching for materials without a details raw_path, over only the
    # runs not already claimed (this build's matches, or another material's details).
    for amdb_id, material in sorted(source_materials.items()):
        if amdb_id in matched:
            continue
        exact = [
            item
            for item in by_material.get(material, [])
            if item.raw_path not in coupled_paths and item.raw_path not in claimed
        ]
        variants = [
            item
            for item in observations
            if _is_run_material(item.material, material)
            and item.material != material
            and item.raw_path not in coupled_paths
            and item.raw_path not in claimed
        ]
        candidates = exact + variants
        if len(exact) == 1 and not variants and len(materials_to_amdb[material]) == 1:
            observation = exact[0]
            matched[amdb_id] = observation
            coupled_paths.add(observation.raw_path)
        elif candidates:
            ambiguous.add(amdb_id)
            source_ids = sorted({item.item.run.source_id for item in candidates})
            logger.warning("Ambiguous run match for %s (%s): candidate source ids %s", amdb_id, material, source_ids)
        else:
            logger.warning("No ingested run for CSV material %s (%s)", amdb_id, material)

    # One-run-one-material invariant. Passes 1/2 already prevent reuse via
    # coupled_paths, so this only ever catches a regression in that bookkeeping.
    path_to_ids: dict[str, list[str]] = {}
    for amdb_id, obs in matched.items():
        path_to_ids.setdefault(obs.raw_path, []).append(amdb_id)
    shared = {path: ids for path, ids in path_to_ids.items() if len(ids) > 1}
    if shared:
        path, ids = min(shared.items())
        raise ValueError(
            f"coupling matches one run to multiple materials: raw_path {path} shared by {', '.join(sorted(ids))}"
        )

    # Runs collected that belong to no CSV material (suffix variants included).
    csv_materials = set(materials_to_amdb)
    orphans = sum(
        1
        for obs in observations
        if obs.material not in csv_materials and re.sub(r"-\d+$", "", obs.material) not in csv_materials
    )
    if orphans:
        logger.info("%d ingested runs have no CSV material row", orphans)

    # Resolve each match against the ledger's binding matrix (see _resolve_run_binding).
    coupled: dict[str, _RunObservation] = {}
    run_owners = _run_owners(ledger)
    for amdb_id, observation in sorted(matched.items()):
        run_id = _resolve_run_binding(ledger, amdb_id, observation.item.run.source_id, run_owners)
        if run_id is not None:
            coupled[amdb_id] = observation

    # A material with an existing entity->run binding but no matching observation
    # this build: the bound run is absent from the tree (identical in spirit to the
    # old CSV world's "row without run" case). Warn and leave it uncoupled for this
    # build; the ledger is never mutated for an absent run.
    for amdb_id in sorted(source_materials):
        if amdb_id in coupled or amdb_id in ambiguous:
            continue
        if ledger.lookup(_run_key(amdb_id)) is not None:
            logger.warning("Coupled run for %s not collected in this build (bound run absent from the tree)", amdb_id)

    counts = {status: count for status, count in (("auto", len(coupled)), ("ambiguous", len(ambiguous))) if count}
    logger.info("Coupled %d materials to runs (%d ambiguous)", len(coupled), len(ambiguous))
    return coupled, counts


def _entry_records_layout() -> dict[type, type | tuple[type, ...]]:
    """Mirror workflow ``--into`` with the altermagnets structure families."""
    return {
        AltermagnetScreeningResultEntry: AltermagnetScreeningResult,
        AltermagnetStructureEntry: UnitcellStructureRecord,
        AltermagnetReferenceEntry: AltermagnetReferenceRecord,
        RunEntry: Run,
        # The AMDB records family (internal type still ``records``), served from the
        # two normalized backings; the collected one-value DataRecords are not stored.
        AltermagnetDataRecordEntry: (CalculationOutputRecord, ScreeningResultRecord),
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


def _save_alternative_cells(
    store: SqlStore,
    materials: Iterable[AltermagnetScreeningResult],
    structure_id_by_material: Mapping[str, str],
) -> tuple[int, int]:
    """Derive and store the conventional/primitive alternatives of each structure.

    Bulk ingest is mains-only, so alternatives are saved with ordinary
    :meth:`SqlStore.save` after the bulk context finalizes: each is a slim standard
    ``UnitcellStructureRecord`` re-parented to its screened structure main
    (``alternative_of=<structure id>``) and gets its own lineage under an immutable
    ``<id>~<kind>~<n>``. The exact nuclear-symmetry
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
    seen: set[str] = set()
    for material in materials:
        structure = material_structure(material)
        if structure is None:
            continue
        assert material.id is not None  # always set to the amdb id at construction
        structure_id = structure_id_by_material.get(material.id)
        # Content-identical structures share one stamped main; derive its alternatives once.
        if structure_id is None or structure_id in seen:
            continue
        seen.add(structure_id)
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
                _material_structure_record(cell),
                alternative_of=structure_id,
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
    """The coupled run's ``total_energy`` DataRecord value, as a plain float."""
    outputs = getattr(observation.item, "outputs", {})
    energy = outputs.get("total_energy") if isinstance(outputs, Mapping) else None
    value = getattr(energy, "value", None)
    return float(value) if isinstance(value, (int, float)) else None


def _record_mains(
    materials: Iterable[AltermagnetScreeningResult],
    coupled: Mapping[str, _RunObservation],
    ledger: IdLedger,
) -> tuple[dict[str, ScreeningResultRecord], dict[str, CalculationOutputRecord]]:
    """Build the two ``_httk_records`` backings' mains, keyed by amdb id.

    Every screened material gets a :class:`ScreeningResultRecord` (fresh
    ``amdb:<id>:screening_record`` ledger keys); every coupled, non-degraded run whose
    outputs carry a numeric total energy also gets a :class:`CalculationOutputRecord`
    under the PRE-EXISTING ``amdb:<id>:total_energy`` key, so the typed record inherits
    the id the retired one-value ``DataRecord`` held and the run edges and ProductLinks
    targeting it keep resolving.

    :param materials: The screened materials, each carrying its amdb id.
    :param coupled: The coupled run observations keyed by amdb id.
    :param ledger: The open ledger to allocate from.
    :return: The ``(screening records, calculation outputs)`` maps keyed by amdb id.
    """
    screening: dict[str, ScreeningResultRecord] = {}
    for material in materials:
        assert material.id is not None  # always set to the amdb id at construction
        # ponytail: two materials whose five screening values coincide would dedup by
        # content onto one row under two ledger ids and the bulk merger would RAISE
        # EntryMetadataConflictError -- a loud build failure, not silent data loss.
        # Upgrade to smallest-key ownership + alias (as for structures) if that ever
        # happens; the published table has no such pair today.
        screening[material.id] = ScreeningResultRecord(
            max_ss=material.max_ss,
            avg_ss=material.avg_ss,
            fdelta_pct=material.fdelta_pct,
            bandgap=material.bandgap,
            electronic_type=material.electronic_type,
            id=ledger.assign(_screening_record_key(material.id), "records"),
        )
    calculations: dict[str, CalculationOutputRecord] = {}
    for amdb_id, observation in coupled.items():
        if getattr(observation.item, "missing_collector", None) is not None:
            continue
        energy = _coupled_total_energy(observation)
        if energy is None:
            continue
        calculations[amdb_id] = CalculationOutputRecord(
            total_energy=energy,
            id=ledger.assign(_record_key(amdb_id, "total_energy"), "records"),
        )
    return screening, calculations


def _resolve_edge_id(
    store: SqlStore,
    label: str,
    entry_type: str,
    entry_id: str,
    *,
    structure_id: str,
    record_ids: Mapping[str, str],
    memo: dict[tuple[str, str], str],
) -> str:
    """Map a collected edge's ``(entry_type, content-id)`` to the store-served id.

    The ``output_structure`` edge resolves to the screened structure main's stamped
    id (the structure IS the served output structure now that the science moved to
    the screening result). It is taken from the build's material->structure-id MAP --
    never a content-id fetch: the details-CONTCAR fallback materials have a structure
    content id that differs from the collected run's output structure id, so a fetch
    would silently miss for every fallback material. A ``records`` edge resolves the
    same way, through ``record_ids`` keyed by the output role: the collected one-value
    ``DataRecord`` is no longer stored at all (the typed
    :class:`CalculationOutputRecord` replaces it and inherits its ledger id), so its
    content id matches nothing and a fetch would always miss. Any other structures or
    records edge is rejected -- a foreign edge (e.g. a future ``input_structure``)
    must not silently self-attribute. File outputs saved in the bulk pass are still
    looked up by their content id -- the id their collected edges carry -- memoized so
    a shared output is fetched once.

    :param store: The finalized store the outputs were bulk-saved into.
    :param label: The collected edge's relationship label (the output role).
    :param entry_type: The collected edge's internal (unprefixed) target type.
    :param entry_id: The collected edge's target id (an output's content id).
    :param structure_id: The coupled material's stamped structure id, targeted by the structure edge.
    :param record_ids: The material's ``output role -> records-family id`` map.
    :param memo: The per-run ``(entry_type, entry_id) -> minted id`` cache.
    :return: The store-served id the rewritten edge must carry.
    :raises ValueError: If the type is unmappable, a structures/records edge has no
        mapped target, or the file output has no resolvable store id.
    """
    if entry_type == AltermagnetStructureEntry.type:
        if label != "output_structure":
            raise ValueError(f"structures edge {label!r} is not the output structure; only it maps to the structure")
        return structure_id
    if entry_type == AltermagnetDataRecordEntry.type:
        resolved = record_ids.get(label)
        if resolved is None:
            raise ValueError(f"records edge {label!r} has no typed AMDB record backing it")
        return resolved
    if entry_type != FileEntry.type:
        raise ValueError(f"run edge to unmappable entry type {entry_type!r}")
    key = (entry_type, entry_id)
    if key not in memo:
        fetched = store.fetch_entry(FileEntry, entry_id, eager=True)
        minted = getattr(fetched, "id", None)
        if not isinstance(minted, str):
            raise ValueError(
                f"{entry_type} output {entry_id!r} has no resolvable store id "
                "(never stored in the bulk pass, or its content id does not match a stored row)"
            )
        memo[key] = minted
    return memo[key]


def _rewrite_edges(
    store: SqlStore,
    edges: Iterable[RunEdge],
    *,
    structure_id: str,
    record_ids: Mapping[str, str],
    memo: dict[tuple[str, str], str],
) -> tuple[RunEdge, ...]:
    """Rewrite each collected edge's content-id target to the store-served id."""
    return tuple(
        RunEdge(
            edge.label,
            edge.entry_type,
            _resolve_edge_id(
                store,
                edge.label,
                edge.entry_type,
                edge.entry_id,
                structure_id=structure_id,
                record_ids=record_ids,
                memo=memo,
            ),
        )
        for edge in edges
    )


def _save_reconstructed_runs(
    store: SqlStore,
    materials: Iterable[AltermagnetScreeningResult],
    coupled: Mapping[str, _RunObservation],
    structure_id_by_material: Mapping[str, str],
    calculation_outputs: Mapping[str, CalculationOutputRecord],
    ledger: IdLedger,
) -> tuple[int, int]:
    """Save one store-resolvable replacement :class:`~httk.core.Run` per coupled material.

    A collected ``item.run``'s edges carry collection-time content ids the store
    never minted, so they cannot resolve. This constructs a replacement run at save
    time -- never mutating ``item.run`` or its :class:`_RunObservation` -- whose edges
    carry the store-served ids: the ``output_structure`` edge is retargeted at the
    screened structure main's stamped id (through the material->structure-id map), the
    ``total_energy`` records edge at the material's :class:`CalculationOutputRecord`,
    and the file edges at the ids minted for the outputs the bulk pass just saved.

    ``artifacts`` is always ``()``: this deployment has no sub-workflows, so an
    artifact edge would only ever duplicate the matching output edge (the collector
    overlays one edge tuple onto both sides) -- serving both bloats every run response
    for no information gain. ``outputs`` alone carries the (retargeted) edges; the core
    :attr:`Run.artifacts` mechanism is untouched (a deployment WITH sub-workflows still
    needs to tell a genuine intermediate artifact from a final output), so restoring it
    here is a one-line change: pass this same rewritten ``edges`` tuple to ``artifacts=``
    too. No edge targets the screening RESULT: the result is a curated collection entry
    assembled at build time, not a product of any run, so provenance runs through the
    records instead (result -> ``_httk_records`` reference -> record -> reverse
    ``_httk_is_output`` -> run).

    ``item.products`` ProductLinks are rewritten through the same map; the
    ``ScreeningResultRecord`` gets neither an edge nor a ProductLink (its values come
    from the published CSV, not from any run we collected). ``inputs`` are omitted (the
    collected runs' inputs are not stored). Reconstructing edges is record construction,
    not post-save mutation, so it is done with an ordinary :meth:`SqlStore.save` after
    the bulk context finalizes (both are refused inside bulk ingest, and the runs need
    the outputs' minted ids the bulk pass assigned). One run backs one material
    (enforced in :func:`_couple_runs`), so exactly one replacement run is saved per
    coupled, non-degraded material.

    :return: The ``(runs, product links)`` saved counts.
    """
    by_id = {material.id: material for material in materials}
    runs = products = 0
    for amdb_id, observation in coupled.items():
        material = by_id.get(amdb_id)
        item = observation.item
        run = getattr(item, "run", None)
        if material is None or run is None or getattr(item, "missing_collector", None) is not None:
            continue
        assert material.id is not None  # always set to the amdb id at construction
        result_id = material.id
        structure_id = structure_id_by_material.get(result_id)
        if structure_id is None:
            # A coupled run always yields a relaxed structure, so the coupled material
            # carries one; guard the pathological degraded case rather than mis-target.
            logger.warning("Skipping run reconstruction for %s: no stamped structure id", result_id)
            continue
        memo: dict[tuple[str, str], str] = {}
        calculation = calculation_outputs.get(result_id)
        record_ids = {} if calculation is None else {"total_energy": cast(str, calculation.id)}
        edges = _rewrite_edges(store, run.outputs, structure_id=structure_id, record_ids=record_ids, memo=memo)
        # _couple_runs already bound every coupled material's run in the ledger
        # (_resolve_run_binding); look it up rather than re-deriving it here.
        run_ledger_id = ledger.lookup(_run_key(result_id))
        assert run_ledger_id is not None  # guaranteed present for a coupled material
        store.save(
            Run(
                workflow_declaration_uri=run.workflow_declaration_uri,
                # No sub-workflows in this deployment, so an artifact edge would only
                # ever duplicate the matching output edge; restore by passing this same
                # rewritten `edges` tuple to `artifacts=` too (Run.artifacts itself is
                # untouched -- see the docstring).
                artifacts=(),
                outputs=edges,
                source_id=run.source_id,
                last_modified=run.last_modified,
                id=run_ledger_id,
            )
        )
        runs += 1
        for product in item.products:
            store.save(
                ProductLink(
                    product.source_type,
                    # Every product here sources from the output_structure output
                    # (the toml ``product_of`` chain), so the structures-edge guard
                    # in _resolve_edge_id sees that label; the target label is unused
                    # for the record/file types products actually target.
                    _resolve_edge_id(
                        store,
                        "output_structure",
                        product.source_type,
                        product.source_id,
                        structure_id=structure_id,
                        record_ids=record_ids,
                        memo=memo,
                    ),
                    product.target_type,
                    _resolve_edge_id(
                        store,
                        product.label,
                        product.target_type,
                        product.target_id,
                        structure_id=structure_id,
                        record_ids=record_ids,
                        memo=memo,
                    ),
                    product.label,
                    product.workflow_declaration_uri,
                )
            )
            products += 1
    logger.info("Saved %d reconstructed runs and %d product links with store-resolvable edges", runs, products)
    return runs, products


def build_store(
    target: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    ledger_path: str | os.PathLike[str] | None = None,
    details_dir: str | os.PathLike[str] | None = None,
    runs_dir: str | os.PathLike[str] | None = None,
    legacy: bool = False,
    initialize_ledger: bool = False,
    timings: MutableMapping[str, float] | None = None,
) -> Path:
    """Build a fresh store next to ``target`` and atomically replace it.

    The caller never sees a partially written target: the DuckDB connection is
    disposed before :func:`os.replace` commits the completed temporary file.

    A non-legacy build allocates every result, structure, reference, run, record and
    file id from the sealed id ledger (see :func:`resolve_ledger_path`), so the ids
    are stable across rebuilds; the ledger is opened FIRST (before source loading and
    coupling) and the ``results`` family that mints the served material ids
    ``anyt.am-1-N`` is seeded once, in screening-row order, from the rows' magndata
    keys. The screening CSVs read from *data_dir* (mounted, untracked); the ledger
    lives on the untracked data volume too (run<->material coupling is expressed as
    ledger bindings, not a coupling document -- see the run-coupling-ledger-bindings
    design). The ledger is signed with the operator identity as an audit record
    (``IdLedger.open`` logs who signed each reopened ledger and refuses a tampered
    one; git history is the tamper witness). The build refuses to run without a
    signing seed.

    A missing ledger file is refused (:func:`_open_ledger`), not silently created,
    UNLESS *initialize_ledger* is set: that is the once-per-deployment ceremony (akin
    to the results-family seeding) for a genuine first-time deployment. Every other
    build -- in particular one that only *thinks* the ledger is missing because of a
    wrong path, an un-restored backup, or a fresh checkout that never got the
    untracked data volume -- must never silently re-mint every public id.

    When ``timings`` is supplied it is populated with the wall-clock seconds of
    the ``load`` (source parsing), ``write`` (the bulk-ingest context) and
    ``finalize`` (dispose plus atomic replace) phases, and the ``total``.
    """
    total_started = time.perf_counter()
    resolved_target = resolve_store_path(target)
    source_dir = resolve_data_dir(data_dir)
    resolved_ledger_path = None if legacy else resolve_ledger_path(ledger_path)
    resolved_details_dir = resolve_details_dir(details_dir)
    resolved_runs_dir = resolve_runs_dir(runs_dir)
    load_started = time.perf_counter()
    items: list[Any] = []
    observations: tuple[_RunObservation, ...] = ()
    coupled: dict[str, _RunObservation] = {}
    coupling_counts: dict[str, int] = {}
    database: Backend | None = None
    ledger: IdLedger | None = None
    temporary_path: Path | None = None
    try:
        # The sealed ledger allocates every result/structure/reference/run/record/file id
        # from stable amdb source keys, so the store keeps its ids across rebuilds. It is
        # opened FIRST so the results-family seeding (one-time) and the per-row result-id
        # resolution happen before coupling and source loading -- both of which derive
        # amdb ids from the ledger, not the screening CSV's AMDBId column. The legacy
        # raw-storage build has no id-managed families and never opens the ledger.
        result_ids: dict[str, str] | None = None
        if not legacy:
            assert resolved_ledger_path is not None  # non-legacy always resolves the ledger path
            # Snapshot the ledger as it stands BEFORE the build touches it. The one-time
            # results seeding pre-validates the whole screening table before its first
            # assign, but opening the ledger already extends its base map (the added
            # ``results`` family), which a reseal would stamp. So on a seeding abort the
            # pre-seed bytes are restored (or a freshly created ledger removed): the
            # seeding is all-or-nothing and a failed seed leaves the committed file
            # byte-identical. A failure AFTER a successful seed follows the normal
            # append-only persistence (the complete seed is durable).
            ledger_before = resolved_ledger_path.read_bytes() if resolved_ledger_path.exists() else None
            ledger = _open_ledger(resolved_ledger_path, create_if_missing=initialize_ledger)
            try:
                result_ids = _result_ids(ledger, _load_csv_rows(source_dir / SCREENING_RESULTS_FILENAME, delimiter=";"))
            except BaseException:
                ledger.close()  # releases the lock; may reseal the added base map
                ledger = None
                if ledger_before is None:
                    resolved_ledger_path.unlink(missing_ok=True)
                else:
                    resolved_ledger_path.write_bytes(ledger_before)
                raise
            from httk.workflow.compat.v1 import collect_finished_tree

            if resolved_runs_dir.is_dir():
                items = list(
                    collect_finished_tree(
                        resolved_runs_dir,
                        workflow_dir=Path(__file__).resolve().parents[2] / "workflows" / "scf_httk_v1",
                    )
                )
            else:
                logger.warning("No v1 runs directory at %s; building the partial tabular store", resolved_runs_dir)
            observations = _run_observations(items)
            coupled, coupling_counts = _couple_runs(
                source_dir,
                observations,
                details_dir=resolved_details_dir,
                ledger=ledger,
                runs_root=resolved_runs_dir,
                collected=len(items),
                result_ids=result_ids,
            )
        materials = _load_source_materials(
            source_dir,
            details_dir=resolved_details_dir,
            legacy_structures=legacy,
            result_ids=result_ids,
        )
        if coupled:
            materials = tuple(
                replace(material, structure=_material_structure_record(coupled[material.id].structure.unwrap()))
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

            def _with_details_fallback(material: AltermagnetScreeningResult) -> AltermagnetScreeningResult:
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
        created_database = Backend.duckdb(temporary_path)
        database = created_database
        store = SqlStore(
            created_database,
            entry_records={} if legacy else _entry_records_layout(),
            # No id-minting scheme: every id-managed family carries an explicit ledger id
            # (materials anyt.am-1-N and references/structures/runs/records/files from the
            # ledger); a save that reaches the store without one is a loud ValueError, not
            # a silent fallthrough to store minting.
            entry_ids=None,
        )
        reference_id_by_doi = _reference_ids_by_doi(materials, ledger)
        # The stamped structure mains (deduped by content) and the result->structure id
        # map. Legacy stores configure no families (raw storage), so they keep the
        # nested-structure-only layout and stamp no structure ids/mains.
        structure_id_by_material: dict[str, str] = {}
        structure_mains: dict[str, UnitcellStructureRecord] = {}
        screening_records: dict[str, ScreeningResultRecord] = {}
        calculation_outputs: dict[str, CalculationOutputRecord] = {}
        if not legacy:
            assert ledger is not None  # non-legacy always opens the ledger
            structure_id_by_material, structure_mains = _structure_mains(materials, ledger)
            screening_records, calculation_outputs = _record_mains(materials, coupled, ledger)
        # Stamp the densely enumerated reference ids AND structure id on the list ONCE, and
        # repoint each result's ``structure``/record references at their canonical stamped
        # mains, so both the bulk save and the alternative-cell derivation / provenance
        # retarget (which replace()/read off these materials) carry them; otherwise
        # alternatives would serve empty references and results empty relationships. The
        # run id is stamped from the ledger too (``ledger`` is None on the legacy path,
        # which stamps every material's run id None -- legacy couples no runs either).
        materials = tuple(
            _stamp_material(
                material,
                reference_id_by_doi,
                structure_id_by_material,
                structure_mains,
                screening_records,
                calculation_outputs,
                ledger=ledger,
            )
            for material in materials
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
                # One run backs one material (enforced in _couple_runs), so the
                # coupled items are already distinct. Only the outputs are bulk-saved
                # here (each stamped with its explicit ledger id, keyed by amdb id and
                # output role/locator); the runs and product links are rebuilt with
                # store-resolvable edges and saved post-bulk (they need those ids).
                assert ledger is not None  # non-legacy always opens the ledger
                for amdb_id, observation in coupled.items():
                    item = observation.item
                    if getattr(item, "missing_collector", None) is not None:
                        continue
                    for value in item.outputs.values():
                        # Only FILE outputs are stored verbatim. The relaxed-structure
                        # output is served as the stamped structure main (from the
                        # material's final, possibly details-fallback structure), and the
                        # one-value total-energy DataRecord as the typed
                        # CalculationOutputRecord saved below; both retarget their run
                        # edges through the build's maps, not through this output.
                        if not isinstance(value, File):
                            continue
                        entry_id = ledger.assign(_file_key(amdb_id, value.url), "files")
                        # ponytail: content-identical outputs across amdb ids get distinct
                        # ledger ids, and the bulk merger compares the id metadata of a
                        # content-dedup hit and RAISES EntryMetadataConflictError, so such a
                        # collision fails the build loudly rather than silently picking a
                        # winner. Upgrade to smallest-key ownership + alias (as for
                        # structures) if two materials ever legitimately share an output.
                        bulk.save(replace(cast(Any, value), id=entry_id))
                # Structure and records mains (stamped ids, deduped by content) saved BEFORE
                # the result rows, so each result's nested references dedup by content id
                # onto their canonical stamped mains (identical identity-excluded metadata).
                for main in structure_mains.values():
                    bulk.save(main)
                for screening_main in screening_records.values():
                    bulk.save(screening_main)
                for calculation_main in calculation_outputs.values():
                    bulk.save(calculation_main)
            for material in materials:
                bulk.save(material)
            # One row per reference id: two DOIs that differ only in case collapse onto
            # one ledger id, so save the row once (keyed by id) to avoid a same-id,
            # different-content conflict. Dense (in-memory) ids are distinct, so this is
            # a no-op there.
            for rid, doi in {rid: doi for doi, rid in reference_id_by_doi.items()}.items():
                bulk.save(AltermagnetReferenceRecord(rid, doi, id=rid))
        if not legacy:
            # Alternatives and the store-resolvable replacement runs (with their
            # rewritten product links) are saved after the mains-only bulk context
            # finalizes: bulk ingest refuses non-mains, and the runs need the outputs'
            # ids the bulk pass just saved.
            _save_alternative_cells(store, materials, structure_id_by_material)
            assert ledger is not None  # non-legacy always opens the ledger
            _save_reconstructed_runs(store, materials, coupled, structure_id_by_material, calculation_outputs, ledger)
        write_elapsed = time.perf_counter() - write_started
        finalize_started = time.perf_counter()
        # Reseal the ledger (no-op when nothing was assigned) BEFORE committing the
        # store: an id recorded in the store but not the ledger would re-mint on the
        # next build, so the ledger must be durable first.
        if ledger is not None:
            ledger.close()
        created_database.dispose()
        database = None
        os.replace(temporary_path, resolved_target)
        finalize_elapsed = time.perf_counter() - finalize_started
    except BaseException:
        if database is not None:
            database.dispose()
        # Release the ledger lock (and persist any assigns already made -- an
        # append-only allocator keeps orphaned ids across a failed build; the results
        # seeding is all-or-nothing before the first assign, so this never seals a
        # partial seed).
        if ledger is not None:
            ledger.close()
        if temporary_path is not None:
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
        # Serving is pure reads; opening read-only keeps a store on read-only
        # media (or a write-protected deployment) servable instead of tripping
        # the broad fallback below with a write-lock/WAL permission error.
        opened_database = Backend.duckdb(store_path, read_only=True)
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
        material = searcher.variable(AltermagnetScreeningResult)
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
    except (OSError, ValueError, SchemaError, SQLAlchemyError, StorageLayoutUpgradeRequiredError) as error:
        logger.warning("Prebuilt store %s is unusable: %s", store_path, error)
        if database is not None:
            database.dispose()
        return None
    except BaseException as error:
        if database is not None:
            try:
                database.dispose()
            except BaseException as cleanup_error:
                error.add_note(f"Additional material-store cleanup failure: {cleanup_error!r}")
        raise


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
            entry_ids=EntryIdScheme("anyt.am", "1", type_in_base=True),
        )
        reference_id_by_doi = _reference_ids_by_doi(materials)
        structure_id_by_material, structure_mains = _structure_mains(materials)
        # No ledger and no coupled runs here, so no records family is seeded: the
        # in-memory results carry no ``_httk_records`` references (documented degradation,
        # same as the relationships the in-memory/--validate path already omits).
        materials = tuple(
            _stamp_material(material, reference_id_by_doi, structure_id_by_material, structure_mains, {}, {})
            for material in materials
        )
        # Bulk ingestion creates the tables and their indexes itself. Structure mains
        # (stamped ids, deduped by content) are seeded BEFORE the results so each result's
        # nested reference dedups by content id onto its canonical stamped structure main;
        # without them the injected ``structures`` relationship would serve empty.
        with store.bulk_ingest() as bulk:
            for main in structure_mains.values():
                bulk.save(main)
            for material in materials:
                bulk.save(material)
            for doi, rid in reference_id_by_doi.items():
                bulk.save(AltermagnetReferenceRecord(rid, doi, id=rid))
        return OpenedMaterialStore(
            opened_database,
            store,
            len(materials),
            mode="memory",
            revision=_source_revision(source_dir, details_dir=resolved_details_dir),
            source_path=source_dir,
        )
    except (OSError, ValueError, SchemaError, SQLAlchemyError, StorageLayoutUpgradeRequiredError) as error:
        logger.warning("In-memory material store is unavailable: %s", error)
        if database is not None:
            database.dispose()
        return None
    except BaseException as error:
        if database is not None:
            try:
                database.dispose()
            except BaseException as cleanup_error:
                error.add_note(f"Additional material-store cleanup failure: {cleanup_error!r}")
        raise


def open_material_store(
    path: str | os.PathLike[str] | None = None,
    *,
    data_dir: str | os.PathLike[str] | None = None,
    details_dir: str | os.PathLike[str] | None = None,
    require_prebuilt: bool = False,
) -> OpenedMaterialStore | None:
    """Open the prebuilt store, or seed memory unless ``require_prebuilt`` is set."""

    persistent = open_prebuilt_store(path)
    if persistent is not None:
        return persistent
    if require_prebuilt:
        raise RuntimeError("a usable prebuilt material store is required; run `make build_store` and deploy it")
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
