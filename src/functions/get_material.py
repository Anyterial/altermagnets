import base64
import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

from formula_katex import katex_formula_inline
from httk.atomistic import CartesianSiteMomentsView
from httk.data.db import SqlStore
from input_sanitize import sanitize_material_id
from material_store import (
    MaterialFigure,
    MaterialRecord,
    PlotFile,
    SymmetryVariant,
    details_dir_for_material,
    material_id_aliases,
    material_structure,
)

logger = logging.getLogger(__name__)

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


def _katex_join(values: tuple[str, ...] | list[str]) -> str:
    parts = [_katex_inline(part) for part in values if _katex_inline(part)]
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


def _doi_links(values: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for value in values:
        if value.startswith(("http://", "https://")):
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


def _stored_plot_path(
    plot_file: PlotFile,
    *,
    details_root: Path,
    max_bytes: int,
) -> Path | None:
    relative = Path(plot_file.url)
    if relative.is_absolute():
        return None
    resolved_root = details_root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    actual_size = candidate.stat().st_size
    if actual_size > max_bytes:
        return None
    if plot_file.size is not None and (plot_file.size < 0 or actual_size > plot_file.size):
        return None
    return candidate


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


def _stored_figure_data_urls(
    figure: MaterialFigure,
    *,
    details_root: Path,
    max_bytes: int,
) -> tuple[str | None, str | None]:
    light_path = _stored_plot_path(
        figure.light,
        details_root=details_root,
        max_bytes=max_bytes,
    )
    if light_path is None:
        return (None, None)

    media_type = (figure.light.media_type or "").lower()
    if media_type == "image/png" or light_path.suffix.lower() == ".png":
        if figure.dark is None:
            return (None, None)
        dark_path = _stored_plot_path(
            figure.dark,
            details_root=details_root,
            max_bytes=max_bytes,
        )
        if dark_path is None:
            return (None, None)
        return (
            _png_data_url(light_path, max_bytes=max_bytes),
            _png_data_url(dark_path, max_bytes=max_bytes),
        )

    if media_type == "image/svg+xml" or light_path.suffix.lower() == ".svg":
        light_url, generated_dark_url = _svg_data_urls(
            light_path,
            max_svg_bytes=max_bytes,
        )
        if figure.dark is None:
            return (light_url, generated_dark_url)
        dark_path = _stored_plot_path(
            figure.dark,
            details_root=details_root,
            max_bytes=max_bytes,
        )
        if dark_path is None:
            return (None, None)
        return (
            light_url,
            _svg_data_url(dark_path, max_svg_bytes=max_bytes),
        )

    return (None, None)


def _load_detail_assets(
    material_id: str,
    stored_figures: tuple[MaterialFigure, ...],
    global_data: Any,
) -> dict[str, Any]:
    details_root = _detail_assets_root(global_data)
    max_svg_bytes = _max_svg_bytes(global_data)
    figures: list[dict[str, Any]] = []
    raw_path = ""
    details_dir = details_dir_for_material(details_root, material_id)
    if details_dir is None:
        return {"figures": figures, "raw_path": raw_path, "available_count": 0}

    metadata_paths = [details_dir / f"{alias}.json" for alias in material_id_aliases(material_id)]
    for metadata_path in metadata_paths:
        if not metadata_path.exists() or not metadata_path.is_file():
            continue
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {}
        raw_path = str(payload.get("raw_path", "")).strip()
        break

    stored_by_key: dict[str, MaterialFigure] = {}
    for candidate_figure in stored_figures:
        stored_by_key.setdefault(candidate_figure.key, candidate_figure)

    available_count = 0
    for spec in DETAIL_FIGURE_SPECS:
        figure_record = stored_by_key.get(spec["key"])
        if figure_record is None:
            light_data_url, dark_data_url = (None, None)
        else:
            light_data_url, dark_data_url = _stored_figure_data_urls(
                figure_record,
                details_root=details_root,
                max_bytes=max_svg_bytes,
            )
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


def _decorate_linked_entry(magndata_id: str, variant: SymmetryVariant | None) -> dict[str, Any]:
    """Render a stored variant, including the unresolved-left-join equivalent."""
    source_kind = variant.source_kind if variant is not None else ""
    formula = variant.formula if variant is not None else ""
    magnetic_phases = variant.magnetic_phases if variant is not None else ()
    wave_classes = variant.wave_classes if variant is not None else ()
    warnings = list(variant.warnings) if variant is not None else []
    notes = list(variant.notes) if variant is not None else []
    return {
        "magndata_id": magndata_id,
        "source_kind": source_kind,
        "source_label": CLASSIFICATION_LABELS.get(source_kind, "No symmetry table entry"),
        "magndata_url": _magndata_url(magndata_id),
        "formula": formula,
        "formula_label": katex_formula_inline(formula) or formula,
        "symprec_label": _format_symprec_katex(variant.symprec if variant is not None else None),
        "symprec_variants": variant.symprec_variants if variant is not None else 0,
        "phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "parent_spacegroups": list(variant.parent_spacegroups) if variant is not None else [],
        "parent_spacegroup_label": _katex_join(variant.parent_spacegroups_latex) if variant is not None else "n/a",
        "bns_mcif_label": _katex_join(variant.bns_mcif_labels_latex) if variant is not None else "n/a",
        "bns_label": _katex_join(variant.bns_labels_latex) if variant is not None else "n/a",
        "g_laue_class_label": ", ".join(variant.g_laue_classes) if variant is not None else "n/a",
        "h_laue_class_label": ", ".join(variant.h_laue_classes) if variant is not None else "n/a",
        "connecting_element_label": _katex_join(variant.connecting_elements_latex) if variant is not None else "n/a",
        "spin_angle_mismatch_display": _format_decimal(
            variant.spin_angle_mismatch if variant is not None else None, digits=1, empty="n/a"
        ),
        "spin_length_mismatch_display": _format_decimal(
            variant.spin_length_mismatch if variant is not None else None, digits=3, empty="n/a"
        ),
        "icsd_ids": list(variant.icsd_ids) if variant is not None else [],
        "reference_links": _doi_links(list(variant.reference_dois) if variant is not None else []),
        "warnings": warnings,
        "notes": notes,
    }


def _find_material(store: SqlStore, material_id: str) -> MaterialRecord | None:
    """Resolve aliases in their existing priority order through the query DSL."""
    for candidate in material_id_aliases(material_id):
        searcher = store.searcher()
        material = searcher.variable(MaterialRecord)
        searcher.add(material.id == candidate)
        row = searcher.results(material=material).first()
        if row is not None:
            return row["material"]
    return None


def _structure_payload(material: MaterialRecord) -> dict[str, Any] | None:
    try:
        structure = material_structure(material)
        if structure is None:
            return None
        moments = structure.site_moments
        return {
            "lattice_vectors": structure.cell.basis.to_floats(),
            "species_at_sites": list(structure.species_at_sites),
            "cartesian_site_positions": structure.cartesian_sites().to_floats(),
            "fractional_site_positions": structure.sites.reduced_coords.to_floats(),
            "site_moments": (
                None if moments is None else CartesianSiteMomentsView(moments).cartesian_moments.to_floats()
            ),
        }
    except Exception as error:  # noqa: BLE001 - structure data must not sink the material page.
        logger.warning("Could not build structure payload for %s: %s", material.id, error)
        return None


def execute(global_data, id: str = "", **kwargs):
    store = global_data.get("materials_store")
    if not isinstance(store, SqlStore):
        return None

    material_id = sanitize_material_id(id)
    if not material_id:
        return None

    material = _find_material(store, material_id)
    if material is None:
        return None

    linked_entries = [
        _decorate_linked_entry(link.record.id, variant)
        for link in material.links
        for variant in (link.record.variants or (None,))
    ]
    detail_assets = _load_detail_assets(material.id, material.figures, global_data)
    warnings = [warning for entry in linked_entries for warning in entry["warnings"]]
    notes = [note for entry in linked_entries for note in entry["notes"]]
    magnetic_phases = list(material.magnetic_phases)
    wave_classes = list(material.wave_classes)
    parent_spacegroups = list(material.parent_spacegroups)
    parent_spacegroups_latex = list(material.parent_spacegroups_latex)
    magndata_ids = [link.record.id for link in material.links]
    icsd_ids = list(material.icsd_ids)
    material_label = katex_formula_inline(material.formula) or material.formula
    structure = _structure_payload(material)

    return {
        "material_id": material.id,
        "screening_rank": material.screening_rank,
        "material": material.formula,
        "formula": material.formula,
        "space_group": material.space_group,
        "classification": material.classification,
        "electronic_type": material.electronic_type,
        "max_ss": material.max_ss,
        "avg_ss": material.avg_ss,
        "fdelta_pct": material.fdelta_pct,
        "bandgap": material.bandgap,
        "min_abund_ppm": material.min_abund_ppm,
        "material_label": material_label,
        "classification_label": CLASSIFICATION_LABELS.get(material.classification, material.classification),
        "classification_note": CLASSIFICATION_NOTES.get(material.classification, ""),
        "electronic_type_label": ELECTRONIC_TYPE_LABELS.get(material.electronic_type, material.electronic_type),
        "magndata_ids": magndata_ids,
        "magndata_ids_display": ", ".join(magndata_ids) if magndata_ids else "n/a",
        "magndata_links": [{"id": magndata_id, "url": _magndata_url(magndata_id)} for magndata_id in magndata_ids],
        "elements": list(material.elements),
        "elements_display": ", ".join(material.elements) or "n/a",
        "magnetic_phases": magnetic_phases,
        "magnetic_phase_label": ", ".join(magnetic_phases) if magnetic_phases else "n/a",
        "wave_classes": wave_classes,
        "wave_class_label": ", ".join(wave_classes) if wave_classes else "n/a",
        "parent_spacegroups": parent_spacegroups,
        "space_group_label": (
            _katex_join(parent_spacegroups_latex)
            if parent_spacegroups_latex
            else _katex_inline(material.space_group) or "n/a"
        ),
        "parent_spacegroup_label": _katex_join(parent_spacegroups_latex),
        "max_ss_display": _format_decimal(material.max_ss),
        "avg_ss_display": _format_decimal(material.avg_ss),
        "fdelta_display": _format_percent(material.fdelta_pct),
        "bandgap_display": _format_decimal(material.bandgap),
        "abundance_display": _format_abundance(material.min_abund_ppm),
        "icsd_ids": icsd_ids,
        "doi_links": _doi_links(list(material.dois)),
        "linked_entries": linked_entries,
        "detail_figures": detail_assets["figures"],
        "detail_figure_count": detail_assets["available_count"],
        "detail_figure_total": len(detail_assets["figures"]),
        "detail_raw_path": detail_assets["raw_path"],
        "structure": structure,
        "warnings": warnings,
        "notes": notes,
    }
