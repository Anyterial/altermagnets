def execute(global_data, id: str = "", **kwargs):
    database = global_data.get("materials_db", [])
    if not isinstance(database, list):
        return None

    target = (id or "").strip().lower()
    if not target:
        return None

    for entry in database:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("id", "")).lower() == target:
            return entry
    return None
