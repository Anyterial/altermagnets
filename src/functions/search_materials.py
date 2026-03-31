from math import isfinite
from typing import Any

from formula_katex import katex_formula_inline
from input_sanitize import sanitize_search_inputs

CLASSIFICATION_LABELS = {
    "collinear": "Collinear",
    "noncollinear-derived": "Based on noncollinear",
    "mixed": "Both",
    "unclassified": "Not classified yet",
}

ELECTRONIC_TYPE_LABELS = {
    "metallic": "Metallic",
    "semiconducting": "Semiconducting",
    "unknown": "Band gap unavailable",
}

SORT_SQL = {
    "screening_rank": "material_id ASC",
    "max_ss_desc": "max_ss DESC NULLS LAST, material_id ASC",
    "avg_ss_desc": "avg_ss DESC NULLS LAST, material_id ASC",
    "bandgap_desc": "bandgap DESC NULLS LAST, material_id ASC",
    "abundance_desc": "min_abund_ppm DESC NULLS LAST, max_ss DESC NULLS LAST, material_id ASC",
}
SORT_LABELS = {
    "screening_rank": "ID",
    "max_ss_desc": "Largest maximum spin splitting",
    "avg_ss_desc": "Largest average spin splitting",
    "bandgap_desc": "Largest band gap",
    "abundance_desc": "Most abundant constituents",
}
MAX_SPLIT_LABEL = r"$\Delta E^{\mathrm{max}}_{\mathrm{split}}$"
AVG_SPLIT_LABEL = r"$\Delta E^{\mathrm{avg}}_{\mathrm{split}}$"

MAX_TEXT_TOKEN_LENGTH = 64
MAX_TEXT_TOKENS = 12
MAX_ELEMENT_TOKEN_LENGTH = 8
MAX_ELEMENT_TOKENS = 16
MAX_PREDICATES = 40


def _bounded_tokens(value: str, *, max_tokens: int, max_token_length: int) -> list[str]:
    tokens: list[str] = []
    for raw in value.replace(",", " ").split():
        cleaned = raw.strip()
        if not cleaned:
            continue
        tokens.append(cleaned[:max_token_length])
        if len(tokens) >= max_tokens:
            break
    return tokens


def _canonical_element_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for cleaned in _bounded_tokens(
        value,
        max_tokens=MAX_ELEMENT_TOKENS,
        max_token_length=MAX_ELEMENT_TOKEN_LENGTH,
    ):
        tokens.append(cleaned[:1].upper() + cleaned[1:].lower())
    return tokens


def _parse_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    if not isfinite(parsed):
        return None
    return parsed


def _split_pipe(value: str | None) -> list[str]:
    if not value:
        return []
    return [part for part in value.split("|") if part]


def _format_decimal(value: float | None, *, digits: int = 3) -> str:
    if value is None:
        return "n/a"
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


def _fetch_all(connection: Any, sql: str, params: list[Any]) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row, strict=False)) for row in cursor.fetchall()]


def _text_tokens(value: str) -> list[str]:
    return _bounded_tokens(
        value.lower(),
        max_tokens=MAX_TEXT_TOKENS,
        max_token_length=MAX_TEXT_TOKEN_LENGTH,
    )


def _decorate_row(row: dict[str, Any]) -> dict[str, Any]:
    magnetic_phases = _split_pipe(row.get("magnetic_phases_text"))
    wave_classes = _split_pipe(row.get("wave_classes_text"))
    material = row.get("material") or ""
    formula = row.get("formula") or material
    return {
        **row,
        "material_label": katex_formula_inline(formula) or material,
        "classification_label": CLASSIFICATION_LABELS.get(row["classification"], row["classification"]),
        "electronic_type_label": ELECTRONIC_TYPE_LABELS.get(row["electronic_type"], row["electronic_type"]),
        "magndata_ids": _split_pipe(row.get("magndata_ids_text")),
        "elements": _split_pipe(row.get("elements_text")),
        "magnetic_phases": magnetic_phases,
        "magnetic_phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_classes": wave_classes,
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "max_ss_display": _format_decimal(row.get("max_ss")),
        "avg_ss_display": _format_decimal(row.get("avg_ss")),
        "fdelta_display": _format_percent(row.get("fdelta_pct")),
        "bandgap_display": _format_decimal(row.get("bandgap")),
        "abundance_display": _format_abundance(row.get("min_abund_ppm")),
    }


def _active_filters(
    *,
    q: str,
    elements: str,
    classification: str,
    electronic_type: str,
    magnetic_phase: str,
    wave_class: str,
    space_group: str,
    min_max_ss: str,
    min_avg_ss: str,
    min_fdelta_pct: str,
    min_bandgap: str,
    max_bandgap: str,
    min_abundance_ppm: str,
    sort: str,
) -> list[dict[str, str]]:
    filters: list[dict[str, str]] = []
    if q.strip():
        filters.append({"label": "Text", "value": q.strip()})
    if elements.strip():
        filters.append({"label": "Elements", "value": ", ".join(_canonical_element_tokens(elements))})
    if classification:
        filters.append({"label": "Collinearity", "value": CLASSIFICATION_LABELS.get(classification, classification)})
    if electronic_type:
        filters.append(
            {"label": "Electronic type", "value": ELECTRONIC_TYPE_LABELS.get(electronic_type, electronic_type)}
        )
    if magnetic_phase:
        filters.append({"label": "Phase", "value": magnetic_phase})
    if wave_class:
        filters.append({"label": "Wave class", "value": wave_class})
    if space_group.strip():
        filters.append({"label": "Space group", "value": space_group.strip()})
    if min_max_ss.strip():
        filters.append({"label": f"{MAX_SPLIT_LABEL} >=", "value": f"{min_max_ss.strip()} eV"})
    if min_avg_ss.strip():
        filters.append({"label": f"{AVG_SPLIT_LABEL} >=", "value": f"{min_avg_ss.strip()} eV"})
    if min_fdelta_pct.strip():
        filters.append({"label": "FΔ >=", "value": f"{min_fdelta_pct.strip()} %"})
    if min_bandgap.strip():
        filters.append({"label": "Band gap >=", "value": f"{min_bandgap.strip()} eV"})
    if max_bandgap.strip():
        filters.append({"label": "Band gap <=", "value": f"{max_bandgap.strip()} eV"})
    if min_abundance_ppm.strip():
        filters.append({"label": "Min abundance >=", "value": f"{min_abundance_ppm.strip()} ppm"})
    if sort and sort != "screening_rank":
        filters.append({"label": "Sorted by", "value": SORT_LABELS.get(sort, sort.replace("_", " "))})
    return filters


def execute(
    global_data,
    q: str = "",
    elements: str = "",
    classification: str = "",
    electronic_type: str = "",
    magnetic_phase: str = "",
    wave_class: str = "",
    space_group: str = "",
    min_max_ss: str = "",
    min_avg_ss: str = "",
    min_fdelta_pct: str = "",
    min_bandgap: str = "",
    max_bandgap: str = "",
    min_abundance_ppm: str = "",
    sort: str = "screening_rank",
    **kwargs,
):
    sanitized = sanitize_search_inputs(
        {
            "q": q,
            "elements": elements,
            "classification": classification,
            "electronic_type": electronic_type,
            "magnetic_phase": magnetic_phase,
            "wave_class": wave_class,
            "space_group": space_group,
            "min_max_ss": min_max_ss,
            "min_avg_ss": min_avg_ss,
            "min_fdelta_pct": min_fdelta_pct,
            "min_bandgap": min_bandgap,
            "max_bandgap": max_bandgap,
            "min_abundance_ppm": min_abundance_ppm,
            "sort": sort,
        }
    )
    q = sanitized["q"]
    elements = sanitized["elements"]
    classification = sanitized["classification"]
    electronic_type = sanitized["electronic_type"]
    magnetic_phase = sanitized["magnetic_phase"]
    wave_class = sanitized["wave_class"]
    space_group = sanitized["space_group"]
    min_max_ss = sanitized["min_max_ss"]
    min_avg_ss = sanitized["min_avg_ss"]
    min_fdelta_pct = sanitized["min_fdelta_pct"]
    min_bandgap = sanitized["min_bandgap"]
    max_bandgap = sanitized["max_bandgap"]
    min_abundance_ppm = sanitized["min_abundance_ppm"]
    sort = sanitized["sort"]

    site_stats = global_data.get("site_stats", {})
    connection = global_data.get("materials_db")
    lock = global_data.get("materials_db_lock")
    if connection is None or lock is None:
        return {
            "dataset_available": False,
            "summary": "The screening tables are not mounted on this deployment.",
            "items": [],
            "count": 0,
            "total": 0,
            "active_filters": [],
        }

    sql = ["""
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
            max_ss,
            avg_ss,
            fdelta_pct,
            bandgap,
            electronic_type,
            min_abund_ppm
        FROM materials
        WHERE 1 = 1
        """]
    params: list[Any] = []

    predicate_count = 0

    for token in _text_tokens(q):
        if predicate_count >= MAX_PREDICATES:
            break
        sql.append("AND search_text LIKE ?")
        params.append(f"%{token}%")
        predicate_count += 1

    for element in _canonical_element_tokens(elements):
        if predicate_count >= MAX_PREDICATES:
            break
        sql.append("AND list_contains(string_split(elements_text, '|'), ?)")
        params.append(element)
        predicate_count += 1

    if classification:
        sql.append("AND classification = ?")
        params.append(classification)
        predicate_count += 1

    if electronic_type:
        sql.append("AND electronic_type = ?")
        params.append(electronic_type)
        predicate_count += 1

    if magnetic_phase:
        sql.append("AND list_contains(string_split(magnetic_phases_text, '|'), ?)")
        params.append(magnetic_phase)
        predicate_count += 1

    if wave_class:
        sql.append("AND list_contains(string_split(wave_classes_text, '|'), ?)")
        params.append(wave_class)
        predicate_count += 1

    if space_group.strip():
        sql.append("AND lower(space_group) LIKE ?")
        params.append(f"%{space_group.strip().lower()}%")
        predicate_count += 1

    numeric_filters = [
        ("max_ss", min_max_ss, ">="),
        ("avg_ss", min_avg_ss, ">="),
        ("fdelta_pct", min_fdelta_pct, ">="),
        ("bandgap", min_bandgap, ">="),
        ("bandgap", max_bandgap, "<="),
        ("min_abund_ppm", min_abundance_ppm, ">="),
    ]
    for column, raw_value, operator in numeric_filters:
        if predicate_count >= MAX_PREDICATES:
            break
        parsed = _parse_float(raw_value)
        if parsed is None:
            continue
        sql.append(f"AND {column} {operator} ?")
        params.append(parsed)
        predicate_count += 1

    order_sql = SORT_SQL.get(sort, SORT_SQL["screening_rank"])
    sql.append(f"ORDER BY {order_sql}")

    with lock:
        rows = _fetch_all(connection, "\n".join(sql), params)

    items = [_decorate_row(row) for row in rows]
    active_filters = _active_filters(
        q=q,
        elements=elements,
        classification=classification,
        electronic_type=electronic_type,
        magnetic_phase=magnetic_phase,
        wave_class=wave_class,
        space_group=space_group,
        min_max_ss=min_max_ss,
        min_avg_ss=min_avg_ss,
        min_fdelta_pct=min_fdelta_pct,
        min_bandgap=min_bandgap,
        max_bandgap=max_bandgap,
        min_abundance_ppm=min_abundance_ppm,
        sort=sort,
    )
    total = int(site_stats.get("total_materials", len(items)) or 0)
    summary = f"Showing {len(items)} of {total} screened entries."
    if not active_filters:
        summary = f"Showing all {total} screened entries."

    return {
        "dataset_available": bool(site_stats.get("dataset_available")),
        "summary": summary,
        "items": items,
        "count": len(items),
        "total": total,
        "active_filters": active_filters,
    }
