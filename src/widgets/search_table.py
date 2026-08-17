"""Render the browser-side OPTIMADE table used by the material search page."""

import os

from _internal import first_line, served_structure_definitions
from httk.serve.web.widgets.optimade_table import render as render_optimade_table


def _with_descriptions(columns):
    """Add each served property's first description line as a column hover hint."""
    definitions = served_structure_definitions()
    described = []
    for column in columns:
        description = first_line(definitions.get(column["key"], {}).get("description"))
        described.append({**column, "description": description} if description is not None else column)
    return tuple(described)


def render(context, **props):
    """Render the site's nine-column OPTIMADE search table."""

    del props
    base_url = os.environ.get("ALTERMAGNETS_OPTIMADE_BASE_URL", "/optimade/amdb").rstrip("/") or "/"
    return render_optimade_table(
        context,
        base_url=base_url,
        columns=_with_descriptions(
            (
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
            )
        ),
        page_size=50,
        page_size_query="page_size",
        filter_query="filter",
        sort_query="sort",
        # The widget resolves these human-facing sort aliases to OPTIMADE sorts, so a stray
        # `sort=screening_rank` from a non-JS navigation is translated, never sent verbatim.
        sort_aliases={
            "screening_rank": "id",
            "max_ss_desc": "-_anyterial_max_spin_splitting,id",
            "avg_ss_desc": "-_anyterial_avg_spin_splitting,id",
            "bandgap_desc": "-_httk_dft_band_gap,id",
            "abundance_desc": "-_anyterial_min_crustal_abundance,-_anyterial_max_spin_splitting,id",
        },
        caption="Screened altermagnet search results",
        advanced_filter={"help_url": context.url_for("fields")},
        detail_route="material",
        detail_column="_anyterial_formula",
        detail_query="id",
        summary={
            "noun": "screened entries",
            "fields": {
                "_anyterial_search_text": {"label": "Text"},
                "_anyterial_elements": {"label": "Elements"},
                "_anyterial_space_group_search": {"label": "Space group"},
                "_anyterial_magnetic_phases": {"label": "Phase"},
                "_anyterial_wave_classes": {"label": "Wave class"},
                # Plain-text labels for the pill/sort summary; the LaTeX column labels are never
                # KaTeX-typeset in the summary block, so they must not be inherited there.
                "_anyterial_max_spin_splitting": {"label": "Max spin splitting"},
                "_anyterial_avg_spin_splitting": {"label": "Avg spin splitting"},
                # Option labels duplicated from src/functions/init.py search_options
                # (source of truth); that list is a local literal with no importable handle.
                "_anyterial_classification": {
                    "values": {
                        "collinear": "Collinear",
                        "noncollinear-derived": "Based on noncollinear",
                        "mixed": "Both",
                        "unclassified": "Not classified yet",
                    }
                },
                "_anyterial_electronic_type": {
                    "label": "KS Gap Type",
                    "values": {
                        "metallic": "Metallic",
                        "semiconducting": "Semiconducting",
                        "unknown": "KS gap unavailable",
                    },
                },
                "_anyterial_min_crustal_abundance": {"format": {"name": "number", "digits": 1, "suffix": " ppm"}},
            },
        },
    )
