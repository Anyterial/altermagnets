"""Bounded ``httk.serve.table`` provider for the altermagnet search page."""

from collections.abc import Mapping
from typing import Any

from httk.data import ContinuationToken
from httk.data.db import SqlStore
from httk.serve.web import ProviderContext, TableColumn, TablePage, TableRequest
from input_sanitize import sanitize_search_inputs
from search_materials import decorate_material, search_materials

TABLE_COLUMNS = (
    TableColumn("material", "Material", class_name="col-material"),
    TableColumn("magndata_ids", "MAGNDATA IDs", class_name="col-magndata"),
    TableColumn("classification_label", "Collinearity", class_name="col-collinearity"),
    TableColumn("space_group", "Space group", class_name="col-spacegroup"),
    TableColumn("max_ss_display", r"$\Delta E^{\mathrm{max}}_{\mathrm{split}}$", class_name="col-maxss"),
    TableColumn("avg_ss_display", r"$\Delta E^{\mathrm{avg}}_{\mathrm{split}}$", class_name="col-avgss"),
    TableColumn("fdelta_display", "FΔ", class_name="col-fdelta"),
    TableColumn("bandgap_display", "KS Gap", class_name="col-gap"),
    TableColumn("abundance_display", "Min abundance", class_name="col-abundance"),
)


def _sanitized_query(query: Mapping[str, str]) -> dict[str, str]:
    """Keep only search controls the site accepts before they reach the DSL."""

    sanitized = sanitize_search_inputs(dict(query))
    # ``id`` is valid only on the detail route.  It must never become an
    # unexpected ``search_materials`` keyword or override a row's own ID.
    sanitized.pop("id", None)
    return sanitized


def _detail_url(context: ProviderContext, material_id: str, query: Mapping[str, str]) -> str:
    """Build one encoded detail URL with the normalized search snapshot."""

    snapshot = {
        key: value for key, value in query.items() if value and not (key == "sort" and value == "screening_rank")
    }
    snapshot["id"] = material_id
    return context.url_for("material", query=snapshot)


def _unavailable_page(revision: str | None) -> TablePage:
    return TablePage.from_rows((), columns=TABLE_COLUMNS, revision=revision)


def provide(context: ProviderContext, request: TableRequest, **provider_args: object) -> TablePage:
    """Fetch exactly one store-backed keyset page for the current search."""

    del provider_args
    sanitized = _sanitized_query(context.query)
    store = context.global_data.get("materials_store")
    revision_value: Any = context.global_data.get("materials_store_revision")
    revision = revision_value if isinstance(revision_value, str) else None
    if not isinstance(store, SqlStore):
        return _unavailable_page(revision)

    results, page_order = search_materials(store, **sanitized)
    result_page = results.page(
        size=request.page_size,
        order_by=page_order,
        cursor=ContinuationToken(request.cursor) if request.cursor is not None else None,
        include_total=False,
    )
    rows = tuple(
        decorate_material(
            result_row["material"],
            detail_url=_detail_url(context, result_row["material"].id, sanitized),
        )
        for result_row in result_page.rows
    )
    return TablePage.from_rows(
        rows,
        columns=TABLE_COLUMNS,
        next_cursor=result_page.next,
        previous_cursor=result_page.previous,
        revision=revision,
    )
