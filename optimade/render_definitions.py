#!/usr/bin/env python3
"""Render the altermagnets OPTIMADE property-definition YAML sources to JSON.

Each source under ``property_definitions/<name>.yaml`` is a self-contained
OPTIMADE property definition authored with the optimade-property-yaml skill
(so it validates with that skill's ``validate_yaml.py``). Rendering is a
faithful ``yaml.safe_load`` -> ``json.dump`` with a single sanctioned
normalization: the skill's source-schema meta key ``$$schema`` becomes the
published ``$schema``. Any other ``$$``-prefixed key (notably ``$$inherit``) is
refused, guaranteeing the rendered JSON is self-contained and directly loadable
via :meth:`httk.core.PropertyDefinition.from_optimade`.
"""

import json
import sys
from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
SOURCE_DIR = HERE / "property_definitions"
JSON_DIR = SOURCE_DIR / "json"


def _reject_dollar_dollar(node: Any, path: str) -> None:
    """Raise if any ``$$``-prefixed key remains anywhere in ``node``."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.startswith("$$"):
                raise ValueError(
                    f"Refusing to render {path}: unexpected source-schema key {key!r}. "
                    "Definitions must be self-contained (no $$inherit); only a top-level "
                    "$$schema is allowed and is normalized to $schema."
                )
            _reject_dollar_dollar(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_dollar_dollar(value, f"{path}[{index}]")


def render_document(document: dict[str, Any], source_name: str) -> dict[str, Any]:
    """Normalize a loaded YAML definition into a published-JSON document."""
    rendered: dict[str, Any] = {}
    for key, value in document.items():
        rendered["$schema" if key == "$$schema" else key] = value
    _reject_dollar_dollar(rendered, source_name)
    return rendered


def render_all() -> list[Path]:
    """Render every ``*.yaml`` source to ``json/<name>.json``; return the written paths."""
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sorted(SOURCE_DIR.glob("*.yaml")):
        with source.open(encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
        if not isinstance(document, dict):
            raise ValueError(f"{source.name}: top-level YAML document must be a mapping.")
        rendered = render_document(document, source.name)
        target = JSON_DIR / f"{source.stem}.json"
        with target.open("w", encoding="utf-8") as handle:
            json.dump(rendered, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        written.append(target)
    return written


def main() -> int:
    written = render_all()
    for target in written:
        print(f"rendered {target.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
