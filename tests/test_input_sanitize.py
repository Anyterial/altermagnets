import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "functions" / "input_sanitize.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("input_sanitize", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_sanitize_search_inputs_preserves_valid_fields() -> None:
    sanitized = MODULE.sanitize_search_inputs(
        {
            "q": "CrSb spin splitting",
            "elements": "Mn, O, Co",
            "space_group": "P2_1/c",
            "classification": "collinear",
            "sort": "max_ss_desc",
            "min_max_ss": "0.25",
        }
    )
    assert sanitized["q"] == "CrSb spin splitting"
    assert sanitized["elements"] == "Mn, O, Co"
    assert sanitized["space_group"] == "P2_1/c"
    assert sanitized["classification"] == "collinear"
    assert sanitized["sort"] == "max_ss_desc"
    assert sanitized["min_max_ss"] == "0.25"


def test_sanitize_search_inputs_drops_unexpected_chars_and_values() -> None:
    sanitized = MODULE.sanitize_search_inputs(
        {
            "q": "'<script>alert(1)</script>`",
            "space_group": "P2_1/c' OR 1=1 --",
            "classification": "collinear;drop",
            "sort": "max_ss_desc;drop table",
            "min_bandgap": "1e2; rm -rf /",
        }
    )
    assert "<" not in sanitized["q"]
    assert ">" not in sanitized["q"]
    assert "'" not in sanitized["q"]
    assert sanitized["classification"] == ""
    assert sanitized["sort"] == "screening_rank"
    assert sanitized["min_bandgap"] == "1e2"


def test_sanitize_material_id_accepts_expected_pattern_only() -> None:
    assert MODULE.sanitize_material_id("anyt:amdb-1-0001") == "anyt:amdb-1-0001"
    assert MODULE.sanitize_material_id("amdb-1-0001") == "amdb-1-0001"
    assert MODULE.sanitize_material_id("../../etc/passwd") == ""
    assert MODULE.sanitize_material_id("anyt:../../etc/passwd") == ""
    assert MODULE.sanitize_material_id("amdb-1-0001<script>") == ""
