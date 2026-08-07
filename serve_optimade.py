#!/usr/bin/env python3
"""Serve the altermagnets dataset over OPTIMADE.

This is a thin site-level entry point: it reads the three CSV tables shipped
under ``data/tables/``, assembles OPTIMADE ``structures`` and ``references``
records, and serves them through the generic *httk-serve* OPTIMADE engine. All of the
reusable machinery lives in the httk modules:

* crystal structures (with their VASP z-axis site moments) come from the shared
  material store built by ``material_store`` from the per-material
  ``CONTCAR.bz2`` + ``MAGN.bz2`` files;
* the ``structures`` provider (with auto-derived composition fields, custom
  ``_anyterial_`` properties, and null structure-less entries) and the ``references``
  provider come from *httk-atomistic* / *httk-data*;
* the custom property definitions are authored under
  ``optimade/property_definitions/`` and rendered to JSON there.

Run ``python serve_optimade.py`` to serve, or ``--validate`` to validate every
assembled record against its definition and exit non-zero on any failure.
"""

import argparse
import csv
import json
import logging
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
FUNCTIONS_ROOT = ROOT / "src" / "functions"
if str(FUNCTIONS_ROOT) not in sys.path:
    sys.path.insert(0, str(FUNCTIONS_ROOT))

import material_store
from httk.atomistic import StructureEntryProvider
from httk.core import PropertyDefinition, RelatedEntry, register_definition_prefix, report
from httk.data import ReferenceEntryProvider, validate_record
from httk.serve.optimade import adapter_from_providers, serve

logger = report.context_logger(logging.getLogger("httk.altermagnets.serve_optimade"), "altermagnets")

DEFS_JSON = ROOT / "optimade" / "property_definitions" / "json"

#: The base URL under which the altermagnets custom property ``$id``s are minted.
ANYTERIAL_DEFS_BASE = "https://anyterial.se/optimade/defs/properties"

SYMMETRY_CSVS = (material_store.MAGNDATA_COLLINEAR_FILENAME, material_store.MAGNDATA_NONCOLLINEAR_FILENAME)

# In-memory fallback stores kept alive because harvested record-backed views read
# from them lazily for the process lifetime; see build_dataset.
_RETAINED_STORES: list[Any] = []


# --- small parsing helpers -----------------------------------------------------


def _to_float(value: str | None) -> float | None:
    text = (value or "").strip()
    if not text or text == "?":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_nonempty(values: Iterable[str | None]) -> str | None:
    for value in values:
        text = (value or "").strip()
        if text:
            return text
    return None


def strip_latex_bns(raw: str | None) -> str | None:
    """Turn a LaTeX-wrapped BNS symbol into a plain ASCII ``symbol (number)`` string, or None."""
    text = (raw or "").strip()
    if not text or text == "?":
        return None
    text = text.replace("$", "")
    text = re.sub(r"\\overline\{([^}]*)\}", r"-\1", text)
    text = re.sub(r"\\mathrm\{([^}]*)\}", r"\1", text)
    text = text.replace("^{\\prime}", "'").replace("^\\prime", "'").replace("\\prime", "'")
    text = re.sub(r"_\{([^}]*)\}", r"_\1", text)
    text = re.sub(r"\^\{([^}]*)\}", r"^\1", text)
    text = text.replace("{", "").replace("}", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _electronic_type(band_gap: float | None) -> str:
    if band_gap is None:
        return "unknown"
    if band_gap <= 0:
        return "metallic"
    return "semiconducting"


def _read_csv(path: Path, delimiter: str) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


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


def load_property_definitions() -> dict[str, PropertyDefinition]:
    """Load the rendered ``_anyterial_`` property definitions keyed by property name."""
    definitions: dict[str, PropertyDefinition] = {}
    for path in sorted(DEFS_JSON.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        name = document["x-optimade-definition"]["name"]
        definitions[name] = PropertyDefinition.from_optimade(name, document)
    return definitions


def build_dataset() -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, dict[str, tuple[str, ...]]],
    dict[str, dict[str, Any]],
]:
    """Assemble the structures, per-material properties, relationships, and references."""
    data_dir = material_store.resolve_data_dir()
    details_dir = material_store.resolve_details_dir()
    screening = _read_csv(data_dir / material_store.SCREENING_RESULTS_FILENAME, delimiter=";")

    stored_structures: dict[str, Any] = {}
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

    symmetry_by_magndata: dict[str, list[dict[str, str]]] = {}
    symmetry_rows: list[dict[str, str]] = []
    for filename in SYMMETRY_CSVS:
        for row in _read_csv(data_dir / filename, delimiter=","):
            magndata_id = (row.get("MAGNDATAId") or "").strip()
            if magndata_id:
                symmetry_by_magndata.setdefault(magndata_id, []).append(row)
            symmetry_rows.append(row)

    # References: dedupe DOIs across both symmetry tables in first-seen order.
    reference_id_by_doi: dict[str, str] = {}
    references: dict[str, dict[str, Any]] = {}
    for row in symmetry_rows:
        doi = (row.get("ReferenceDOI") or "").strip()
        if not doi or doi == "?" or doi in reference_id_by_doi:
            continue
        reference_id = f"anyt:ref-{len(references) + 1:04d}"
        reference_id_by_doi[doi] = reference_id
        references[reference_id] = {"doi": doi}

    structures: dict[str, Any] = {}
    properties: dict[str, dict[str, Any]] = {}
    relationships: dict[str, dict[str, tuple[str, ...]]] = {}

    for row in screening:
        material_id = (row.get("AMDBId") or "").strip()
        if not material_id:
            continue
        magndata_ids = [part.strip() for part in (row.get("MAGNDATA ID") or "").split(",") if part.strip()]
        linked = [entry for magndata_id in magndata_ids for entry in symmetry_by_magndata.get(magndata_id, [])]

        band_gap = _to_float(row.get("Bandgap"))
        fraction = _to_float(row.get("FdeltaPct"))
        properties[material_id] = {
            "_anyterial_max_spin_splitting": _to_float(row.get("MaxSS")),
            "_anyterial_avg_spin_splitting": _to_float(row.get("AvgSS")),
            "_anyterial_spin_splitting_fraction": None if fraction is None else fraction / 100.0,
            "_anyterial_band_gap": band_gap,
            "_anyterial_electronic_type": _electronic_type(band_gap),
            "_anyterial_min_crustal_abundance": _to_float(row.get("MinAbundPpm")),
            "_anyterial_magnetic_phase": _first_nonempty(entry.get("MagneticPhase") for entry in linked),
            "_anyterial_wave_class": _first_nonempty(entry.get("WaveClassSimple") for entry in linked),
            "_anyterial_magnetic_space_group_bns": _first_nonempty(strip_latex_bns(entry.get("BNS")) for entry in linked),
            "_anyterial_magndata_ids": magndata_ids or None,
        }
        structures[material_id] = stored_structures.get(material_id)

        reference_ids: list[str] = []
        for entry in linked:
            matched = reference_id_by_doi.get((entry.get("ReferenceDOI") or "").strip())
            if matched is not None and matched not in reference_ids:
                reference_ids.append(matched)
        if reference_ids:
            relationships[material_id] = {"references": tuple(reference_ids)}

    return structures, properties, relationships, references


def build_providers() -> list[Any]:
    """Register the ``_anyterial_`` prefix and build the structures + references providers."""
    register_definition_prefix("_anyterial_", ANYTERIAL_DEFS_BASE)
    structures, properties, relationships, references = build_dataset()
    structure_provider = AltermagnetStructureProvider(
        structures,
        extra_definitions=load_property_definitions(),
        properties=properties,
        relationships=relationships,
    )
    reference_provider = ReferenceEntryProvider(references)
    return [structure_provider, reference_provider]


# --- validation ----------------------------------------------------------------


def _validation_record(record: Mapping[str, Any], columns: Mapping[str, str]) -> dict[str, Any]:
    """Rewrite a served record (keyed by column) to a property-name-keyed record for validation.

    ``None`` values are dropped for non-``id``/``type`` properties: a nullable
    property served as null is valid OPTIMADE, but a null would spuriously fail
    the jsonschema ``enum`` constraint (enums are authored null-free by
    convention), so absent-value handling is the faithful choice.
    """
    result: dict[str, Any] = {}
    for name, column in columns.items():
        value = record.get(column)
        if value is None and name not in ("id", "type"):
            continue
        result[name] = value
    return result


def run_validation(providers: list[Any]) -> int:
    """Validate every assembled record against its definition; return a process exit code."""
    total = 0
    failures = 0
    for provider in providers:
        for entry_type, definition in provider.entry_types().items():
            columns = provider.property_keys(entry_type)
            for record in provider.records(entry_type):
                total += 1
                candidate = _validation_record(record, columns)
                try:
                    validate_record(definition, candidate)
                except Exception as exc:
                    failures += 1
                    print(f"INVALID {entry_type} {candidate.get('id')!r}: {exc}", file=sys.stderr)
    print(f"validated {total} record(s) across {len(providers)} provider(s): {failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the altermagnets dataset over OPTIMADE.")
    parser.add_argument("--validate", action="store_true", help="validate every record and exit")
    parser.add_argument("--host", default="127.0.0.1", help="host to bind when serving")
    parser.add_argument("--port", type=int, default=8081, help="port to bind when serving")
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

    providers = build_providers()
    if args.validate:
        return run_validation(providers)
    serve(adapter_from_providers(providers), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
