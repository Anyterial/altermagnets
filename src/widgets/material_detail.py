"""Render the browser-side OPTIMADE material detail widget."""

import os
from html import escape
from pathlib import Path

from _internal import first_line, safe_json, served_structure_definitions
from httk.serve.web.widgets import WidgetAsset, WidgetRenderResult, optimade_protocol_asset, optimade_protocol_href

RESPONSE_FIELDS = (
    "chemical_formula_reduced",
    "_anyterial_formula",
    "_anyterial_elements",
    "_anyterial_space_group",
    "_anyterial_classification",
    "_anyterial_electronic_type",
    "_anyterial_magnetic_phases",
    "_anyterial_wave_classes",
    "_anyterial_parent_spacegroups",
    "_anyterial_icsd_ids",
    "_httk_magndata_ids",
    "_httk_dft_band_gap",
    "_anyterial_max_spin_splitting",
    "_anyterial_avg_spin_splitting",
    "_anyterial_spin_splitting_fraction",
    "_anyterial_min_crustal_abundance",
    "_anyterial_magndata_variants",
    "_httk_custom_figures",
)


def render(context, **props):
    """Render an inert shell that the browser fills from OPTIMADE."""
    del props
    base_url = os.environ.get("ALTERMAGNETS_OPTIMADE_BASE_URL", "/optimade/amdb").rstrip("/") or "/"
    widget_id = escape(context.widget_id, quote=True)
    config_id = escape(f"site-material-detail-{context.widget_id}-config", quote=True)
    definitions = served_structure_definitions()
    field_info = {
        name: {"description": description}
        for name in RESPONSE_FIELDS
        if (description := first_line(definitions.get(name, {}).get("description"))) is not None
    }
    config = {
        "base_url": base_url,
        "entry_type": "structures",
        "id_query": "id",
        "response_fields": list(RESPONSE_FIELDS),
        "widget_id": context.widget_id,
        "field_info": field_info,
    }
    asset = WidgetAsset(
        "site-material-detail.mjs",
        Path(__file__).with_name("material-detail.mjs").read_bytes(),
        "text/javascript",
    )
    asset_href = f"{optimade_protocol_href(context).rsplit('/', 1)[0]}/site-material-detail.mjs"
    html = (
        f'<script type="module" src="{escape(optimade_protocol_href(context), quote=True)}"></script>'
        f'<script type="module" src="{escape(asset_href, quote=True)}"></script>'
        f'<section class="material-detail-widget" data-site-material-detail="1" data-widget-id="{widget_id}" '
        f'data-config-id="{config_id}" aria-busy="true">'
        '<p class="empty" data-material-detail-status role="status" aria-live="polite">Loading material details.</p>'
        '<noscript><p class="empty">Material details require JavaScript and an OPTIMADE data service.</p></noscript>'
        f'<script id="{config_id}" type="application/json">{safe_json(config)}</script>'
        "</section>"
    )
    return WidgetRenderResult(html, assets=(optimade_protocol_asset(), asset))
