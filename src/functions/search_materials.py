from typing import Any


def _normalized_tokens(query: str) -> list[str]:
    cleaned = query.replace(",", " ").strip().lower()
    if not cleaned:
        return []
    return [token for token in cleaned.split() if token]


def _haystack(entry: dict[str, Any]) -> str:
    fields = [
        str(entry.get("id", "")),
        str(entry.get("name", "")),
        str(entry.get("formula", "")),
        str(entry.get("spacegroup", "")),
        str(entry.get("order", "")),
        " ".join(str(x) for x in entry.get("elements", [])),
    ]
    return " ".join(fields).lower()


def execute(global_data, q: str = "", **kwargs):
    database = global_data.get("materials_db", [])
    if not isinstance(database, list):
        return []

    tokens = _normalized_tokens(q)
    if not tokens:
        return list(database)

    results: list[dict[str, Any]] = []
    for entry in database:
        if not isinstance(entry, dict):
            continue
        text = _haystack(entry)
        if all(token in text for token in tokens):
            results.append(entry)
    return results
