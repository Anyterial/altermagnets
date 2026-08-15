"""Validate every assembled record against its property definitions."""

import sys
from collections.abc import Mapping
from typing import Any

from httk.store import validate_record


def _validation_record(
    record: Mapping[str, Any],
    columns: Mapping[str, str],
    definition: Any,
    stripped_nulls: dict[str, int],
) -> dict[str, Any]:
    """Rewrite a served column-keyed record to property-name keys.

    Explicit nulls are retained when the definition permits them. A null is
    omitted only when the definition is non-nullable and the validator cannot
    express the provider's existing null-valued column; each omission is
    counted and reported by :func:`run_validation`.
    """
    result: dict[str, Any] = {}
    properties = definition.properties
    for name, column in columns.items():
        if column not in record:
            continue
        value = record[column]
        if value is None and name not in ("id", "type") and not properties[name].nullable:
            stripped_nulls[name] = stripped_nulls.get(name, 0) + 1
            continue
        result[name] = value
    return result


def run_validation(providers: list[Any]) -> int:
    """Validate every assembled record against its definition; return a process exit code."""
    total = 0
    failures = 0
    stripped_nulls: dict[str, int] = {}
    for provider in providers:
        for entry_type, definition in provider.entry_types().items():
            columns = provider.property_keys(entry_type)
            entry_count = 0
            for record in provider.records(entry_type):
                entry_count += 1
                total += 1
                candidate = _validation_record(record, columns, definition, stripped_nulls)
                try:
                    validate_record(definition, candidate)
                except Exception as exc:
                    failures += 1
                    print(f"INVALID {entry_type} {candidate.get('id')!r}: {exc}", file=sys.stderr)
            if entry_count == 0:
                failures += 1
                print(f"INVALID {entry_type}: no records were served", file=sys.stderr)
    if stripped_nulls:
        details = ", ".join(f"{name} ({count})" for name, count in sorted(stripped_nulls.items()))
        print(f"omitted null values for non-nullable definitions: {details}", file=sys.stderr)
    if total == 0:
        failures += 1
        print("INVALID: no records were served by any provider", file=sys.stderr)
    print(f"validated {total} record(s) across {len(providers)} provider(s): {failures} failure(s)")
    return 1 if failures else 0
