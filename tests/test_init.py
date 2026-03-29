import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "src" / "functions" / "init.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("init", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_summarize_symmetry_rows_splits_entries_by_symprec() -> None:
    rows = [
        {
            "MAGNDATAId": "0.528",
            "Symprec": "0.001",
            "ChemicalFormula": "CrSb",
            "MagneticPhaseShort": "AM",
            "WaveClassSimple": "d",
            "WaveClass": "d",
            "ParentSpacegroup": "P6_3/mmc",
            "BNSmcif": "P_Cc2_1",
            "BNS": "1.1",
            "EffectiveBNS": "1.1",
            "GMagneticSystemLaueClass": "6/mmm",
            "HHalvingSubgroupLaueClass": "mmm",
            "AGenopConnectingElement": "2_z",
            "SpinAngleMismatch": "0.5",
            "SpinLengthMismatch": "0.1",
            "ICSDId": "123",
            "ReferenceDOI": "10.1000/example-1",
            "Warnings": "",
            "Notes": "",
        },
        {
            "MAGNDATAId": "0.528",
            "Symprec": "0.010",
            "ChemicalFormula": "CrSb",
            "MagneticPhaseShort": "AM",
            "WaveClassSimple": "g",
            "WaveClass": "g",
            "ParentSpacegroup": "P6_3/mmc",
            "BNSmcif": "P_Cc2_1",
            "BNS": "1.1",
            "EffectiveBNS": "1.1",
            "GMagneticSystemLaueClass": "6/mmm",
            "HHalvingSubgroupLaueClass": "mmm",
            "AGenopConnectingElement": "2_z",
            "SpinAngleMismatch": "0.2",
            "SpinLengthMismatch": "0.05",
            "ICSDId": "123",
            "ReferenceDOI": "10.1000/example-1",
            "Warnings": "",
            "Notes": "",
        },
    ]

    summaries = MODULE._summarize_symmetry_rows(rows, source_kind="collinear")

    assert len(summaries) == 2
    assert [entry["symprec"] for entry in summaries] == [0.001, 0.01]
    assert summaries[0]["wave_classes"] == ["d"]
    assert summaries[1]["wave_classes"] == ["g"]
