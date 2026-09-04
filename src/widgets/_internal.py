"""Shared helpers for site-local widgets."""

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
_FUNCTIONS_ROOT = _ROOT / "src" / "functions"


def _ensure_site_imports() -> None:
    """Make the repo-root ``optimade`` package and ``src/functions`` importable.

    Widgets are executed with only ``src/widgets`` on ``sys.path`` (the loader
    removes its temporary entries after module import), so the service package
    and its ``material_store`` dependency are not reachable by default. Mirror
    ``optimade/__init__.py``'s own self-insertion so the accessor works in serve,
    publish, and direct-import test contexts alike.
    """
    for path in (_FUNCTIONS_ROOT, _ROOT):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)


def result_entry_type() -> str:
    """Return the AMDB main entity's served (wire) entry type.

    Resolved lazily (the ``optimade`` package is only importable once
    :func:`_ensure_site_imports` has run) so widget modules can name the
    screening-result endpoint without a load-time dependency on the service.
    """
    _ensure_site_imports()
    from optimade.adapter import RESULT_TYPE

    return RESULT_TYPE


@lru_cache(maxsize=1)
def served_field_definitions() -> dict[str, dict[str, Any]]:
    """Return the property definitions the AMDB service serves, merged across both types.

    Reuses the service's own schema-building code path over the two served
    entry types: the AMDB main entity (:class:`AltermagnetScreeningResultEntry`,
    which owns the screening science, figures and energy) and the slim standard
    :class:`AltermagnetStructureEntry` (which owns the CrysViz structural fields
    and ``_httk_site_moments``). The service's sortable set and its public-schema
    projection (which hides the storage-only public-id, reference-id and
    structure-id properties) are applied, then the two ``{served_name:
    definition}`` mappings are flattened into one — the shared identity keys
    (``id``/``type``/…) are byte-identical across types. Evaluated once, cached.
    """
    _ensure_site_imports()
    import material_store
    from httk.serve.optimade.schema.served import build_served_schema
    from optimade.adapter import RESULT_TYPE, SORTABLE_PROPERTIES, _public_store_schema

    schema = build_served_schema(
        {
            RESULT_TYPE: material_store.AltermagnetScreeningResultEntry.entry_type_definition(),
            "structures": material_store.AltermagnetStructureEntry.entry_type_definition(),
        },
        sortable=SORTABLE_PROPERTIES,
    )
    definitions = _public_store_schema(schema).property_definitions
    return {**definitions[RESULT_TYPE], **definitions["structures"]}


def first_line(description: object) -> str | None:
    """Return the first non-empty line of a property description, else ``None``."""
    if not isinstance(description, str):
        return None
    for line in description.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def safe_json(value: object) -> str:
    """Encode configuration safely inside a JSON script element."""
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"))
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
