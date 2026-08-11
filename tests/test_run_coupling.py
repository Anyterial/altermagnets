import csv
from pathlib import Path
from types import SimpleNamespace

import pytest
from material_store import _build_coupling


def _tables(root: Path, material: str = "Ba3CoSb2O9", amdb_ids: tuple[str, ...] = ("anyt:am-1-0001",)) -> Path:
    root.mkdir()
    with (root / "high_throughput_screening_results_fixed.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "AMDBId",
                "MAGNDATA ID",
                "Material",
                "Space group",
                "FdeltaPct",
                "MaxSS",
                "AvgSS",
                "Bandgap",
                "MinAbundPpm",
            ),
            delimiter=";",
        )
        writer.writeheader()
        for amdb_id in amdb_ids:
            writer.writerow(
                {
                    "AMDBId": amdb_id,
                    "MAGNDATA ID": "",
                    "Material": material,
                    "Space group": "",
                    "FdeltaPct": "",
                    "MaxSS": "",
                    "AvgSS": "",
                    "Bandgap": "",
                    "MinAbundPpm": "",
                }
            )
    return root


def _run(material: str, run_id: str = "run-1", structure_id: str = "structure-1") -> SimpleNamespace:
    return SimpleNamespace(material=material, run_id=run_id, structure_id=structure_id, structure=object())


def test_auto_coupling(tmp_path: Path) -> None:
    coupled, counts = _build_coupling(_tables(tmp_path / "tables", "CrSb"), (_run("CrSb"),))
    assert counts == {"auto": 1}
    assert coupled["anyt:am-1-0001"].run_id == "run-1"


def test_suffixed_variant_is_ambiguous(tmp_path: Path) -> None:
    coupled, counts = _build_coupling(
        _tables(tmp_path / "tables"),
        (_run("Ba3CoSb2O9"), _run("Ba3CoSb2O9-2", "run-2", "structure-2")),
    )
    assert not coupled
    assert counts == {"ambiguous": 1}


def test_present_content_id_mismatch_is_hard_error(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;structure_content_id;run_content_id;status\nanyt:am-1-0001;CrSb;wrong;run-1;curated\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/CrSb"):
        _build_coupling(tables, (_run("CrSb"),))


def test_changed_run_is_hard_error(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;CrSb;structure-old;run-old;curated\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/CrSb"):
        _build_coupling(tables, (_run("CrSb", "run-new", "structure-new"),))


def test_absent_run_row_is_preserved(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    row = "anyt:am-1-0001;CrSb;structure-old;run-old;curated"
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;structure_content_id;run_content_id;status\n" + row + "\n",
        encoding="utf-8",
    )
    _build_coupling(tables, ())
    assert row in (tables / "amdb_run_content_ids.csv").read_text(encoding="utf-8")


def test_duplicate_csv_formula_is_ambiguous_for_each_amdb_id(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "TmVO3", ("anyt:am-1-0001", "anyt:am-1-0002"))
    coupled, counts = _build_coupling(tables, (_run("TmVO3"),))
    assert not coupled
    assert counts == {"ambiguous": 2}
    assert all(
        not row["structure_content_id"]
        for row in csv.DictReader((tables / "amdb_run_content_ids.csv").open(encoding="utf-8"), delimiter=";")
    )


def test_cross_material_row_is_rejected(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;structure_content_id;run_content_id;status\nanyt:am-1-0001;MnTe;structure;run;curated\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/MnTe"):
        _build_coupling(tables, ())


def test_csv_row_without_run_warns_and_is_omitted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    coupled, counts = _build_coupling(_tables(tmp_path / "tables", "Missing"), ())
    assert not coupled and not counts
    assert "No ingested run" in caplog.text
    assert (tmp_path / "tables" / "amdb_run_content_ids.csv").read_text(encoding="utf-8").splitlines() == [
        "AMDBId;run_material;structure_content_id;run_content_id;status"
    ]
