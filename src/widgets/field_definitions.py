"""Render the static table of OPTIMADE field definitions the AMDB service serves."""

from _internal import served_structure_definitions
from httk.serve.web.widgets.optimade_fields import render as render_optimade_fields


def render(context, **props):
    """Render the alphabetical field-definition table for the ``structures`` schema."""
    del props
    return render_optimade_fields(
        context,
        properties=served_structure_definitions(),
        caption="Field definitions for the altermagnets database",
    )
