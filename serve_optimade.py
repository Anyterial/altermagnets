#!/usr/bin/env python3
"""Serve the altermagnets dataset over OPTIMADE.

This is a thin site-level entry point: it opens the prebuilt DuckDB store (or
the SQLite fallback seeded from ``data/tables/``) and serves its registered
``structures`` and ``references`` families directly through the generic
*httk-serve* OPTIMADE engine. It does not enumerate the store into providers or
copy records into an in-memory serving dataset.

* crystal structures (with their VASP z-axis site moments) come from the shared
  material store built by ``material_store``, which ingests the finished httk v1
  run tree and takes each material's relaxed structure and OUTCAR moments from
  the run named by its ``raw_path`` coupling row, falling back to the
  per-material ``CONTCAR.bz2`` + ``MAGN.bz2`` detail assets;
* stored-property projections provide auto-derived composition fields, custom
  ``_anyterial_`` and ``_httk_`` properties, and null structure-less entries;
* curated custom property definitions are loaded verbatim from the schema
  submodules, while the deployment-local figure property is generated in an
  unpublished ad-hoc namespace.

Run ``python serve_optimade.py`` to serve. ``--validate`` deliberately uses the
compatibility provider projection to validate every assembled response record.
"""

import argparse
import logging
import sys
from collections.abc import Iterable, Iterator, Mapping, MutableMapping, Sequence
from dataclasses import replace as replace_dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FUNCTIONS_ROOT = ROOT / "src" / "functions"
if str(FUNCTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_ROOT))

import material_store
from httk.atomistic import StructureEntryProvider
from httk.core import PropertyDefinition, RelatedEntry, report
from httk.serve.optimade import adapter_from_stores
from httk.serve.optimade.model import ResultRow
from httk.serve.web.runtime.devserver import run_dev_server
from httk.store import ReferenceEntryProvider, validate_record
from httk.store.db import StoredEntrySource
from optimade_service import build_service_app, figure_file_is_servable

logger = report.context_logger(logging.getLogger("httk.altermagnets.serve_optimade"), "altermagnets")

#: The base URL under which the altermagnets custom property ``$id``s are published.
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
}
HTTK_DEFINITION_PATHS = {
    "_httk_dft_band_gap": "electronic/dft_band_gap.json",
    "_httk_magnetic_space_group_bns": "magnetism/magnetic_space_group_bns.json",
    "_httk_magndata_ids": "magnetism/magndata_ids.json",
}

ANYTERIAL_DEFS_DIR = ROOT / "dependencies/submodules/anyterial-schemas/defs/v0.1/properties/altermagnets"
HTTK_DEFS_DIR = ROOT / "dependencies/submodules/httk-schemas/defs/v0.1/properties"

DEFAULT_PUBLIC_BASE_URL = "http://127.0.0.1:8081"
FIGURE_KEYS = ("band", "structure", "bz")
SCREENING_PHASE_LABELS = {
    "AM": "altermagnet",
    "FiM": "compensated ferrimagnet",
}
SORTABLE_PROPERTIES = {
    "structures": (
        "id",
        "_anyterial_max_spin_splitting",
        "_anyterial_avg_spin_splitting",
        "_anyterial_spin_splitting_fraction",
        "_httk_dft_band_gap",
        "_anyterial_min_crustal_abundance",
    )
}

# In-memory fallback stores kept alive because harvested record-backed views read
# from them lazily for the process lifetime; see build_dataset.
_RETAINED_STORES: list[Any] = []

_PUBLIC_ID = "_httk_custom_public_id"
_REFERENCE_IDS = "_httk_custom_reference_ids"
_INTERNAL_STORE_PROPERTIES = {_PUBLIC_ID, _REFERENCE_IDS}


class _LiveResults:
    """Preserve store result metadata while enriching only the returned page."""

    def __init__(self, source: Any, rows: Sequence[ResultRow]) -> None:
        self._source = source
        self._rows = tuple(rows)

    @property
    def more_data_available(self) -> bool:
        return bool(self._source.more_data_available)

    def count(self) -> int:
        return int(self._source.count())

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(self._rows)


def _rewrite_id_filter(node: Any) -> Any:
    """Route public OPTIMADE id predicates to AMDB's durable public-id field."""
    if isinstance(node, tuple):
        if len(node) == 2 and node == ("Identifier", "id"):
            return ("Identifier", _PUBLIC_ID)
        return tuple(_rewrite_id_filter(item) for item in node)
    return node


def _absolute_figure_urls(value: object, public_base_url: str) -> object:
    if not isinstance(value, list):
        return value
    figures: list[object] = []
    for item in value:
        if not isinstance(item, Mapping):
            figures.append(item)
            continue
        projected = dict(item)
        for name in ("url", "dark_url"):
            url = projected.get(name)
            if isinstance(url, str) and url.startswith("/"):
                projected[name] = public_base_url + url
        figures.append(projected)
    return figures


def _public_store_schema(schema: Any) -> Any:
    """Hide storage-only projections from the published OPTIMADE schema."""

    def public_names(names: Sequence[str]) -> tuple[str, ...]:
        return tuple(name for name in names if name not in _INTERNAL_STORE_PROPERTIES)

    entry_info = {
        entry: {
            **info,
            "properties": {
                name: value for name, value in info["properties"].items() if name not in _INTERNAL_STORE_PROPERTIES
            },
        }
        for entry, info in schema.entry_info.items()
    }
    return replace_dataclass(
        schema,
        entry_info=entry_info,
        properties_by_entry={entry: public_names(names) for entry, names in schema.properties_by_entry.items()},
        default_response_fields={entry: public_names(names) for entry, names in schema.default_response_fields.items()},
        required_response_fields={
            entry: public_names(names) for entry, names in schema.required_response_fields.items()
        },
        unknown_response_fields={entry: public_names(names) for entry, names in schema.unknown_response_fields.items()},
        sortable_response_fields={
            entry: public_names(names) for entry, names in schema.sortable_response_fields.items()
        },
        property_definitions={
            entry: {name: value for name, value in definitions.items() if name not in _INTERNAL_STORE_PROPERTIES}
            for entry, definitions in schema.property_definitions.items()
        },
    )


class AltermagnetStoreAdapter:
    """Thin AMDB envelope policy over the generic lazy store adapter.

    Filtering, sorting, counting, pagination, and hydration remain in the
    underlying store. This layer only restores deployment-owned public IDs,
    relationships, and absolute figure URLs on the bounded returned page.
    """

    def __init__(self, store: Any, public_base_url: str) -> None:
        self._adapter = adapter_from_stores(
            (
                StoredEntrySource(store, material_store.AltermagnetStructureEntry, "amdb-structures"),
                StoredEntrySource(store, material_store.AltermagnetReferenceEntry, "amdb-references"),
            ),
            sortable=SORTABLE_PROPERTIES,
        )
        self._public_base_url = public_base_url.rstrip("/")
        self.schema = _public_store_schema(self._adapter.schema)

    def query_function(self):
        query = self._adapter.query_function()

        def execute(
            entries: list[str],
            response_fields: list[str],
            unknown_response_fields: list[str],
            page_limit: int,
            page_offset: int,
            filter_ast: Any = None,
            *,
            as_of: int | None = None,
            sort: Sequence[tuple[str, bool]] | None = None,
            debug: bool = False,
        ) -> _LiveResults:
            entry_type = entries[0] if len(entries) == 1 else ""
            remapped = entry_type in {"structures", "references"}
            requested = set(response_fields)
            fields = list(response_fields)
            if remapped and _PUBLIC_ID not in fields:
                fields.append(_PUBLIC_ID)
            if entry_type == "structures" and _REFERENCE_IDS not in fields:
                fields.append(_REFERENCE_IDS)
            store_sort = tuple(
                (_PUBLIC_ID if name == "id" and remapped else name, descending) for name, descending in (sort or ())
            )
            source = query(
                entries,
                fields,
                unknown_response_fields,
                page_limit,
                page_offset,
                _rewrite_id_filter(filter_ast) if remapped else filter_ast,
                as_of=as_of,
                sort=store_sort,
                debug=debug,
            )
            rows: list[ResultRow] = []
            for row in source:
                values = dict(row.values)
                public_id = values.get(_PUBLIC_ID)
                if remapped and isinstance(public_id, str):
                    values["id"] = public_id
                reference_ids = values.pop(_REFERENCE_IDS, None)
                if _PUBLIC_ID not in requested:
                    values.pop(_PUBLIC_ID, None)
                if "_httk_custom_figures" in values:
                    values["_httk_custom_figures"] = _absolute_figure_urls(
                        values["_httk_custom_figures"], self._public_base_url
                    )
                relationships = dict(row.relationships)
                if entry_type == "structures" and isinstance(reference_ids, list) and reference_ids:
                    relationships["references"] = [{"id": value} for value in reference_ids]
                rows.append(ResultRow(values, relationships, dict(row.property_metadata)))
            return _LiveResults(source, rows)

        return execute


# --- dataset assembly ----------------------------------------------------------


class AltermagnetStructureProvider(StructureEntryProvider):
    """A ``structures`` provider that also links each material to its reference entries."""

    def __init__(
        self,
        entries: Mapping[str, Any],
        *,
        extra_definitions: Mapping[str, PropertyDefinition],
        properties: Mapping[str, Mapping[str, Any]],
        relationships: Mapping[str, Mapping[str, tuple[str, ...]]],
    ) -> None:
        super().__init__(entries, extra_definitions=extra_definitions, properties=properties)
        # httk-core states relationships as a flat tuple of RelatedEntry per
        # entry id; grouping by related type is the serving layer's job.
        self._material_relationships = {
            str(entry_id): tuple(
                RelatedEntry(entry_type=related, id=related_id)
                for related, ids in related_map.items()
                for related_id in ids
            )
            for entry_id, related_map in relationships.items()
        }

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        if entry_type != "structures":
            return {}
        return self._material_relationships


def load_schema_definitions() -> dict[str, PropertyDefinition]:
    """Load curated definitions and generate deployment-local definitions."""
    definitions = dict(material_store._optimade_definitions())
    definitions.pop("_httk_custom_public_id")
    definitions.pop("_httk_custom_reference_ids")
    return definitions


def _nullable_list(values: Iterable[str]) -> list[str] | None:
    """Return an ordinary nullable JSON list while preserving source order."""
    result = list(values)
    return result or None


def _screening_phase(value: str | None) -> str | None:
    """Map the store's short phase vocabulary to the existing scalar contract."""
    return None if value is None else SCREENING_PHASE_LABELS.get(value, value)


def _variant_payload(magndata_id: str, variant: Any) -> dict[str, Any]:
    """Project one stored symmetry variant into the detail widget contract."""
    return {
        "magndata_id": magndata_id,
        "source": variant.source_kind,
        "formula": variant.formula or None,
        "symprec": variant.symprec,
        "phases": _nullable_list(variant.magnetic_phases),
        "wave_classes": _nullable_list(variant.wave_classes),
        "bns": _nullable_list(variant.bns_labels),
        "bns_latex": _nullable_list(variant.bns_labels_latex),
        "bns_mcif": _nullable_list(variant.bns_mcif_labels),
        "bns_mcif_latex": _nullable_list(variant.bns_mcif_labels_latex),
        "effective_bns": _nullable_list(variant.effective_bns_labels),
        "effective_bns_latex": _nullable_list(variant.effective_bns_labels_latex),
        "parent_spacegroups": _nullable_list(variant.parent_spacegroups),
        "parent_spacegroups_latex": _nullable_list(variant.parent_spacegroups_latex),
        "connecting_elements": _nullable_list(variant.connecting_elements),
        "connecting_elements_latex": _nullable_list(variant.connecting_elements_latex),
        "g_laue_classes": _nullable_list(variant.g_laue_classes),
        "h_laue_classes": _nullable_list(variant.h_laue_classes),
        "spin_angle_mismatch": variant.spin_angle_mismatch,
        "spin_length_mismatch": variant.spin_length_mismatch,
        "reference_dois": _nullable_list(variant.reference_dois),
        "warnings": _nullable_list(variant.warnings),
        "notes": _nullable_list(variant.notes),
    }


def _figure_payload(record: Any, public_base_url: str) -> list[dict[str, Any]]:
    """Project stored figure filenames into absolute, route-ready metadata."""
    stored = {figure.key: figure for figure in record.figures}
    figures: list[dict[str, Any]] = []
    for key in FIGURE_KEYS:
        figure = stored.get(key)
        if figure is None:
            figures.append({"key": key, "url": None, "dark_url": None, "media_type": None, "available": False})
            continue
        base = f"{public_base_url}/extensions/figures/{record.id}"
        light_name = figure.light.name
        light_servable = figure_file_is_servable(figure.light.size)
        dark_url = None
        if light_servable and figure.dark is not None and figure_file_is_servable(figure.dark.size):
            dark_url = f"{base}/{figure.dark.name}"
        elif light_servable and figure.dark is None and figure.light.media_type == "image/svg+xml":
            # The dark SVG route/variant is generated by the Phase-2b figure service.
            dark_url = f"{base}/dark--{light_name}"
        figures.append(
            {
                "key": key,
                "url": f"{base}/{light_name}" if light_servable else None,
                "dark_url": dark_url,
                "media_type": figure.light.media_type if light_servable else None,
                "available": light_servable,
            }
        )
    return figures


def _material_properties(record: Any, public_base_url: str) -> dict[str, Any]:
    """Project one :class:`MaterialRecord` into served custom properties."""
    variants = [_variant_payload(link.record.id, variant) for link in record.links for variant in link.record.variants]
    return {
        "_anyterial_formula": record.formula or None,
        "_anyterial_elements": sorted(record.elements) or None,
        "_anyterial_space_group": record.space_group or None,
        "_anyterial_space_group_search": record.space_group_search or None,
        "_anyterial_classification": record.classification or None,
        "_anyterial_magnetic_phases": _nullable_list(record.magnetic_phases),
        "_anyterial_wave_classes": _nullable_list(record.wave_classes),
        "_anyterial_parent_spacegroups": _nullable_list(record.parent_spacegroups),
        "_anyterial_icsd_ids": _nullable_list(record.icsd_ids),
        "_anyterial_search_text": record.search_text or None,
        # An unresolved linked MAGNDATA id deliberately produces []: the detail
        # page renders its no-symmetry-record placeholder from that state.
        "_anyterial_magndata_variants": variants,
        "_httk_custom_figures": _figure_payload(record, public_base_url),
        "_anyterial_max_spin_splitting": record.max_ss,
        "_anyterial_avg_spin_splitting": record.avg_ss,
        "_anyterial_spin_splitting_fraction": (None if record.fdelta_pct is None else record.fdelta_pct / 100.0),
        "_httk_dft_band_gap": record.bandgap,
        "_anyterial_electronic_type": record.electronic_type,
        "_anyterial_min_crustal_abundance": record.min_abund_ppm,
        "_anyterial_magnetic_phase": _screening_phase(record.magnetic_phases[0]) if record.magnetic_phases else None,
        "_anyterial_wave_class": record.wave_classes[0] if record.wave_classes else None,
        "_httk_magnetic_space_group_bns": (variants[0]["bns"][0] if variants and variants[0]["bns"] else None),
        "_httk_magndata_ids": [link.record.id for link in record.links] or None,
    }


def build_dataset(
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    *,
    records_out: MutableMapping[str, Any] | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, tuple[str, ...]]],
    dict[str, dict[str, Any]],
]:
    """Assemble the structures, per-material properties, relationships, and references."""
    data_dir = material_store.resolve_data_dir()
    details_dir = material_store.resolve_details_dir()
    public_base_url = public_base_url.rstrip("/")

    stored_structures: dict[str, Any] = {}
    material_records: dict[str, Any] = {}
    opened = material_store.open_material_store(data_dir=data_dir, details_dir=details_dir)
    if opened is None:
        logger.warning("No material store could be opened; every structure will serve null")
    else:
        owned_store = {"materials_database": opened.database}
        try:
            searcher = opened.store.searcher()
            material = searcher.variable(material_store.MaterialRecord)
            for result in searcher.results(material=material):
                record = result["material"]
                material_records[record.id] = record
                structure = material_store.material_structure(record)
                if structure is not None:
                    stored_structures[record.id] = structure
        finally:
            # Record-backed views read lazily, so the store must outlive them. A
            # file-backed engine reconnects after dispose, but disposing the
            # in-memory fallback destroys the seeded database under the views —
            # retain it for the process lifetime instead.
            if opened.mode != "memory":
                material_store.cleanup_material_store(owned_store)
            else:
                _RETAINED_STORES.append(opened)
        logger.info("%d structures from the %s material store", len(stored_structures), opened.mode)
        if not stored_structures:
            logger.warning(
                "The material store holds no structures; every structure will serve null "
                "(was the store built with the detail tree and httk-io available?)"
            )

    # References: dedupe DOIs across the normalized material records in first-seen order.
    reference_id_by_doi: dict[str, str] = {}
    references: dict[str, dict[str, Any]] = {}
    for record in material_records.values():
        for doi in record.dois:
            if doi not in reference_id_by_doi:
                reference_id = f"anyt:ref-{len(references) + 1:04d}"
                reference_id_by_doi[doi] = reference_id
                references[reference_id] = {"doi": doi}

    structures: dict[str, Any] = {}
    properties: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, tuple[str, ...]]] = {}

    for material_id, record in material_records.items():
        properties[material_id] = _material_properties(record, public_base_url)
        structures[material_id] = stored_structures.get(material_id)

        reference_ids: list[str] = []
        for doi in record.dois:
            matched = reference_id_by_doi.get(doi)
            if matched is not None and matched not in reference_ids:
                reference_ids.append(matched)
        if reference_ids:
            relationships[material_id] = {"references": tuple(reference_ids)}

    if records_out is not None:
        records_out.update(material_records)
    return structures, properties, relationships, references


def build_providers(
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    *,
    material_records: MutableMapping[str, Any] | None = None,
) -> list[Any]:
    """Register the ``_anyterial_`` prefix and build the structures + references providers."""
    structures, properties, relationships, references = build_dataset(
        public_base_url=public_base_url, records_out=material_records
    )
    structure_provider = AltermagnetStructureProvider(
        structures,
        extra_definitions=load_schema_definitions(),
        properties=properties,
        relationships=relationships,
    )
    reference_provider = ReferenceEntryProvider(references)
    return [structure_provider, reference_provider]


# --- validation ----------------------------------------------------------------


def _validation_record(
    record: Mapping[str, Any],
    columns: Mapping[str, str],
    definition: Any,
    stripped_nulls: dict[str, int],
) -> dict[str, Any]:
    """Rewrite a served column-keyed record to property-name keys.

    Explicit nulls are retained when the definition permits them. A null is
    omitted only when the definition is non-nullable and the validator cannot
    express the provider's existing null-valued column; each omission is
    counted and reported by :func:`run_validation`.
    """
    result: dict[str, Any] = {}
    properties = definition.properties
    for name, column in columns.items():
        if column not in record:
            continue
        value = record[column]
        if value is None and name not in ("id", "type") and not properties[name].nullable:
            stripped_nulls[name] = stripped_nulls.get(name, 0) + 1
            continue
        result[name] = value
    return result


def run_validation(providers: list[Any]) -> int:
    """Validate every assembled record against its definition; return a process exit code."""
    total = 0
    failures = 0
    stripped_nulls: dict[str, int] = {}
    for provider in providers:
        for entry_type, definition in provider.entry_types().items():
            columns = provider.property_keys(entry_type)
            entry_count = 0
            for record in provider.records(entry_type):
                entry_count += 1
                total += 1
                candidate = _validation_record(record, columns, definition, stripped_nulls)
                try:
                    validate_record(definition, candidate)
                except Exception as exc:
                    failures += 1
                    print(f"INVALID {entry_type} {candidate.get('id')!r}: {exc}", file=sys.stderr)
            if entry_count == 0:
                failures += 1
                print(f"INVALID {entry_type}: no records were served", file=sys.stderr)
    if stripped_nulls:
        details = ", ".join(f"{name} ({count})" for name, count in sorted(stripped_nulls.items()))
        print(f"omitted null values for non-nullable definitions: {details}", file=sys.stderr)
    if total == 0:
        failures += 1
        print("INVALID: no records were served by any provider", file=sys.stderr)
    print(f"validated {total} record(s) across {len(providers)} provider(s): {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the altermagnets dataset over OPTIMADE.")
    parser.add_argument("--validate", action="store_true", help="validate every record and exit")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind when serving")
    parser.add_argument("--port", type=int, default=8081, help="port to bind when serving")
    parser.add_argument(
        "--public-base-url",
        default=DEFAULT_PUBLIC_BASE_URL,
        help="absolute base URL used in figure metadata",
    )
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=[],
        help="exact browser origin allowed to query OPTIMADE (repeatable)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="report diagnostics on the httk channel (-v info, -vv debug)",
    )
    args = parser.parse_args(argv)
    if args.verbose:
        report.configure_reporting(level="debug" if args.verbose > 1 else "info")

    if args.validate:
        return run_validation(build_providers(public_base_url=args.public_base_url))
    app = build_service_app(
        public_base_url=args.public_base_url,
        cors_origins=args.cors_origin,
    )
    run_dev_server(app=app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
