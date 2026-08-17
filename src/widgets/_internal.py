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


@lru_cache(maxsize=1)
def served_structure_definitions() -> dict[str, dict[str, Any]]:
    """Return the ``structures`` property definitions the AMDB service serves.

    Reuses the service's own schema-building code path: the store-native
    :class:`AltermagnetStructureEntry` definition, the service's sortable set,
    and its public-schema projection (which hides the storage-only public-id and
    reference-id properties). The result is the ``{served_name: definition}``
    mapping the ``optimade_fields`` widget expects, evaluated once and cached.
    """
    _ensure_site_imports()
    import material_store
    from httk.serve.optimade.schema.served import build_served_schema
    from optimade.adapter import SORTABLE_PROPERTIES, _public_store_schema

    schema = build_served_schema(
        {"structures": material_store.AltermagnetStructureEntry.entry_type_definition()},
        sortable=SORTABLE_PROPERTIES,
    )
    return _public_store_schema(schema).property_definitions["structures"]


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
