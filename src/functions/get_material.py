from typing import Any

CLASSIFICATION_LABELS = {
    "collinear": "Naturally collinear",
    "noncollinear-derived": "Noncollinear-derived",
    "mixed": "Mixed provenance",
    "unclassified": "Not classified yet",
}

ELECTRONIC_TYPE_LABELS = {
    "metallic": "Metallic",
    "semiconducting": "Semiconducting",
    "unknown": "Band gap unavailable",
}

CLASSIFICATION_NOTES = {
    "collinear": (
        "All linked MAGNDATA entries are naturally collinear in the symmetry screening. "
        "These are the most direct candidates for experimental follow-up."
    ),
    "noncollinear-derived": (
        "All linked MAGNDATA entries entered the workflow as noncollinear structures and were "
        "converted to collinear reference states before the altermagnetism test."
    ),
    "mixed": (
        "This screening row bundles multiple MAGNDATA entries, and the linked symmetry data spans "
        "both naturally collinear and noncollinear-derived records."
    ),
    "unclassified": (
        "The DFT screening row exists, but the linked MAGNDATA identifiers are currently missing "
        "from the symmetry-summary tables mounted in this deployment."
    ),
}


def _split_pipe(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.split("|") if part]


def _format_decimal(value: float | None, *, digits: int = 3, empty: str = "n/a") -> str:
    if value is None:
        return empty
    return f"{value:.{digits}f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.1f}%"


def _format_abundance(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"{value:,.0f} ppm"
    if value >= 1:
        return f"{value:,.1f} ppm"
    return f"{value:.3f} ppm"


def _fetch_one(connection: Any, sql: str, params: list[Any]) -> dict[str, Any] | None:
    cursor = connection.execute(sql, params)
    row = cursor.fetchone()
    if row is None:
        return None
    columns = [column[0] for column in cursor.description]
    return dict(zip(columns, row, strict=False))


def _fetch_all(connection: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def _doi_links(values: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for value in values:
        if value.startswith("http://") or value.startswith("https://"):
            links.append({"label": value, "url": value})
            continue
        if value.startswith("10."):
            links.append({"label": value, "url": f"https://doi.org/{value}"})
    return links


def _decorate_linked_entry(row: dict[str, Any]) -> dict[str, Any]:
    magnetic_phases = _split_pipe(row.get("magnetic_phases_text"))
    wave_classes = _split_pipe(row.get("wave_classes_text"))
    warnings = _split_pipe(row.get("warnings_text"))
    notes = _split_pipe(row.get("notes_text"))
    return {
        "magndata_id": row["magndata_id"],
        "source_kind": row.get("source_kind") or "",
        "source_label": CLASSIFICATION_LABELS.get(row.get("source_kind") or "", "No symmetry table entry"),
        "formula": row.get("formula") or "",
        "symprec_display": _format_decimal(row.get("symprec"), digits=5),
        "symprec_variants": row.get("symprec_variants") or 0,
        "phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "parent_spacegroups": _split_pipe(row.get("parent_spacegroups_text")),
        "parent_spacegroup_label": ", ".join(_split_pipe(row.get("parent_spacegroups_text"))) or "n/a",
        "spin_angle_mismatch_display": _format_decimal(row.get("spin_angle_mismatch"), digits=1, empty="n/a"),
        "spin_length_mismatch_display": _format_decimal(row.get("spin_length_mismatch"), digits=3, empty="n/a"),
        "icsd_ids": _split_pipe(row.get("icsd_ids_text")),
        "reference_links": _doi_links(_split_pipe(row.get("doi_text"))),
        "warnings": warnings,
        "notes": notes,
    }


def execute(global_data, id: str = "", **kwargs):
    connection = global_data.get("materials_db")
    lock = global_data.get("materials_db_lock")
    if connection is None or lock is None:
        return None

    material_id = (id or "").strip()
    if not material_id:
        return None

    with lock:
        material = _fetch_one(
            connection,
            """
            SELECT
                material_id,
                screening_rank,
                material,
                formula,
                space_group,
                primary_magndata_id,
                magndata_ids_text,
                elements_text,
                classification,
                magnetic_phases_text,
                wave_classes_text,
                parent_spacegroups_text,
                max_ss,
                avg_ss,
                fdelta_pct,
                bandgap,
                electronic_type,
                min_abund_ppm,
                icsd_ids_text,
                doi_text
            FROM materials
            WHERE material_id = ?
            """,
            [material_id],
        )
        linked_rows = _fetch_all(
            connection,
            """
            SELECT
                mm.magndata_id,
                mm.ordinal,
                mm.is_primary,
                se.source_kind,
                se.formula,
                se.symprec,
                se.symprec_variants,
                se.magnetic_phases_text,
                se.wave_classes_text,
                se.parent_spacegroups_text,
                se.spin_angle_mismatch,
                se.spin_length_mismatch,
                se.icsd_ids_text,
                se.doi_text,
                se.warnings_text,
                se.notes_text
            FROM material_magndata AS mm
            LEFT JOIN symmetry_entries AS se
                ON se.magndata_id = mm.magndata_id
            WHERE mm.material_id = ?
            ORDER BY mm.ordinal ASC, se.source_kind ASC
            """,
            [material_id],
        )

    if material is None:
        return None

    linked_entries = [_decorate_linked_entry(row) for row in linked_rows]
    warnings = [warning for entry in linked_entries for warning in entry["warnings"]]
    notes = [note for entry in linked_entries for note in entry["notes"]]
    magnetic_phases = _split_pipe(material.get("magnetic_phases_text"))
    wave_classes = _split_pipe(material.get("wave_classes_text"))
    parent_spacegroups = _split_pipe(material.get("parent_spacegroups_text"))
    magndata_ids = _split_pipe(material.get("magndata_ids_text"))
    icsd_ids = _split_pipe(material.get("icsd_ids_text"))
    doi_values = _split_pipe(material.get("doi_text"))

    return {
        **material,
        "classification_label": CLASSIFICATION_LABELS.get(material["classification"], material["classification"]),
        "classification_note": CLASSIFICATION_NOTES.get(material["classification"], ""),
        "electronic_type_label": ELECTRONIC_TYPE_LABELS.get(material["electronic_type"], material["electronic_type"]),
        "magndata_ids": magndata_ids,
        "magndata_ids_display": ", ".join(magndata_ids) if magndata_ids else "n/a",
        "elements": _split_pipe(material.get("elements_text")),
        "elements_display": ", ".join(_split_pipe(material.get("elements_text"))) or "n/a",
        "magnetic_phases": magnetic_phases,
        "magnetic_phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_classes": wave_classes,
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "parent_spacegroups": parent_spacegroups,
        "parent_spacegroup_label": ", ".join(parent_spacegroups) if parent_spacegroups else "n/a",
        "max_ss_display": _format_decimal(material.get("max_ss")),
        "avg_ss_display": _format_decimal(material.get("avg_ss")),
        "fdelta_display": _format_percent(material.get("fdelta_pct")),
        "bandgap_display": _format_decimal(material.get("bandgap")),
        "abundance_display": _format_abundance(material.get("min_abund_ppm")),
        "icsd_ids": icsd_ids,
        "doi_links": _doi_links(doi_values),
        "linked_entries": linked_entries,
        "warnings": warnings,
        "notes": notes,
    }
