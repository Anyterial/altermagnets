"""Render the browser-side OPTIMADE table used by the material search page."""

import os

from httk.serve.web.widgets.optimade_table import render as render_optimade_table


def render(context, **props):
    """Render the site's nine-column OPTIMADE search table."""

    del props
    base_url = os.environ.get("ALTERMAGNETS_OPTIMADE_BASE_URL", "/optimade/amdb").rstrip("/") or "/"
    return render_optimade_table(
        context,
        base_url=base_url,
        columns=(
            {"key": "_anyterial_formula", "label": "Material", "format": "formula"},
            {"key": "_httk_magndata_ids", "label": "MAGNDATA IDs", "format": {"name": "join", "separator": ", "}},
            {"key": "_anyterial_classification", "label": "Collinearity"},
            {"key": "_anyterial_space_group", "label": "Space group"},
            {
                "key": "_anyterial_max_spin_splitting",
                "label": r"$\Delta E^{\mathrm{max}}_{\mathrm{split}}$",
                "format": {"name": "number", "digits": 3, "suffix": " eV"},
            },
            {
                "key": "_anyterial_avg_spin_splitting",
                "label": r"$\Delta E^{\mathrm{avg}}_{\mathrm{split}}$",
                "format": {"name": "number", "digits": 3, "suffix": " eV"},
            },
            {
                "key": "_anyterial_spin_splitting_fraction",
                "label": "FΔ",
                "format": {"name": "number", "digits": 1, "scale": 100, "suffix": " %"},
            },
            {
                "key": "_httk_dft_band_gap",
                "label": "KS Gap",
                "format": {"name": "number", "digits": 3, "suffix": " eV"},
            },
            {"key": "_anyterial_min_crustal_abundance", "label": "Min abundance"},
        ),
        page_size=50,
        filter_query="filter",
        # The OPTIMADE sort is read from a dedicated key so the human-facing `sort` alias
        # (e.g. "screening_rank") in the URL is never forwarded verbatim to OPTIMADE.
        sort_query="osort",
        caption="Screened altermagnet search results",
        detail_route="material",
        detail_column="_anyterial_formula",
        detail_query="id",
    )
