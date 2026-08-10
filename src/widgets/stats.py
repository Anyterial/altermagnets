"""Render the home-page OPTIMADE count widget."""

import os
from html import escape
from pathlib import Path

from _internal import safe_json
from httk.serve.web.widgets import WidgetAsset, WidgetRenderResult


def render(context, **props):
    """Render the inert count configuration and its browser asset."""
    del props
    base_url = os.environ.get("ALTERMAGNETS_OPTIMADE_BASE_URL", "/optimade").rstrip("/") or "/"
    config_id = escape(f"site-stats-{context.widget_id}-config", quote=True)
    relative_base = context.page.get("relbaseurl", ".")
    if not isinstance(relative_base, str) or not relative_base or relative_base.startswith("/"):
        raise ValueError("page context has no safe relative base URL")
    asset_href = f"{relative_base.rstrip('/')}/_httk/serve/assets/site-stats.mjs"
    html = (
        f'<script type="module" src="{escape(asset_href, quote=True)}"></script>'
        f'<script id="{config_id}" type="application/json">{safe_json({"base_url": base_url})}</script>'
    )
    asset = WidgetAsset(
        "site-stats.mjs",
        Path(__file__).with_name("stats.mjs").read_bytes(),
        "text/javascript",
    )
    return WidgetRenderResult(html, assets=(asset,))
