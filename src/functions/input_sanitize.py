import re
import string

# Keep the base policy strict against control chars and obvious HTML/script vectors.
BASE_EXCLUDED_CHARS = {"`", "\\", "<", ">"}
ASCII_PRINTABLE = set(string.printable) - {"\x0b", "\x0c", "\r", "\n", "\t"}

SEARCH_ENUMS: dict[str, set[str]] = {
    "classification": {"", "collinear", "noncollinear-derived", "mixed", "unclassified"},
    "electronic_type": {"", "metallic", "semiconducting", "unknown"},
    "magnetic_phase": {"", "AM", "Luttinger ferrimagnet", "weakly-canted AFM", "FiM", "non-AM"},
    "wave_class": {"", "a", "b", "c", "d", "e", "f", "g", "d/g", "s"},
    "sort": {"screening_rank", "max_ss_desc", "avg_ss_desc", "bandgap_desc", "abundance_desc"},
}

NUMERIC_FIELDS = {
    "min_max_ss",
    "min_avg_ss",
    "min_fdelta_pct",
    "min_bandgap",
    "max_bandgap",
    "min_abundance_ppm",
}
NUMERIC_TOKEN_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
MATERIAL_ID_PATTERN = re.compile(r"^amdb-(?:\d+-)?\d+$")

MAX_LENGTH_BY_FIELD: dict[str, int] = {
    "q": 256,
    "elements": 128,
    "classification": 40,
    "electronic_type": 40,
    "magnetic_phase": 80,
    "wave_class": 8,
    "space_group": 80,
    "sort": 32,
    "min_max_ss": 32,
    "min_avg_ss": 32,
    "min_fdelta_pct": 32,
    "min_bandgap": 32,
    "max_bandgap": 32,
    "min_abundance_ppm": 32,
    "id": 32,
}


def _is_ascii_printable(ch: str) -> bool:
    return ch in ASCII_PRINTABLE


def sanitize_text(
    value: object,
    *,
    max_length: int,
    extra_excluded_chars: set[str] | None = None,
) -> str:
    if not isinstance(value, str):
        return ""
    excluded = BASE_EXCLUDED_CHARS | (extra_excluded_chars or set())
    cleaned = "".join(ch for ch in value if _is_ascii_printable(ch) and ch not in excluded)
    return cleaned.strip()[:max_length]


def sanitize_search_inputs(raw: dict[str, object]) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in raw.items():
        if key not in MAX_LENGTH_BY_FIELD:
            continue

        # For free-text fields we drop quote chars as an extra hardening layer.
        extra_excluded = {"'", '"'} if key in {"q", "space_group"} else set()
        cleaned = sanitize_text(
            value,
            max_length=MAX_LENGTH_BY_FIELD[key],
            extra_excluded_chars=extra_excluded,
        )

        if key in SEARCH_ENUMS:
            allowed_values = SEARCH_ENUMS[key]
            sanitized[key] = cleaned if cleaned in allowed_values else ""
            continue

        if key in NUMERIC_FIELDS:
            numeric_match = NUMERIC_TOKEN_PATTERN.search(cleaned)
            sanitized[key] = numeric_match.group(0) if numeric_match else ""
            continue

        sanitized[key] = cleaned

    for key in MAX_LENGTH_BY_FIELD:
        if key == "id":
            continue
        sanitized.setdefault(key, "")
    sanitized.setdefault("sort", "screening_rank")
    if sanitized["sort"] not in SEARCH_ENUMS["sort"]:
        sanitized["sort"] = "screening_rank"
    return sanitized


def sanitize_material_id(value: object) -> str:
    cleaned = sanitize_text(value, max_length=MAX_LENGTH_BY_FIELD["id"], extra_excluded_chars={"'", '"'})
    if MATERIAL_ID_PATTERN.fullmatch(cleaned):
        return cleaned
    return ""
