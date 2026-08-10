import init
from material_store import summarize_symmetry_rows


def test_init_only_registers_static_search_options() -> None:
    data = {}
    init.execute(data)
    assert data["search_options"]["classifications"][1] == {"value": "collinear", "label": "Collinear"}
    assert set(data) == {"search_options"}


def test_summarize_symmetry_rows_splits_entries_by_symprec() -> None:
    rows = [
        {
            "MAGNDATAId": "0.528",
            "Symprec": "0.001",
            "ChemicalFormula": "CrSb",
            "MagneticPhaseShort": "AM",
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

    summaries = summarize_symmetry_rows(rows, source_kind="collinear")

    assert len(summaries) == 2
    assert [variant.symprec for _identifier, variant in summaries] == [0.001, 0.01]
    assert summaries[0][1].wave_classes == ("d",)
    assert summaries[1][1].wave_classes == ("g",)
