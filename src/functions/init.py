import csv
import os
import re
import threading
from pathlib import Path
from typing import Any

import duckdb
from formula_katex import katex_formula_inline

ELEMENT_PATTERN = re.compile(r"[A-Z][a-z]?")
SCREENING_RESULTS_FILENAME = "high_throughput_screening_results_fixed.csv"
AMDB_ID_COLUMN = "AMDBId"
AMDB_DATASET = "1"

CLASSIFICATION_LABELS = {
    "collinear": "Collinear",
    "noncollinear-derived": "Based on noncollinear",
    "mixed": "Both",
    "unclassified": "Not classified yet",
}

PAPER_PICKED_MATERIALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("UCr2Si2C", ("ucr2si2c",)),
    ("NbMnP", ("nbmnp", "mnnbp")),
    ("YRuO3", ("yruo3",)),
)

ELECTRONIC_TYPE_LABELS = {
    "metallic": "Metallic",
    "semiconducting": "Semiconducting",
    "unknown": "Band gap unavailable",
}


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "tables"


def _default_details_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "details"


def _default_material_id(index: int) -> str:
    return f"anyt:am-{AMDB_DATASET}-{index:04d}"


def _resolve_data_dir() -> Path:
    override = os.environ.get("ALTERMAGNETS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _default_data_dir()


def _parse_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text or text in {".", "?"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


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
    text = text.replace("$", "")
    text = text.replace("{", "")
    text = text.replace("}", "")
    text = text.replace("\\", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_latex_text(value: str) -> str:
    text = (value or "").strip()
    if not text or text in {".", "?"}:
        return ""
    return text


def _split_magndata_ids(value: str) -> list[str]:
    # MAGNDATA identifiers are opaque strings, not numeric values.
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _extract_elements(formula: str) -> list[str]:
    seen: set[str] = set()
    elements: list[str] = []
    for token in ELEMENT_PATTERN.findall(formula or ""):
        if token in seen:
            continue
        seen.add(token)
        elements.append(token)
    return elements


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def _join_pipe(values: list[str]) -> str:
    return "|".join(_dedupe(values))


def _classification_label(value: str) -> str:
    return CLASSIFICATION_LABELS.get(value, value.replace("-", " ").title())


def _electronic_type_label(value: str) -> str:
    return ELECTRONIC_TYPE_LABELS.get(value, value.replace("-", " ").title())


def _format_decimal(value: float | None, *, digits: int = 3, empty: str = "n/a") -> str:
    if value is None:
        return empty
    return f"{value:.{digits}f}"


def _format_percent(value: float | None, *, digits: int = 1, empty: str = "n/a") -> str:
    if value is None:
        return empty
    return f"{value:.{digits}f}%"


def _format_abundance(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"{value:,.0f} ppm"
    if value >= 1:
        return f"{value:,.1f} ppm"
    return f"{value:.3f} ppm"


def _load_csv_rows(data_dir: Path, filename: str, *, delimiter: str = ",") -> list[dict[str, str]]:
    path = data_dir / filename
    if not path.exists() or not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def _summarize_symmetry_rows(
    rows: list[dict[str, str]],
    *,
    source_kind: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        magndata_id = (row.get("MAGNDATAId") or "").strip()
        if not magndata_id:
            continue
        grouped.setdefault(magndata_id, []).append(row)

    summaries: list[dict[str, Any]] = []
    for magndata_id, group_rows in grouped.items():
        # Preserve per-symprec detail so the UI can show one row/card per distinct
        # spglib tolerance used in the source summary tables.
        rows_by_symprec: dict[float | None, list[dict[str, str]]] = {}
        for row in group_rows:
            symprec = _parse_float(row.get("Symprec", ""))
            rows_by_symprec.setdefault(symprec, []).append(row)

        for symprec, variant_rows in rows_by_symprec.items():
            summaries.append(
                {
                    "magndata_id": magndata_id,
                    "source_kind": source_kind,
                    "formula": _clean_display_text(
                        next(
                            (row.get("ChemicalFormula", "") for row in variant_rows if row.get("ChemicalFormula")),
                            "",
                        )
                    ),
                    "symprec": symprec,
                    "symprec_variants": len(variant_rows),
                    "magnetic_phases": _dedupe(
                        [_clean_display_text(row.get("MagneticPhaseShort", "")) for row in variant_rows]
                    ),
                    "wave_classes": _dedupe(
                        [_clean_display_text(row.get("WaveClass", "")) for row in variant_rows]
                    ),
                    "wave_classes_full": _dedupe(
                        [_clean_display_text(row.get("WaveClass", "")) for row in variant_rows]
                    ),
                    "parent_spacegroups": _dedupe(
                        [_clean_display_text(row.get("ParentSpacegroup", "")) for row in variant_rows]
                    ),
                    "parent_spacegroups_latex": _dedupe(
                        [_clean_latex_text(row.get("ParentSpacegroup", "")) for row in variant_rows]
                    ),
                    "bns_mcif_labels": _dedupe([_clean_display_text(row.get("BNSmcif", "")) for row in variant_rows]),
                    "bns_mcif_labels_latex": _dedupe(
                        [_clean_latex_text(row.get("BNSmcif", "")) for row in variant_rows]
                    ),
                    "bns_labels": _dedupe([_clean_display_text(row.get("BNS", "")) for row in variant_rows]),
                    "bns_labels_latex": _dedupe([_clean_latex_text(row.get("BNS", "")) for row in variant_rows]),
                    "effective_bns_labels": _dedupe(
                        [_clean_display_text(row.get("EffectiveBNS", "")) for row in variant_rows]
                    ),
                    "effective_bns_labels_latex": _dedupe(
                        [_clean_latex_text(row.get("EffectiveBNS", "")) for row in variant_rows]
                    ),
                    "g_laue_classes": _dedupe(
                        [_clean_display_text(row.get("GMagneticSystemLaueClass", "")) for row in variant_rows]
                    ),
                    "h_laue_classes": _dedupe(
                        [_clean_display_text(row.get("HHalvingSubgroupLaueClass", "")) for row in variant_rows]
                    ),
                    "connecting_elements": _dedupe(
                        [_clean_display_text(row.get("AGenopConnectingElement", "")) for row in variant_rows]
                    ),
                    "connecting_elements_latex": _dedupe(
                        [_clean_latex_text(row.get("AGenopConnectingElement", "")) for row in variant_rows]
                    ),
                    "spin_angle_mismatch": max(
                        (
                            value
                            for value in (_parse_float(row.get("SpinAngleMismatch", "")) for row in variant_rows)
                            if value is not None
                        ),
                        default=None,
                    ),
                    "spin_length_mismatch": max(
                        (
                            value
                            for value in (_parse_float(row.get("SpinLengthMismatch", "")) for row in variant_rows)
                            if value is not None
                        ),
                        default=None,
                    ),
                    "icsd_ids": _dedupe([_clean_display_text(row.get("ICSDId", "")) for row in variant_rows]),
                    "reference_dois": _dedupe(
                        [_clean_display_text(row.get("ReferenceDOI", "")) for row in variant_rows]
                    ),
                    "warnings": _dedupe([_clean_display_text(row.get("Warnings", "")) for row in variant_rows]),
                    "notes": _dedupe([_clean_display_text(row.get("Notes", "")) for row in variant_rows]),
                }
            )

    return sorted(
        summaries,
        key=lambda item: (
            item["magndata_id"],
            1 if item["symprec"] is None else 0,
            float(item["symprec"] or 0.0),
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


def _build_material_rows(
    screening_rows: list[dict[str, str]],
    symmetry_by_id: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    materials: list[dict[str, Any]] = []
    mapping_rows: list[dict[str, Any]] = []

    for index, row in enumerate(screening_rows, start=1):
        material_id = (row.get(AMDB_ID_COLUMN) or "").strip() or _default_material_id(index)
        magndata_ids = _split_magndata_ids(row.get("MAGNDATA ID", ""))
        linked_summaries: list[dict[str, Any]] = []
        for magndata_id in magndata_ids:
            linked_summaries.extend(symmetry_by_id.get(magndata_id, []))

        has_collinear = any(entry["source_kind"] == "collinear" for entry in linked_summaries)
        has_noncollinear = any(entry["source_kind"] == "noncollinear-derived" for entry in linked_summaries)
        classification = _classification_from_sources(has_collinear, has_noncollinear)

        phases = _dedupe([phase for entry in linked_summaries for phase in entry["magnetic_phases"]])
        wave_classes = _dedupe([wave for entry in linked_summaries for wave in entry["wave_classes"]])
        parent_spacegroups = _dedupe(
            [spacegroup for entry in linked_summaries for spacegroup in entry["parent_spacegroups"]]
        )
        parent_spacegroups_latex = _dedupe(
            [spacegroup for entry in linked_summaries for spacegroup in entry["parent_spacegroups_latex"]]
        )
        icsd_ids = _dedupe([icsd_id for entry in linked_summaries for icsd_id in entry["icsd_ids"]])
        reference_dois = _dedupe([doi for entry in linked_summaries for doi in entry["reference_dois"]])

        material = (row.get("Material") or "").strip()
        elements = _extract_elements(material)
        bandgap = _parse_float(row.get("Bandgap", ""))
        if bandgap is None:
            electronic_type = "unknown"
        elif bandgap > 0:
            electronic_type = "semiconducting"
        else:
            electronic_type = "metallic"

        materials.append(
            {
                "material_id": material_id,
                "screening_rank": index,
                "material": material,
                "formula": material,
                "space_group": (row.get("Space group") or "").strip(),
                "primary_magndata_id": magndata_ids[0] if magndata_ids else "",
                "magndata_ids_text": _join_pipe(magndata_ids),
                "elements_text": _join_pipe(elements),
                "classification": classification,
                "magnetic_phases_text": _join_pipe(phases),
                "wave_classes_text": _join_pipe(wave_classes),
                "parent_spacegroups_text": _join_pipe(parent_spacegroups),
                "parent_spacegroups_latex_text": _join_pipe(parent_spacegroups_latex),
                "has_collinear": has_collinear,
                "has_noncollinear": has_noncollinear,
                "linked_entry_count": len(linked_summaries),
                "max_ss": _parse_float(row.get("MaxSS", "")),
                "avg_ss": _parse_float(row.get("AvgSS", "")),
                "fdelta_pct": _parse_float(row.get("FdeltaPct", "")),
                "bandgap": bandgap,
                "electronic_type": electronic_type,
                "min_abund_ppm": _parse_float(row.get("MinAbundPpm", "")),
                "icsd_ids_text": _join_pipe(icsd_ids),
                "doi_text": _join_pipe(reference_dois),
                "search_text": " ".join(
                    [
                        material.lower(),
                        " ".join(id_value.lower() for id_value in magndata_ids),
                        (row.get("Space group") or "").lower(),
                        " ".join(element.lower() for element in elements),
                        " ".join(parent.lower() for parent in parent_spacegroups),
                        " ".join(phase.lower() for phase in phases),
                        " ".join(wave.lower() for wave in wave_classes),
                        classification.lower(),
                    ]
                ).strip(),
            }
        )

        for ordinal, magndata_id in enumerate(magndata_ids, start=1):
            mapping_rows.append(
                {
                    "material_id": material_id,
                    "magndata_id": magndata_id,
                    "ordinal": ordinal,
                    "is_primary": ordinal == 1,
                }
            )

    return materials, mapping_rows


def _create_empty_db() -> Any:
    connection = duckdb.connect()
    connection.execute("""
        CREATE TABLE materials (
            material_id TEXT,
            screening_rank INTEGER,
            material TEXT,
            formula TEXT,
            space_group TEXT,
            primary_magndata_id TEXT,
            magndata_ids_text TEXT,
            elements_text TEXT,
            classification TEXT,
            magnetic_phases_text TEXT,
            wave_classes_text TEXT,
            parent_spacegroups_text TEXT,
            parent_spacegroups_latex_text TEXT,
            has_collinear BOOLEAN,
            has_noncollinear BOOLEAN,
            linked_entry_count INTEGER,
            max_ss DOUBLE,
            avg_ss DOUBLE,
            fdelta_pct DOUBLE,
            bandgap DOUBLE,
            electronic_type TEXT,
            min_abund_ppm DOUBLE,
            icsd_ids_text TEXT,
            doi_text TEXT,
            search_text TEXT
        )
        """)
    connection.execute("""
        CREATE TABLE material_magndata (
            material_id TEXT,
            magndata_id TEXT,
            ordinal INTEGER,
            is_primary BOOLEAN
        )
        """)
    connection.execute("""
        CREATE TABLE symmetry_entries (
            magndata_id TEXT,
            source_kind TEXT,
            formula TEXT,
            symprec DOUBLE,
            symprec_variants INTEGER,
            magnetic_phases_text TEXT,
            wave_classes_text TEXT,
            wave_classes_full_text TEXT,
            parent_spacegroups_text TEXT,
            parent_spacegroups_latex_text TEXT,
            bns_mcif_text TEXT,
            bns_mcif_latex_text TEXT,
            bns_text TEXT,
            bns_latex_text TEXT,
            effective_bns_text TEXT,
            effective_bns_latex_text TEXT,
            g_laue_classes_text TEXT,
            h_laue_classes_text TEXT,
            connecting_elements_text TEXT,
            connecting_elements_latex_text TEXT,
            spin_angle_mismatch DOUBLE,
            spin_length_mismatch DOUBLE,
            icsd_ids_text TEXT,
            doi_text TEXT,
            warnings_text TEXT,
            notes_text TEXT
        )
        """)
    return connection


def _material_card(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "material_id": entry["material_id"],
        "material": entry["material"],
        "material_label": katex_formula_inline(entry.get("formula") or entry["material"]) or entry["material"],
        "space_group": entry["space_group"],
        "classification_label": _classification_label(entry["classification"]),
        "max_ss_display": _format_decimal(entry["max_ss"]),
        "bandgap_display": _format_decimal(entry["bandgap"]),
        "abundance_display": _format_abundance(entry["min_abund_ppm"]),
    }


def _build_featured_materials(materials: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_name = {str(entry.get("material", "")).strip().lower(): entry for entry in materials}
    picked_interesting: list[dict[str, Any]] = []
    for _label, aliases in PAPER_PICKED_MATERIALS:
        for alias in aliases:
            entry = by_name.get(alias)
            if entry is not None:
                picked_interesting.append(_material_card(entry))
                break

    by_max_ss = sorted(
        materials,
        key=lambda entry: (entry["max_ss"] is None, -(entry["max_ss"] or 0), entry["screening_rank"]),
    )
    by_bandgap = sorted(
        [entry for entry in materials if (entry["bandgap"] or 0) > 0],
        key=lambda entry: (-(entry["bandgap"] or 0), entry["screening_rank"]),
    )
    by_abundance = sorted(
        [entry for entry in materials if entry["min_abund_ppm"] is not None],
        key=lambda entry: (-(entry["min_abund_ppm"] or 0), -(entry["max_ss"] or 0), entry["screening_rank"]),
    )

    return {
        "picked_interesting": picked_interesting,
        "largest_splitting": [_material_card(entry) for entry in by_max_ss[:3]],
        "wide_gap": [_material_card(entry) for entry in by_bandgap[:3]],
        "earth_abundant": [_material_card(entry) for entry in by_abundance[:3]],
    }


def _build_site_stats(materials: list[dict[str, Any]], *, data_available: bool, data_dir: Path) -> dict[str, Any]:
    classification_counts = {
        "collinear": sum(1 for entry in materials if entry["classification"] == "collinear"),
        "noncollinear-derived": sum(1 for entry in materials if entry["classification"] == "noncollinear-derived"),
        "mixed": sum(1 for entry in materials if entry["classification"] == "mixed"),
        "unclassified": sum(1 for entry in materials if entry["classification"] == "unclassified"),
    }
    electronic_counts = {
        "metallic": sum(1 for entry in materials if entry["electronic_type"] == "metallic"),
        "semiconducting": sum(1 for entry in materials if entry["electronic_type"] == "semiconducting"),
        "unknown": sum(1 for entry in materials if entry["electronic_type"] == "unknown"),
    }

    notice = ""
    if not data_available:
        notice = (
            "The screening tables are not mounted on this deployment. "
            "Dynamic pages still load, but no altermagnet entries are available to search."
        )

    return {
        "dataset_available": data_available,
        "data_dir": str(data_dir),
        "total_materials": len(materials),
        "classification_counts": classification_counts,
        "electronic_counts": electronic_counts,
        "notice": notice,
    }


def _build_search_options() -> dict[str, Any]:
    return {
        "classifications": [
            {"value": "", "label": "Any collinearity"},
            {"value": "collinear", "label": "Collinear"},
            {"value": "noncollinear-derived", "label": "Based on noncollinear"},
            {"value": "mixed", "label": "Both"},
            {"value": "unclassified", "label": "Not classified yet"},
        ],
        "electronic_types": [
            {"value": "", "label": "Any type"},
            {"value": "metallic", "label": "Metallic"},
            {"value": "semiconducting", "label": "Semiconducting"},
            {"value": "unknown", "label": "Band gap unavailable"},
        ],
        "magnetic_phases": [
            {"value": "", "label": "Any phase"},
            {"value": "AM", "label": "AM"},
            {"value": "FiM", "label": "FiM"},
        ],
        "wave_classes": [
            {"value": "", "label": "Any wave class"},
            {"value": "d", "label": "d"},
            {"value": "g", "label": "g"},
            {"value": "s", "label": "s"},
        ],
        "sorts": [
            {"value": "screening_rank", "label": "ID"},
            {"value": "max_ss_desc", "label": "Largest maximum spin splitting"},
            {"value": "avg_ss_desc", "label": "Largest average spin splitting"},
            {"value": "bandgap_desc", "label": "Largest band gap"},
            {"value": "abundance_desc", "label": "Most abundant constituents"},
        ],
    }


def execute(global_data, **kwargs):
    data_dir = _resolve_data_dir()
    screening_rows = _load_csv_rows(data_dir, SCREENING_RESULTS_FILENAME, delimiter=";")
    collinear_rows = _load_csv_rows(data_dir, "altermagnets_collinear.csv")
    noncollinear_rows = _load_csv_rows(data_dir, "altermagnets_noncollinear.csv")

    collinear_summaries = _summarize_symmetry_rows(collinear_rows, source_kind="collinear")
    noncollinear_summaries = _summarize_symmetry_rows(
        noncollinear_rows,
        source_kind="noncollinear-derived",
    )

    symmetry_entries = sorted(
        collinear_summaries + noncollinear_summaries,
        key=lambda entry: (entry["magndata_id"], entry["source_kind"]),
    )
    symmetry_by_id: dict[str, list[dict[str, Any]]] = {}
    for entry in symmetry_entries:
        symmetry_by_id.setdefault(entry["magndata_id"], []).append(entry)

    materials, material_magndata = _build_material_rows(screening_rows, symmetry_by_id)

    connection = _create_empty_db()
    if materials:
        connection.executemany(
            """
            INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    entry["material_id"],
                    entry["screening_rank"],
                    entry["material"],
                    entry["formula"],
                    entry["space_group"],
                    entry["primary_magndata_id"],
                    entry["magndata_ids_text"],
                    entry["elements_text"],
                    entry["classification"],
                    entry["magnetic_phases_text"],
                    entry["wave_classes_text"],
                    entry["parent_spacegroups_text"],
                    entry["parent_spacegroups_latex_text"],
                    entry["has_collinear"],
                    entry["has_noncollinear"],
                    entry["linked_entry_count"],
                    entry["max_ss"],
                    entry["avg_ss"],
                    entry["fdelta_pct"],
                    entry["bandgap"],
                    entry["electronic_type"],
                    entry["min_abund_ppm"],
                    entry["icsd_ids_text"],
                    entry["doi_text"],
                    entry["search_text"],
                )
                for entry in materials
            ],
        )

    if material_magndata:
        connection.executemany(
            "INSERT INTO material_magndata VALUES (?, ?, ?, ?)",
            [
                (entry["material_id"], entry["magndata_id"], entry["ordinal"], entry["is_primary"])
                for entry in material_magndata
            ],
        )

    if symmetry_entries:
        connection.executemany(
            "INSERT INTO symmetry_entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    entry["magndata_id"],
                    entry["source_kind"],
                    entry["formula"],
                    entry["symprec"],
                    entry["symprec_variants"],
                    _join_pipe(entry["magnetic_phases"]),
                    _join_pipe(entry["wave_classes"]),
                    _join_pipe(entry["wave_classes_full"]),
                    _join_pipe(entry["parent_spacegroups"]),
                    _join_pipe(entry["parent_spacegroups_latex"]),
                    _join_pipe(entry["bns_mcif_labels"]),
                    _join_pipe(entry["bns_mcif_labels_latex"]),
                    _join_pipe(entry["bns_labels"]),
                    _join_pipe(entry["bns_labels_latex"]),
                    _join_pipe(entry["effective_bns_labels"]),
                    _join_pipe(entry["effective_bns_labels_latex"]),
                    _join_pipe(entry["g_laue_classes"]),
                    _join_pipe(entry["h_laue_classes"]),
                    _join_pipe(entry["connecting_elements"]),
                    _join_pipe(entry["connecting_elements_latex"]),
                    entry["spin_angle_mismatch"],
                    entry["spin_length_mismatch"],
                    _join_pipe(entry["icsd_ids"]),
                    _join_pipe(entry["reference_dois"]),
                    _join_pipe(entry["warnings"]),
                    _join_pipe(entry["notes"]),
                )
                for entry in symmetry_entries
            ],
        )

    global_data["materials_db"] = connection
    global_data["materials_db_lock"] = threading.Lock()
    global_data["detail_assets_root"] = _default_details_dir()
    global_data["site_stats"] = _build_site_stats(
        materials,
        data_available=bool(materials),
        data_dir=data_dir,
    )
    global_data["featured_materials"] = _build_featured_materials(materials)
    global_data["search_options"] = _build_search_options()
    global_data["classification_labels"] = dict(CLASSIFICATION_LABELS)
    global_data["electronic_type_labels"] = dict(ELECTRONIC_TYPE_LABELS)
