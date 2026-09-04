"""Dataset assembly and custom-property projection for the altermagnets service."""

import json
import logging
from collections.abc import Iterable, Mapping, MutableMapping
from pathlib import Path
from typing import Any

import material_store
from httk.atomistic import StructureEntryProvider
from httk.core import EntryProvider, EntryTypeDefinition, PropertyDefinition, RelatedEntry, report
from httk.store import DataRecordEntryProvider, FileEntryProvider, ReferenceEntryProvider

from .files import figure_file_is_servable

ROOT = Path(__file__).resolve().parents[1]

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
    "_anyterial_screening_rank": "screening_rank.json",
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

# In-memory fallback stores kept alive because harvested record-backed views read
# from them lazily for the process lifetime; see build_dataset.
_RETAINED_STORES: list[Any] = []


class AltermagnetScreeningResultProvider(EntryProvider):
    """Serve the AMDB main entity (screening results) through the neutral provider contract.

    Used by the ``--validate`` path: it advertises the served
    ``_anyterial_altermagnet_screening_result`` definition and its science/figure/energy
    property values, plus each result's ``structures``/``references`` relationships (for
    the provider-backed serving path). The crystal structure is a separate provider.
    """

    def __init__(
        self,
        properties: Mapping[str, Mapping[str, Any]],
        *,
        relationships: Mapping[str, Mapping[str, tuple[str, ...]]],
    ) -> None:
        self._definition = material_store.AltermagnetScreeningResultEntry.entry_type_definition().served_form()
        self._entry_type = self._definition.name
        self._properties = {str(entry_id): dict(values) for entry_id, values in properties.items()}
        # The served science/figure/energy property names actually produced.
        self._property_names = sorted({name for values in self._properties.values() for name in values})
        # httk-core states relationships as a flat tuple of RelatedEntry per entry id;
        # grouping by related type is the serving layer's job.
        self._result_relationships = {
            str(entry_id): tuple(
                RelatedEntry(entry_type=related, id=related_id)
                for related, ids in related_map.items()
                for related_id in ids
            )
            for entry_id, related_map in relationships.items()
        }

    def _check(self, entry_type: str) -> None:
        if entry_type != self._entry_type:
            raise KeyError(f"AltermagnetScreeningResultProvider serves only {self._entry_type!r}.")

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        return {self._entry_type: self._definition}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        self._check(entry_type)
        keys = {name: name for name in ("id", "type", "immutable_id", "last_modified")}
        keys.update({name: name for name in self._property_names})
        return keys

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        self._check(entry_type)
        return [
            {"id": entry_id, "type": self._entry_type, "immutable_id": None, "last_modified": None, **values}
            for entry_id, values in self._properties.items()
        ]

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        self._check(entry_type)
        return self._result_relationships


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
        base = f"{public_base_url}/extensions/files/{record.id}"
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
    variants = [
        _variant_payload(link.record.id, variant) for link in record.magndata_links for variant in link.record.variants
    ]
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
        # The producing run's reverse StrongLink relationships carry the run identity;
        # this symmetric scalar is the material-level energy the detail page renders.
        # The in-memory/--validate path serves no relationships, so its pages show
        # only this scalar (documented degradation).
        "_httk_custom_total_energy": getattr(record, "total_energy", None),
        "_anyterial_max_spin_splitting": record.max_ss,
        "_anyterial_avg_spin_splitting": record.avg_ss,
        "_anyterial_spin_splitting_fraction": (None if record.fdelta_pct is None else record.fdelta_pct / 100.0),
        "_httk_dft_band_gap": record.bandgap,
        "_anyterial_electronic_type": record.electronic_type,
        "_anyterial_min_crustal_abundance": record.min_abund_ppm,
        "_anyterial_screening_rank": record.screening_rank,
        "_anyterial_magnetic_phase": _screening_phase(record.magnetic_phases[0]) if record.magnetic_phases else None,
        "_anyterial_wave_class": record.wave_classes[0] if record.wave_classes else None,
        "_httk_magnetic_space_group_bns": (variants[0]["bns"][0] if variants and variants[0]["bns"] else None),
        "_httk_magndata_ids": [link.record.id for link in record.magndata_links] or None,
    }


def build_dataset(
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    *,
    records_out: MutableMapping[str, Any] | None = None,
    files_out: MutableMapping[str, Any] | None = None,
    data_records_out: MutableMapping[str, Any] | None = None,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, tuple[str, ...]]],
    dict[str, dict[str, Any]],
]:
    """Assemble the slim structures (by structure id), per-result science properties,
    each result's structures/references relationships, and the references."""
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
            material = searcher.variable(material_store.AltermagnetScreeningResult)
            for result in searcher.results(material=material):
                record = result["material"]
                material_records[record.id] = record
                # Structures are keyed by their stamped id (deduped across results).
                structure = material_store.material_structure(record)
                if structure is not None and record.structure_id is not None:
                    stored_structures[record.structure_id] = structure
            # The runs' file/record outputs (present only in a prebuilt store built with
            # the runs tree) so validation can cover the mounted files/records endpoints.
            if files_out is not None:
                file_searcher = opened.store.searcher()
                file_var = file_searcher.variable(material_store.FileRecord)
                for result in file_searcher.results(file=file_var):
                    file_record = result["file"]
                    files_out[file_record.id] = file_record
            if data_records_out is not None:
                record_searcher = opened.store.searcher()
                record_var = record_searcher.variable(material_store.AltermagnetDataRecord)
                for result in record_searcher.results(record=record_var):
                    data_record = result["record"]
                    data_records_out[data_record.id] = data_record
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
                "(was the store built with the detail tree and httk-atomistic available?)"
            )

    # References: dedupe DOIs across the normalized material records in first-seen order.
    # Ids are read straight off each material's stored ``reference_ids`` (aligned with
    # its ``dois``), the same densely enumerated ids the store path serves.
    reference_id_by_doi: dict[str, str] = {}
    references: dict[str, dict[str, Any]] = {}
    for record in material_records.values():
        for doi, reference_id in zip(record.dois, record.reference_ids, strict=True):
            if doi not in reference_id_by_doi:
                reference_id_by_doi[doi] = reference_id
                references[reference_id] = {"doi": doi}

    properties: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, tuple[str, ...]]] = {}

    for material_id, record in material_records.items():
        properties[material_id] = _material_properties(record, public_base_url)

        related: dict[str, tuple[str, ...]] = {}
        if record.structure_id is not None:
            related["structures"] = (record.structure_id,)
        reference_ids: list[str] = []
        for doi in record.dois:
            matched = reference_id_by_doi.get(doi)
            if matched is not None and matched not in reference_ids:
                reference_ids.append(matched)
        if reference_ids:
            related["references"] = tuple(reference_ids)
        if related:
            relationships[material_id] = related

    if records_out is not None:
        records_out.update(material_records)
    return stored_structures, properties, relationships, references


def build_providers(
    public_base_url: str = DEFAULT_PUBLIC_BASE_URL,
    *,
    material_records: MutableMapping[str, Any] | None = None,
) -> list[Any]:
    """Register the ``_anyterial_`` prefix and build every serving provider for validation.

    Beyond structures and references, this also serves the runs' file and data-record
    outputs (present only in a prebuilt store built with the runs tree) so
    ``validate_optimade`` covers the mounted ``files`` and ``_httk_records`` endpoints.
    Runs are not validated here (there is no cheap in-memory RunEntryProvider round-trip
    from the store's reconstructed runs); that remains a known validation gap.
    """
    files: dict[str, Any] = {}
    data_records: dict[str, Any] = {}
    structures, properties, relationships, references = build_dataset(
        public_base_url=public_base_url,
        records_out=material_records,
        files_out=files,
        data_records_out=data_records,
    )
    # The AMDB main entity (science + figures + energy, with structures/references
    # relationships) and the slim standard structures it references, served separately.
    result_provider = AltermagnetScreeningResultProvider(properties, relationships=relationships)
    structure_provider = StructureEntryProvider(structures)
    providers: list[Any] = [result_provider, structure_provider, ReferenceEntryProvider(references)]
    # Only a runs-backed prebuilt store holds files/records; skip empty providers so a
    # store without them validates rather than tripping the "no records served" guard.
    if files:
        providers.append(FileEntryProvider(files))
    if data_records:
        providers.append(DataRecordEntryProvider(data_records, definitions=_record_definitions(data_records)))
    return providers


def _record_definitions(data_records: Mapping[str, Any]) -> dict[str, PropertyDefinition]:
    """Resolve each stored data record's served property definition from httk-schemas.

    The record's ``definition_id`` IRI ``.../properties/<relpath>`` maps to
    ``<relpath>.json`` under the httk-schemas tree, keyed by the record's served
    property name for :class:`DataRecordEntryProvider`.

    :param data_records: The stored data records keyed by id.
    :return: The served-property-name to definition map for the records present.
    """
    marker = "/properties/"
    definitions: dict[str, PropertyDefinition] = {}
    for record in data_records.values():
        if record.name in definitions or marker not in record.definition_id:
            continue
        path = HTTK_DEFS_DIR / f"{record.definition_id.split(marker, 1)[1]}.json"
        if path.is_file():
            definitions[record.name] = PropertyDefinition.from_optimade(
                record.name, json.loads(path.read_text(encoding="utf-8"))
            )
    return definitions
