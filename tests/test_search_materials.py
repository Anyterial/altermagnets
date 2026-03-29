import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "functions" / "search_materials.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("search_materials", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_float_rejects_non_finite_values() -> None:
    assert MODULE._parse_float("nan") is None
    assert MODULE._parse_float("inf") is None
    assert MODULE._parse_float("-inf") is None


def test_text_tokens_are_bounded() -> None:
    long_token = "x" * 200
    payload = " ".join([long_token for _ in range(100)])
    tokens = MODULE._text_tokens(payload)
    assert len(tokens) == MODULE.MAX_TEXT_TOKENS
    assert all(len(token) <= MODULE.MAX_TEXT_TOKEN_LENGTH for token in tokens)


def test_canonical_element_tokens_are_bounded() -> None:
    payload = ",".join(["manganese" for _ in range(100)])
    tokens = MODULE._canonical_element_tokens(payload)
    assert len(tokens) == MODULE.MAX_ELEMENT_TOKENS
    assert all(len(token) <= MODULE.MAX_ELEMENT_TOKEN_LENGTH for token in tokens)
