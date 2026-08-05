"""Reusable, bounded material-search query and presentation helpers."""

from math import isfinite
from typing import Any

from formula_katex import katex_formula_inline
from httk.data import PageOrder
from httk.data.db import SqlResultSet, SqlStore
from material_store import CLASSIFICATION_LABELS, ELECTRONIC_TYPE_LABELS, MaterialRecord

PAGE_ORDERS: dict[str, tuple[PageOrder, ...]] = {
    # The former SQL ordered this view by canonical material ID, not integer rank.
    "screening_rank": (PageOrder("material_id"),),
    "max_ss_desc": (PageOrder("max_ss", descending=True), PageOrder("material_id")),
    "avg_ss_desc": (PageOrder("avg_ss", descending=True), PageOrder("material_id")),
    "bandgap_desc": (PageOrder("bandgap", descending=True), PageOrder("material_id")),
    "abundance_desc": (
        PageOrder("min_abund_ppm", descending=True),
        PageOrder("max_ss", descending=True),
        PageOrder("material_id"),
    ),
}
MAX_TEXT_TOKEN_LENGTH = 64
MAX_TEXT_TOKENS = 12
MAX_ELEMENT_TOKEN_LENGTH = 8
MAX_ELEMENT_TOKENS = 16
MAX_PREDICATES = 40


def _bounded_tokens(value: str, *, max_tokens: int, max_token_length: int) -> list[str]:
    tokens: list[str] = []
    for raw in value.replace(",", " ").split():
        cleaned = raw.strip()
        if cleaned:
            tokens.append(cleaned[:max_token_length])
        if len(tokens) >= max_tokens:
            break
    return tokens


def _canonical_element_tokens(value: str) -> list[str]:
    return [
        token[:1].upper() + token[1:].lower()
        for token in _bounded_tokens(
            value,
            max_tokens=MAX_ELEMENT_TOKENS,
            max_token_length=MAX_ELEMENT_TOKEN_LENGTH,
        )
    ]


def _parse_float(value: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if isfinite(parsed) else None


def _text_tokens(value: str) -> list[str]:
    return _bounded_tokens(
        value.lower(),
        max_tokens=MAX_TEXT_TOKENS,
        max_token_length=MAX_TEXT_TOKEN_LENGTH,
    )


def _format_decimal(value: float | None, *, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def _format_abundance(value: float | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1000:
        return f"{value:,.0f} ppm"
    if value >= 1:
        return f"{value:,.1f} ppm"
    return f"{value:.3f} ppm"


def decorate_material(record: MaterialRecord, *, detail_url: str) -> dict[str, Any]:
    """Return the detached, presentation-only row accepted by ``TablePage``."""

    return {
        "material_id": record.id,
        "screening_rank": record.screening_rank,
        "material": record.formula,
        "formula": record.formula,
        "space_group": record.space_group,
        "classification": record.classification,
        "electronic_type": record.electronic_type,
        "max_ss": record.max_ss,
        "avg_ss": record.avg_ss,
        "fdelta_pct": record.fdelta_pct,
        "bandgap": record.bandgap,
        "min_abund_ppm": record.min_abund_ppm,
        "material_label": katex_formula_inline(record.formula) or record.formula,
        "detail_url": detail_url,
        "classification_label": CLASSIFICATION_LABELS.get(record.classification, record.classification),
        "electronic_type_label": ELECTRONIC_TYPE_LABELS.get(record.electronic_type, record.electronic_type),
        "magndata_ids": [link.record.id for link in record.links],
        "elements": list(record.elements),
        "magnetic_phases": list(record.magnetic_phases),
        "magnetic_phase_label": ", ".join(record.magnetic_phases) or "n/a",
        "wave_classes": list(record.wave_classes),
        "wave_class_label": ", ".join(record.wave_classes) or "n/a",
        "max_ss_display": _format_decimal(record.max_ss),
        "avg_ss_display": _format_decimal(record.avg_ss),
        "fdelta_display": _format_percent(record.fdelta_pct),
        "bandgap_display": _format_decimal(record.bandgap),
        "abundance_display": _format_abundance(record.min_abund_ppm),
    }


def search_materials(
    store: SqlStore,
    *,
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
) -> tuple[SqlResultSet, tuple[PageOrder, ...]]:
    """Return a reusable filtered result set and its seek-pagination order.

    The DSL's :meth:`contains` intentionally treats ``%`` and ``_`` as
    literal characters.  The prior raw SQL path treated them as LIKE
    wildcards, so this is a deliberate compatibility improvement and keeps
    future widget callers safe when passing the result set directly to
    ``.page()``.
    """
    searcher = store.searcher()
    material = searcher.variable(MaterialRecord)
    predicate_count = 0

    for token in _text_tokens(q):
        if predicate_count >= MAX_PREDICATES:
            break
        searcher.add(material.search_text.contains(token))
        predicate_count += 1
    for element in _canonical_element_tokens(elements):
        if predicate_count >= MAX_PREDICATES:
            break
        searcher.add(material.elements.has_any(element))
        predicate_count += 1
    if classification:
        searcher.add(material.classification == classification)
        predicate_count += 1
    if electronic_type:
        searcher.add(material.electronic_type == electronic_type)
        predicate_count += 1
    if magnetic_phase:
        searcher.add(material.magnetic_phases.has_any(magnetic_phase))
        predicate_count += 1
    if wave_class:
        searcher.add(material.wave_classes.has_any(wave_class))
        predicate_count += 1
    if space_group.strip():
        # This dedicated normalized query field preserves the prior semantics:
        # a case-insensitive substring of space_group only, never an incidental
        # match in formula, phase, MAGNDATA ID, or another search-text token.
        searcher.add(material.space_group_search.contains(space_group.strip().lower()))
        predicate_count += 1

    for field, raw_value, operator in (
        ("max_ss", min_max_ss, ">="),
        ("avg_ss", min_avg_ss, ">="),
        ("fdelta_pct", min_fdelta_pct, ">="),
        ("bandgap", min_bandgap, ">="),
        ("bandgap", max_bandgap, "<="),
        ("min_abund_ppm", min_abundance_ppm, ">="),
    ):
        if predicate_count >= MAX_PREDICATES:
            break
        value = _parse_float(raw_value)
        if value is None:
            continue
        column = getattr(material, field)
        searcher.add(column >= value if operator == ">=" else column <= value)
        predicate_count += 1

    results = searcher.results(
        material=material,
        material_id=material.id,
        screening_rank=material.screening_rank,
        max_ss=material.max_ss,
        avg_ss=material.avg_ss,
        bandgap=material.bandgap,
        min_abund_ppm=material.min_abund_ppm,
    )
    return results, PAGE_ORDERS.get(sort, PAGE_ORDERS["screening_rank"])
