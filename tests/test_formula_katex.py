import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "functions" / "formula_katex.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("formula_katex", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_katex_formula_inline_handles_basic_formula() -> None:
    assert MODULE.katex_formula_inline("CrSb") == r"$\mathrm{CrSb}$"


def test_katex_formula_inline_subscripts_stoichiometry() -> None:
    assert MODULE.katex_formula_inline("Fe2O3") == r"$\mathrm{Fe_{2}O_{3}}$"
    assert MODULE.katex_formula_inline("Fe0.5Mn0.5") == r"$\mathrm{Fe_{0.5}Mn_{0.5}}$"


def test_katex_formula_inline_handles_hydrate_dot() -> None:
    assert MODULE.katex_formula_inline("CuSO4·5H2O") == r"$\mathrm{CuSO_{4}\cdot 5H_{2}O}$"


def test_katex_formula_inline_passes_through_existing_math_and_empty() -> None:
    assert MODULE.katex_formula_inline("") == ""
    assert MODULE.katex_formula_inline("  ") == ""
    assert MODULE.katex_formula_inline("$\\mathrm{CrSb}$") == "$\\mathrm{CrSb}$"
