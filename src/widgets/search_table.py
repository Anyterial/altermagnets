"""Render the browser-side OPTIMADE table used by the material search page."""

import os

from _internal import first_line, result_entry_type, served_field_definitions
from httk.serve.web.widgets.optimade_table import render as render_optimade_table


def _with_descriptions(columns):
    """Add each served property's first description line as a column hover hint."""
    definitions = served_field_definitions()
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
        entry_type=result_entry_type(),
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
        sort="max_ss_desc",
        sort_query="sort",
        # The widget resolves these human-facing sort aliases to OPTIMADE sorts, so a stray
        # `sort=screening_rank` from a non-JS navigation is translated, never sent verbatim.
        # DEVIATION from the authored plan (see report): the plan specifies the legacy
        # `screening_rank` alias should map to "" (store order), but httk-serve's
        # `optimade_table._sort_aliases`/`_text` (a sibling package this batch does not
        # own) reject an empty alias VALUE outright -- `render()` raises
        # OptimadeTableProtocolError at page-render time for every request, not just
        # legacy ones. The new, explicit "" empty-sort option (search_options below /
        # amendment 3's removable default-sort pill) is unaffected: that path sends a
        # literal empty `sort=` query param, which `effectiveSort` short-circuits to no
        # sort BEFORE it ever reaches this alias table. Only this backward-compatibility
        # shim for old bookmarked/shared URLs needs a non-empty stand-in; "id" is a
        # always-sortable, always-valid, never-erroring proxy for "ID order" (the retired
        # option's own label), which is strictly better than the previous behavior (the
        # old alias pointed at `_anyterial_screening_rank`, which 400s server-side).
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
                # `clears` names the search-form.js (src/static/search-form.js buildQuery)
                # URL param(s) that produce each predicate, so a pill's "x" both drops the
                # predicate AND deletes those params -- otherwise search-form's redirect
                # normalizer would re-derive the same `filter` from the still-present form
                # params and resurrect it (amendment 3).
                "_anyterial_search_text": {"label": "Text", "clears": ["q"]},
                "_anyterial_elements": {"label": "Elements", "clears": ["elements"]},
                "_anyterial_space_group_search": {"label": "Space group", "clears": ["space_group"]},
                "_anyterial_magnetic_phases": {"label": "Phase", "clears": ["magnetic_phase"]},
                "_anyterial_wave_classes": {"label": "Wave class", "clears": ["wave_class"]},
                # Plain-text labels for the pill/sort summary; the LaTeX column labels are never
                # KaTeX-typeset in the summary block, so they must not be inherited there.
                "_anyterial_max_spin_splitting": {"label": "Max spin splitting", "clears": ["min_max_ss"]},
                "_anyterial_avg_spin_splitting": {"label": "Avg spin splitting", "clears": ["min_avg_ss"]},
                "_anyterial_spin_splitting_fraction": {"clears": ["min_fdelta_pct"]},
                # Band gap is the one two-sided range: both the >= and <= clauses (when both are
                # active, two separate pills on this SAME property) clear both source params, so
                # removing either pill also drops the other's now-orphaned param/predicate.
                "_httk_dft_band_gap": {"clears": ["min_bandgap", "max_bandgap"]},
                # Option labels duplicated from src/functions/init.py search_options
                # (source of truth); that list is a local literal with no importable handle.
                "_anyterial_classification": {
                    "values": {
                        "collinear": "Collinear",
                        "noncollinear-derived": "Based on noncollinear",
                        "mixed": "Both",
                        "unclassified": "Not classified yet",
                    },
                    "clears": ["classification"],
                },
                "_anyterial_electronic_type": {
                    "label": "KS Gap Type",
                    "values": {
                        "metallic": "Metallic",
                        "semiconducting": "Semiconducting",
                        "unknown": "KS gap unavailable",
                    },
                    "clears": ["electronic_type"],
                },
                "_anyterial_min_crustal_abundance": {
                    "format": {"name": "number", "digits": 1, "suffix": " ppm"},
                    "clears": ["min_abundance_ppm"],
                },
            },
        },
    )
