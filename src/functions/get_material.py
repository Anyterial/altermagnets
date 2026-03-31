import base64
import json
import math
import os
import re
from pathlib import Path
from typing import Any

from formula_katex import katex_formula_inline
from input_sanitize import sanitize_material_id

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

DETAIL_FIGURE_SPECS = (
    {
        "key": "band",
        "filename": "band.svg",
        "title": "Band structure",
        "summary": "",
        "empty_message": "Band structure has not been generated for this material yet.",
        "alt": "Spin-split band structure",
        "layout_class": "figure-card--wide",
    },
    {
        "key": "structure",
        "filename": "structure.svg",
        "title": "Crystal structure",
        "summary": "",
        "empty_message": "Crystal structure figure has not been generated for this material yet.",
        "alt": "Crystal structure view",
        "layout_class": "",
    },
    {
        "key": "bz",
        "filename": "bz.svg",
        "title": "Brillouin zone and path",
        "summary": "Reciprocal-space box with labelled special points and the reported Δmax location when available.",
        "empty_message": "Brillouin-zone figure has not been generated for this material yet.",
        "alt": "Brillouin zone and k-path",
        "layout_class": "",
    },
)
AMDB_ID_PATTERN = re.compile(r"^(?:anyt:)?amdb-(?:(?P<dataset>\d+)-)?(?P<number>\d+)$")
SVG_DARK_LIGHT_COLOR = "#f2f5fb"
SVG_DARK_TEXT_STYLE = (
    '<style id="httk-dark-svg-text">'
    'g[id^="text_"] path, g[id^="text_"] use, '
    "text, tspan {"
    f"fill: {SVG_DARK_LIGHT_COLOR} !important; "
    f"color: {SVG_DARK_LIGHT_COLOR} !important;"
    "} "
    'g[id^="legend_"] g[id^="patch_"] path[style*="opacity: 0.8"], '
    'g[id^="legend_"] g[id^="patch_"] path[style*="opacity:0.8"] {'
    "fill: rgba(28, 33, 40, 0.88) !important; "
    "stroke: #7e8793 !important; "
    "opacity: 1 !important;"
    "}"
    "</style>"
)
SVG_DARK_BLACK_COLOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(?<![0-9a-f])#000000(?![0-9a-f])"),
    re.compile(r"(?i)(?<![0-9a-f])#000(?![0-9a-f])"),
    re.compile(r"(?i)(?<![0-9a-f])#000000ff(?![0-9a-f])"),
    re.compile(r"(?i)(?<![0-9a-f])#000f(?![0-9a-f])"),
    re.compile(r"(?i)\brgb\(\s*0%\s*,\s*0%\s*,\s*0%\s*\)\b"),
    re.compile(r"(?i)\brgb\(\s*0\s*,\s*0\s*,\s*0\s*\)\b"),
    re.compile(r"(?i)\brgba\(\s*0\s*,\s*0\s*,\s*0\s*,\s*1(?:\.0+)?\s*\)\b"),
    re.compile(r"(?i)\bblack\b"),
    re.compile(r"(?i)(?<![0-9a-f])#262626(?![0-9a-f])"),
    re.compile(r"(?i)(?<![0-9a-f])#1f1f1f(?![0-9a-f])"),
    re.compile(r"(?i)(?<![0-9a-f])#333333(?![0-9a-f])"),
)
SVG_DARK_WHITE_FILL_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'(?i)\bfill\s*=\s*"(?:#ffffff|#fff|white)"'), 'fill="none"'),
    (re.compile(r"(?i)\bfill\s*=\s*'(?:#ffffff|#fff|white)'"), "fill='none'"),
    (re.compile(r"(?i)\bfill\s*:\s*(?:#ffffff|#fff|white)\b"), "fill: none"),
)
MAX_SVG_BYTES = 1_500_000
DEFAULT_MAX_SVG_BYTES = MAX_SVG_BYTES * 100


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


def _katex_inline(text: str) -> str:
    value = (text or "").strip()
    if not value:
        return ""
    if "$" in value:
        return value
    return f"${value}$"


def _katex_join_pipe(value: str | None) -> str:
    parts = [_katex_inline(part) for part in _split_pipe(value) if _katex_inline(part)]
    return ", ".join(parts) if parts else "n/a"


def _format_symprec_katex(value: float | None) -> str:
    if value is None or value <= 0:
        return "n/a"
    exponent = math.log10(value)
    rounded_exponent = round(exponent)
    if abs(exponent - rounded_exponent) < 1e-9:
        exponent_text = str(int(rounded_exponent))
    else:
        exponent_text = f"{exponent:.3f}".rstrip("0").rstrip(".")
    return f"$10^{{{exponent_text}}}$"


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


def _magndata_url(magndata_id: str) -> str:
    return f"https://cryst.ehu.es/magndata/index.php?index={magndata_id}"


def _detail_assets_root(global_data: Any) -> Path:
    root = global_data.get("detail_assets_root")
    if isinstance(root, Path):
        return root
    # Use the src/data symlink so the app can read a read-only mounted data tree.
    return Path(__file__).resolve().parents[1] / "data" / "details"


def _max_svg_bytes(global_data: Any) -> int:
    configured = global_data.get("max_svg_bytes")
    if configured is None:
        configured = os.environ.get("ALTERMAGNETS_MAX_SVG_BYTES", "").strip()
    if configured in {"", None}:
        return DEFAULT_MAX_SVG_BYTES
    try:
        parsed = int(configured)
    except (TypeError, ValueError):
        return DEFAULT_MAX_SVG_BYTES
    return parsed if parsed > 0 else DEFAULT_MAX_SVG_BYTES


def _parsed_material_id(material_id: str) -> tuple[str, str] | None:
    match = AMDB_ID_PATTERN.fullmatch(material_id.strip())
    if match is None:
        return None
    dataset = match.group("dataset") or "1"
    digits = match.group("number")
    return dataset, digits


def _material_id_aliases(material_id: str) -> list[str]:
    cleaned = material_id.strip()
    parsed = _parsed_material_id(cleaned)
    if parsed is None:
        return [cleaned] if cleaned else []

    dataset, digits = parsed
    base_id = f"amdb-{dataset}-{digits}"
    prefixed_id = f"anyt:{base_id}"
    aliases = [cleaned]
    if cleaned.startswith("anyt:"):
        aliases.append(base_id)
    else:
        aliases.append(prefixed_id)

    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias in seen:
            continue
        seen.add(alias)
        deduped.append(alias)
    return deduped


def _details_dir_for_material(details_root: Path, material_id: str) -> Path | None:
    parsed = _parsed_material_id(material_id)
    if parsed is None:
        return None
    dataset, digits = parsed
    if len(digits) < 3:
        digits = digits.zfill(3)
    shard_root = details_root / f"amdb-{dataset}" / digits[:1] / digits[:2] / digits[:3]
    candidates = [shard_root / alias for alias in _material_id_aliases(material_id)]
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return candidates[0] if candidates else None


def _svg_data_url(path: Path, *, max_svg_bytes: int) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    if path.stat().st_size > max_svg_bytes:
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _png_data_url(path: Path, *, max_bytes: int) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    if path.stat().st_size > max_bytes:
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _svg_data_url_from_text(svg_text: str) -> str:
    encoded = base64.b64encode(svg_text.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def _svg_dark_variant(svg_text: str) -> str:
    transformed = svg_text
    for pattern, replacement in SVG_DARK_WHITE_FILL_PATTERNS:
        transformed = pattern.sub(replacement, transformed)
    for pattern in SVG_DARK_BLACK_COLOR_PATTERNS:
        transformed = pattern.sub(SVG_DARK_LIGHT_COLOR, transformed)
    if 'id="httk-dark-svg-text"' not in transformed:
        transformed = re.sub(r"(<svg\b[^>]*>)", r"\1" + SVG_DARK_TEXT_STYLE, transformed, count=1)
    return transformed


def _svg_data_urls(path: Path, *, max_svg_bytes: int) -> tuple[str | None, str | None]:
    if not path.exists() or not path.is_file():
        return (None, None)
    if path.stat().st_size > max_svg_bytes:
        return (None, None)
    raw = path.read_text(encoding="utf-8", errors="replace")
    return (_svg_data_url_from_text(raw), _svg_data_url_from_text(_svg_dark_variant(raw)))


def _load_detail_assets(material_id: str, global_data: Any) -> dict[str, Any]:
    details_root = _detail_assets_root(global_data)
    max_svg_bytes = _max_svg_bytes(global_data)
    figures: list[dict[str, Any]] = []
    raw_path = ""
    details_dir = _details_dir_for_material(details_root, material_id)
    if details_dir is None:
        return {"figures": figures, "raw_path": raw_path, "available_count": 0}

    metadata_paths = [details_dir / f"{alias}.json" for alias in _material_id_aliases(material_id)]
    for metadata_path in metadata_paths:
        if not metadata_path.exists() or not metadata_path.is_file():
            continue
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        raw_path = str(payload.get("raw_path", "")).strip()
        break

    available_count = 0
    for spec in DETAIL_FIGURE_SPECS:
        svg_path = details_dir / spec["filename"]
        png_path = svg_path.with_suffix(".png")
        dark_png_path = png_path.with_name(f"{png_path.stem}_dark{png_path.suffix}")
        light_data_url = _png_data_url(png_path, max_bytes=max_svg_bytes)
        dark_data_url = _png_data_url(dark_png_path, max_bytes=max_svg_bytes)
        if light_data_url is None or dark_data_url is None:
            light_data_url = None
            dark_data_url = None

        if light_data_url is None:
            light_data_url, dark_data_url = _svg_data_urls(svg_path, max_svg_bytes=max_svg_bytes)
            if light_data_url is None:
                # Backward-compatible support for separately generated dark variants.
                dark_filename = f"{Path(spec['filename']).stem}_dark.svg"
                dark_data_url = _svg_data_url(details_dir / dark_filename, max_svg_bytes=max_svg_bytes)
        available = light_data_url is not None
        if available:
            available_count += 1
        figures.append(
            {
                "key": spec["key"],
                "title": spec["title"],
                "summary": spec["summary"],
                "empty_message": spec["empty_message"],
                "alt": spec["alt"],
                "layout_class": spec["layout_class"],
                "available": available,
                "data_url": light_data_url or "",
                "dark_data_url": dark_data_url or "",
            }
        )

    return {
        "figures": figures,
        "raw_path": raw_path,
        "available_count": available_count,
    }


def _decorate_linked_entry(row: dict[str, Any]) -> dict[str, Any]:
    magnetic_phases = _split_pipe(row.get("magnetic_phases_text"))
    wave_classes = _split_pipe(row.get("wave_classes_text"))
    warnings = _split_pipe(row.get("warnings_text"))
    notes = _split_pipe(row.get("notes_text"))
    formula = row.get("formula") or ""
    return {
        "magndata_id": row["magndata_id"],
        "source_kind": row.get("source_kind") or "",
        "source_label": CLASSIFICATION_LABELS.get(row.get("source_kind") or "", "No symmetry table entry"),
        "magndata_url": _magndata_url(row["magndata_id"]),
        "formula": formula,
        "formula_label": katex_formula_inline(formula) or formula,
        "symprec_label": _format_symprec_katex(row.get("symprec")),
        "symprec_variants": row.get("symprec_variants") or 0,
        "phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "parent_spacegroups": _split_pipe(row.get("parent_spacegroups_text")),
        "parent_spacegroup_label": _katex_join_pipe(row.get("parent_spacegroups_latex_text")),
        "bns_mcif_label": _katex_join_pipe(row.get("bns_mcif_latex_text")),
        "bns_label": _katex_join_pipe(row.get("bns_latex_text")),
        "g_laue_class_label": ", ".join(_split_pipe(row.get("g_laue_classes_text"))) or "n/a",
        "h_laue_class_label": ", ".join(_split_pipe(row.get("h_laue_classes_text"))) or "n/a",
        "connecting_element_label": _katex_join_pipe(row.get("connecting_elements_latex_text")),
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

    material_id = sanitize_material_id(id)
    if not material_id:
        return None

    with lock:
        material = None
        resolved_material_id = material_id
        for material_id_candidate in _material_id_aliases(material_id):
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
                    parent_spacegroups_latex_text,
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
                [material_id_candidate],
            )
            if material is not None:
                resolved_material_id = str(material.get("material_id") or material_id_candidate)
                break
        if material is None:
            return None

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
                se.wave_classes_full_text,
                se.parent_spacegroups_text,
                se.parent_spacegroups_latex_text,
                se.bns_mcif_text,
                se.bns_mcif_latex_text,
                se.bns_text,
                se.bns_latex_text,
                se.effective_bns_text,
                se.effective_bns_latex_text,
                se.g_laue_classes_text,
                se.h_laue_classes_text,
                se.connecting_elements_text,
                se.connecting_elements_latex_text,
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
            ORDER BY
                mm.ordinal ASC,
                se.source_kind ASC,
                se.symprec ASC NULLS LAST
            """,
            [resolved_material_id],
        )

    linked_entries = [_decorate_linked_entry(row) for row in linked_rows]
    detail_assets = _load_detail_assets(resolved_material_id, global_data)
    warnings = [warning for entry in linked_entries for warning in entry["warnings"]]
    notes = [note for entry in linked_entries for note in entry["notes"]]
    magnetic_phases = _split_pipe(material.get("magnetic_phases_text"))
    wave_classes = _split_pipe(material.get("wave_classes_text"))
    parent_spacegroups = _split_pipe(material.get("parent_spacegroups_text"))
    parent_spacegroups_latex = _split_pipe(material.get("parent_spacegroups_latex_text"))
    magndata_ids = _split_pipe(material.get("magndata_ids_text"))
    icsd_ids = _split_pipe(material.get("icsd_ids_text"))
    doi_values = _split_pipe(material.get("doi_text"))
    material_formula = material.get("formula") or material.get("material") or ""
    material_label = katex_formula_inline(material_formula) or material.get("material") or ""

    return {
        **material,
        "material_label": material_label,
        "classification_label": CLASSIFICATION_LABELS.get(material["classification"], material["classification"]),
        "classification_note": CLASSIFICATION_NOTES.get(material["classification"], ""),
        "electronic_type_label": ELECTRONIC_TYPE_LABELS.get(material["electronic_type"], material["electronic_type"]),
        "magndata_ids": magndata_ids,
        "magndata_ids_display": ", ".join(magndata_ids) if magndata_ids else "n/a",
        "magndata_links": [{"id": magndata_id, "url": _magndata_url(magndata_id)} for magndata_id in magndata_ids],
        "elements": _split_pipe(material.get("elements_text")),
        "elements_display": ", ".join(_split_pipe(material.get("elements_text"))) or "n/a",
        "magnetic_phases": magnetic_phases,
        "magnetic_phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_classes": wave_classes,
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "parent_spacegroups": parent_spacegroups,
        "space_group_label": (
            _katex_join_pipe(material.get("parent_spacegroups_latex_text"))
            if parent_spacegroups_latex
            else _katex_inline(material.get("space_group") or "") or "n/a"
        ),
        "parent_spacegroup_label": _katex_join_pipe(material.get("parent_spacegroups_latex_text")),
        "max_ss_display": _format_decimal(material.get("max_ss")),
        "avg_ss_display": _format_decimal(material.get("avg_ss")),
        "fdelta_display": _format_percent(material.get("fdelta_pct")),
        "bandgap_display": _format_decimal(material.get("bandgap")),
        "abundance_display": _format_abundance(material.get("min_abund_ppm")),
        "icsd_ids": icsd_ids,
        "doi_links": _doi_links(doi_values),
        "linked_entries": linked_entries,
        "detail_figures": detail_assets["figures"],
        "detail_figure_count": detail_assets["available_count"],
        "detail_figure_total": len(detail_assets["figures"]),
        "detail_raw_path": detail_assets["raw_path"],
        "warnings": warnings,
        "notes": notes,
    }
