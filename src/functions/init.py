"""Initialize the dynamic site from an already-built persistent material store."""

from pathlib import Path
from typing import Any

from formula_katex import katex_formula_inline
from httk.data import PageOrder
from httk.web import SITE_RESOURCES_KEY, SiteResources
from material_store import (
    CLASSIFICATION_LABELS,
    ELECTRONIC_TYPE_LABELS,
    PAPER_PICKED_MATERIALS,
    MaterialRecord,
    cleanup_material_store,
    open_prebuilt_store,
    resolve_store_path,
)


def _default_details_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "details"


def _format_decimal(value: float | None, *, digits: int = 3, empty: str = "n/a") -> str:
    return empty if value is None else f"{value:.{digits}f}"


def _format_abundance(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"{value:,.0f} ppm"
    if value >= 1:
        return f"{value:,.1f} ppm"
    return f"{value:.3f} ppm"


def _material_card(record: MaterialRecord) -> dict[str, Any]:
    return {
        "material_id": record.id,
        "material": record.formula,
        "material_label": katex_formula_inline(record.formula) or record.formula,
        "space_group": record.space_group,
        "classification_label": CLASSIFICATION_LABELS.get(record.classification, record.classification),
        "max_ss_display": _format_decimal(record.max_ss),
        "bandgap_display": _format_decimal(record.bandgap),
        "abundance_display": _format_abundance(record.min_abund_ppm),
    }


def _material_count(store, field: str, value: str) -> int:
    searcher = store.searcher()
    material = searcher.variable(MaterialRecord)
    searcher.add(getattr(material, field) == value)
    return searcher.count()


def _featured_page(store, *, predicate, order: tuple[PageOrder, ...]) -> list[MaterialRecord]:
    """Fetch a bounded feature-card query; it never snapshots the whole store."""
    searcher = store.searcher()
    material = searcher.variable(MaterialRecord)
    searcher.add(predicate(material))
    results = searcher.results(
        material=material,
        material_id=material.id,
        screening_rank=material.screening_rank,
        max_ss=material.max_ss,
        bandgap=material.bandgap,
        min_abund_ppm=material.min_abund_ppm,
    )
    return [row["material"] for row in results.page(size=3, order_by=order).rows]


def _picked_materials(store) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    for _label, aliases in PAPER_PICKED_MATERIALS:
        found: MaterialRecord | None = None
        for alias in aliases:
            searcher = store.searcher()
            material = searcher.variable(MaterialRecord)
            # search_text is lower-case at build time; .contains() treats '%' and
            # '_' as literal text through httk-data's backend-neutral query DSL.
            searcher.add(material.search_text.contains(alias))
            results = searcher.results(material=material, material_id=material.id)
            for row in results.page(size=12, order_by=(PageOrder("material_id"),)).rows:
                candidate = row["material"]
                if candidate.formula.lower() == alias:
                    found = candidate
                    break
            if found is not None:
                break
        if found is not None:
            cards.append(_material_card(found))
    return cards


def _build_featured_materials(store) -> dict[str, list[dict[str, Any]]]:
    return {
        "picked_interesting": _picked_materials(store),
        "largest_splitting": [
            _material_card(record)
            for record in _featured_page(
                store,
                predicate=lambda material: material.always_true(),
                order=(PageOrder("max_ss", descending=True), PageOrder("material_id")),
            )
        ],
        "wide_gap": [
            _material_card(record)
            for record in _featured_page(
                store,
                predicate=lambda material: material.bandgap > 0,
                order=(PageOrder("bandgap", descending=True), PageOrder("material_id")),
            )
        ],
        "earth_abundant": [
            _material_card(record)
            for record in _featured_page(
                store,
                predicate=lambda material: material.min_abund_ppm != None,
                order=(
                    PageOrder("min_abund_ppm", descending=True),
                    PageOrder("max_ss", descending=True),
                    PageOrder("material_id"),
                ),
            )
        ],
    }


def _site_stats(store, *, total_materials: int, store_path: Path) -> dict[str, Any]:
    classification_counts = {
        value: _material_count(store, "classification", value)
        for value in ("collinear", "noncollinear-derived", "mixed", "unclassified")
    }
    electronic_counts = {
        value: _material_count(store, "electronic_type", value)
        for value in ("metallic", "semiconducting", "unknown")
    }
    return {
        "dataset_available": True,
        "store_path": str(store_path),
        "total_materials": total_materials,
        "classification_counts": classification_counts,
        "electronic_counts": electronic_counts,
        "notice": "",
    }


def _unavailable_stats(store_path: Path) -> dict[str, Any]:
    return {
        "dataset_available": False,
        "store_path": str(store_path),
        "total_materials": 0,
        "classification_counts": {value: 0 for value in CLASSIFICATION_LABELS},
        "electronic_counts": {value: 0 for value in ELECTRONIC_TYPE_LABELS},
        "notice": (
            "The screening tables are not mounted on this deployment. "
            "Dynamic pages still load, but no altermagnet entries are available to search."
        ),
    }


def _store_revision(store_path: Path) -> str:
    """Return a compact, path-independent identity for the mounted store."""

    metadata = store_path.stat()
    return f"{metadata.st_size:x}-{metadata.st_mtime_ns:x}"


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
            {"value": "unknown", "label": "KS gap unavailable"},
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
            {"value": "bandgap_desc", "label": "Largest KS gap"},
            {"value": "abundance_desc", "label": "Most abundant constituents"},
        ],
    }


def execute(global_data, **kwargs) -> None:
    """Open the explicit persistent store; never rebuild or read CSV at runtime."""
    cleanup_material_store(global_data)
    store_path = resolve_store_path()
    opened = open_prebuilt_store(store_path)
    global_data["detail_assets_root"] = _default_details_dir()
    global_data["search_options"] = _build_search_options()
    global_data["classification_labels"] = dict(CLASSIFICATION_LABELS)
    global_data["electronic_type_labels"] = dict(ELECTRONIC_TYPE_LABELS)

    if opened is None:
        global_data["site_stats"] = _unavailable_stats(store_path)
        global_data["featured_materials"] = {
            "picked_interesting": [],
            "largest_splitting": [],
            "wide_gap": [],
            "earth_abundant": [],
        }
        return

    global_data["materials_database"] = opened.database
    global_data["materials_store"] = opened.store
    global_data["materials_store_path"] = store_path
    resources = global_data.get(SITE_RESOURCES_KEY)
    if not isinstance(resources, SiteResources):
        cleanup_material_store(global_data)
        raise TypeError("httk-web site resources are required to own the material store")
    try:
        resources.register(lambda: cleanup_material_store(global_data))
    except BaseException:
        cleanup_material_store(global_data)
        raise

    global_data["materials_store_revision"] = _store_revision(store_path)
    global_data["site_stats"] = _site_stats(
        opened.store,
        total_materials=opened.material_count,
        store_path=store_path,
    )
    global_data["featured_materials"] = _build_featured_materials(opened.store)
