import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from material_store import _build_coupling, details_raw_path

_HEADER = "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"


def _tables(
    root: Path,
    material: str = "Ba3CoSb2O9",
    amdb_ids: tuple[str, ...] = ("anyt:am-1-0001",),
    *,
    materials: dict[str, str] | None = None,
) -> Path:
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
                    "Material": material if materials is None else materials[amdb_id],
                    "Space group": "",
                    "FdeltaPct": "",
                    "MaxSS": "",
                    "AvgSS": "",
                    "Bandgap": "",
                    "MinAbundPpm": "",
                }
            )
    return root


def _run(
    material: str,
    run_id: str = "run-1",
    structure_id: str = "structure-1",
    raw_path: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        material=material,
        run_id=run_id,
        structure_id=structure_id,
        structure=object(),
        raw_path=raw_path,
        item=object(),
    )


def _details(root: Path, amdb_id: str, raw_path: str) -> Path:
    # Mirror the real shard layout: details/am-1/<d0>/<d0d1>/<d0d1d2>/am-1-NNNN/am-1-NNNN.json
    number = amdb_id.rsplit("-", 1)[1]
    shard = root / "am-1" / number[:1] / number[:2] / number[:3] / f"am-1-{number}"
    shard.mkdir(parents=True)
    (shard / f"am-1-{number}.json").write_text(json.dumps({"raw_path": raw_path}), encoding="utf-8")
    return root


def _absent_details(tmp_path: Path) -> Path:
    return tmp_path / "details"


def test_auto_coupling(tmp_path: Path) -> None:
    coupled, counts = _build_coupling(
        _tables(tmp_path / "tables", "CrSb"), (_run("CrSb"),), details_dir=_absent_details(tmp_path)
    )
    assert counts == {"auto": 1}
    assert coupled["anyt:am-1-0001"].run_id == "run-1"


def test_suffixed_variant_is_ambiguous(tmp_path: Path) -> None:
    coupled, counts = _build_coupling(
        _tables(tmp_path / "tables"),
        (_run("Ba3CoSb2O9"), _run("Ba3CoSb2O9-2", "run-2", "structure-2")),
        details_dir=_absent_details(tmp_path),
    )
    assert not coupled
    assert counts == {"ambiguous": 1}


def test_present_content_id_mismatch_is_hard_error(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;CrSb;;wrong;run-1;curated\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/CrSb"):
        _build_coupling(tables, (_run("CrSb"),), details_dir=_absent_details(tmp_path))


def test_changed_run_is_hard_error(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;CrSb;;structure-old;run-old;curated\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/CrSb"):
        _build_coupling(tables, (_run("CrSb", "run-new", "structure-new"),), details_dir=_absent_details(tmp_path))


def test_absent_run_row_is_preserved(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    row = "anyt:am-1-0001;CrSb;;structure-old;run-old;curated"
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n" + row + "\n",
        encoding="utf-8",
    )
    _build_coupling(tables, (), details_dir=_absent_details(tmp_path))
    assert row in (tables / "amdb_run_content_ids.csv").read_text(encoding="utf-8")


def test_duplicate_csv_formula_is_ambiguous_for_each_amdb_id(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "TmVO3", ("anyt:am-1-0001", "anyt:am-1-0002"))
    coupled, counts = _build_coupling(tables, (_run("TmVO3"),), details_dir=_absent_details(tmp_path))
    assert not coupled
    assert counts == {"ambiguous": 2}
    assert all(
        not row["structure_content_id"]
        for row in csv.DictReader((tables / "amdb_run_content_ids.csv").open(encoding="utf-8"), delimiter=";")
    )


def test_cross_material_row_is_rejected(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;MnTe;;structure;run;curated\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/MnTe"):
        _build_coupling(tables, (), details_dir=_absent_details(tmp_path))


def test_csv_row_without_run_warns_and_is_omitted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    coupled, counts = _build_coupling(
        _tables(tmp_path / "tables", "Missing"), (), details_dir=_absent_details(tmp_path)
    )
    assert not coupled and not counts
    assert "No ingested run" in caplog.text
    assert (tmp_path / "tables" / "amdb_run_content_ids.csv").read_text(encoding="utf-8").splitlines() == [
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status"
    ]


def test_details_raw_path_creates_auto_row(tmp_path: Path) -> None:
    # The details raw_path couples a run whose derived name never matches the CSV.
    tables = _tables(tmp_path / "tables", "Cu2H3ClO3")
    details = _details(tmp_path / "details", "am-1-0001", "9/Runs/ht.task.foo.Cu2O3Cl_SCF.finished")
    coupled, counts = _build_coupling(
        tables,
        (_run("Cu2O3Cl", "run-x", "structure-x", raw_path="9/Runs/ht.task.foo.Cu2O3Cl_SCF.finished"),),
        details_dir=details,
    )
    assert counts == {"auto": 1}
    assert coupled["anyt:am-1-0001"].run_id == "run-x"
    written = list(csv.DictReader((tables / "amdb_run_content_ids.csv").open(encoding="utf-8"), delimiter=";"))
    assert written[0]["raw_path"] == "9/Runs/ht.task.foo.Cu2O3Cl_SCF.finished"
    assert written[0]["run_material"] == "Cu2O3Cl"


def test_raw_path_row_matches_despite_name_disagreement(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "Cu2H3ClO3")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;Cu2O3Cl;9/Runs/task;sid;rid;auto\n",
        encoding="utf-8",
    )
    coupled, counts = _build_coupling(
        tables,
        (_run("Cu2O3Cl", "rid", "sid", raw_path="9/Runs/task"),),
        details_dir=_absent_details(tmp_path),
    )
    assert counts == {"auto": 1}
    assert coupled["anyt:am-1-0001"].structure_id == "sid"


def test_raw_path_row_absent_from_build_is_preserved(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    row = "anyt:am-1-0001;CrSb;9/Runs/gone;structure-old;run-old;curated"
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n" + row + "\n",
        encoding="utf-8",
    )
    coupled, _ = _build_coupling(tables, (), details_dir=_absent_details(tmp_path))
    assert not coupled
    assert row in (tables / "amdb_run_content_ids.csv").read_text(encoding="utf-8")


def test_refresh_rewrites_stale_pin(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;CrSb;9/Runs/task;structure-old;run-old;auto\n",
        encoding="utf-8",
    )
    coupled, counts = _build_coupling(
        tables,
        (_run("CrSb", "run-new", "structure-new", raw_path="9/Runs/task"),),
        details_dir=_absent_details(tmp_path),
        refresh_coupling=True,
    )
    assert counts == {"auto": 1}
    assert coupled["anyt:am-1-0001"].run_id == "run-new"
    written = list(csv.DictReader((tables / "amdb_run_content_ids.csv").open(encoding="utf-8"), delimiter=";"))
    assert written[0]["raw_path"] == "9/Runs/task"
    assert written[0]["status"] == "auto"
    assert written[0]["structure_content_id"] == "structure-new"
    assert written[0]["run_content_id"] == "run-new"


def test_stale_pin_without_refresh_raises(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        "AMDBId;run_material;raw_path;structure_content_id;run_content_id;status\n"
        "anyt:am-1-0001;CrSb;9/Runs/task;structure-old;run-old;auto\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="anyt:am-1-0001/CrSb"):
        _build_coupling(
            tables,
            (_run("CrSb", "run-new", "structure-new", raw_path="9/Runs/task"),),
            details_dir=_absent_details(tmp_path),
        )


def test_refresh_is_idempotent_with_non_screening_run_material(tmp_path: Path) -> None:
    # 1a: the shipped ambiguous row carries a run_material absent from the screening
    # CSV (the Cu2O3Cl-2 shape); a second refresh must not raise and must be stable.
    tables = _tables(tmp_path / "tables", "Cu2O3Cl", ("anyt:am-1-0007",))
    (tables / "amdb_run_content_ids.csv").write_text(_HEADER + "anyt:am-1-0007;Cu2O3Cl-2;;;;ambiguous\n", "utf-8")
    observations = (_run("Cu2O3Cl-2", raw_path="1/Runs/x"),)
    _build_coupling(tables, observations, details_dir=_absent_details(tmp_path), refresh_coupling=True)
    first = (tables / "amdb_run_content_ids.csv").read_text(encoding="utf-8")
    _build_coupling(tables, observations, details_dir=_absent_details(tmp_path), refresh_coupling=True)
    second = (tables / "amdb_run_content_ids.csv").read_text(encoding="utf-8")
    assert first == second
    assert "anyt:am-1-0007;Cu2O3Cl-2;;;;ambiguous" in first


def test_two_rows_sharing_one_raw_path_raise(tmp_path: Path) -> None:
    # 1b: one run must not back two materials.
    tables = _tables(tmp_path / "tables", "CrSb", ("anyt:am-1-0001", "anyt:am-1-0002"))
    (tables / "amdb_run_content_ids.csv").write_text(
        _HEADER + "anyt:am-1-0001;CrSb;1/Runs/x;sid;rid;curated\n" + "anyt:am-1-0002;CrSb;1/Runs/x;sid;rid;curated\n",
        "utf-8",
    )
    with pytest.raises(ValueError, match="one run to multiple materials"):
        _build_coupling(
            tables, (_run("CrSb", "rid", "sid", raw_path="1/Runs/x"),), details_dir=_absent_details(tmp_path)
        )


@pytest.mark.parametrize("order", [("anyt:am-1-0001", "anyt:am-1-0002"), ("anyt:am-1-0002", "anyt:am-1-0001")])
def test_rule_e_authoritative_beats_name_match_both_orders(tmp_path: Path, order: tuple[str, str]) -> None:
    # 1c: details owns run P for A; B (no details) name-matches P. A must win, B must not,
    # regardless of the row/screening order.
    tables = _tables(
        tmp_path / "tables",
        amdb_ids=order,
        materials={"anyt:am-1-0001": "Amat", "anyt:am-1-0002": "Bmat"},
    )
    details = _details(tmp_path / "details", "am-1-0001", "1/Runs/P")
    (tables / "amdb_run_content_ids.csv").write_text(_HEADER + "anyt:am-1-0002;Bmat;;old-s;old-r;auto\n", "utf-8")
    observations = (_run("Bmat", "rid", "sid", raw_path="1/Runs/P"),)
    coupled, _ = _build_coupling(tables, observations, details_dir=details, refresh_coupling=True)
    assert coupled["anyt:am-1-0001"].raw_path == "1/Runs/P"
    assert "anyt:am-1-0002" not in coupled


def test_wrong_runs_root_raises_even_when_all_tasks_are_filtered(tmp_path: Path) -> None:
    # 1d/fix-1: a wrong root (e.g. <root>/1/Runs) collects tasks whose one-part
    # payloads are all rejected by collect()'s 3a check, so observations is empty but
    # collected > 0. The guard must discriminate on collected, not observations.
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        _HEADER + "anyt:am-1-0001;CrSb;1/Runs/x;sid;rid;curated\n", "utf-8"
    )
    with pytest.raises(ValueError, match="no coupling raw_path resolved"):
        _build_coupling(
            tables,
            (),  # every collected task was filtered out by 3a
            details_dir=_absent_details(tmp_path),
            runs_root=tmp_path / "1" / "Runs",
            collected=30,
        )


def test_empty_tree_stays_silent(tmp_path: Path) -> None:
    # 1d/fix-1: an absent or genuinely empty tree collects nothing and must not raise.
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        _HEADER + "anyt:am-1-0001;CrSb;1/Runs/x;sid;rid;curated\n", "utf-8"
    )
    coupled, _ = _build_coupling(tables, (), details_dir=_absent_details(tmp_path), collected=0)
    assert not coupled  # row preserved, no exception


def test_partial_raw_path_miss_only_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 1d: a partial transfer (some runs present) preserves the absent rows without raising.
    tables = _tables(tmp_path / "tables", "CrSb", ("anyt:am-1-0001", "anyt:am-1-0002"))
    (tables / "amdb_run_content_ids.csv").write_text(
        _HEADER
        + "anyt:am-1-0001;CrSb;1/Runs/a;sid;rid;curated\n"
        + "anyt:am-1-0002;CrSb;1/Runs/b;other-s;other-r;curated\n",
        "utf-8",
    )
    with caplog.at_level("WARNING"):
        _build_coupling(
            tables, (_run("CrSb", "rid", "sid", raw_path="1/Runs/a"),), details_dir=_absent_details(tmp_path)
        )
    assert "not collected in this build: 1/Runs/b" in caplog.text


def test_unexpected_column_raises(tmp_path: Path) -> None:
    # 1f: a curator's hand-added column must not be silently dropped.
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(
        _HEADER.rstrip("\n") + ";note\n" + "anyt:am-1-0001;CrSb;;;;ambiguous;keep-me\n", "utf-8"
    )
    with pytest.raises(ValueError, match="unexpected columns"):
        _build_coupling(tables, (), details_dir=_absent_details(tmp_path))


def _curated_row(tables: Path, amdb_id: str = "anyt:am-1-0001") -> dict[str, str]:
    reader = csv.DictReader((tables / "amdb_run_content_ids.csv").open(encoding="utf-8"), delimiter=";")
    return {row["AMDBId"]: row for row in reader}[amdb_id]


def test_pending_curated_row_survives_and_is_never_demoted(tmp_path: Path) -> None:
    # 1g (corrected): a curated row with a raw_path and no content-ids is the natural
    # hand-curation shape (the curator cannot know the ids). It must survive both a
    # refresh and a plain build with its status intact while the run is absent, and
    # get its ids filled by a later refresh once the run is present -- never demoted.
    # A sibling material with a present run keeps resolved>0 so the 1d guard stays quiet.
    tables = _tables(
        tmp_path / "tables",
        materials={"anyt:am-1-0001": "CrSb", "anyt:am-1-0002": "MnTe"},
        amdb_ids=("anyt:am-1-0001", "anyt:am-1-0002"),
    )
    (tables / "amdb_run_content_ids.csv").write_text(
        _HEADER + "anyt:am-1-0001;CrSb;1/Runs/x;;;curated\n" + "anyt:am-1-0002;MnTe;1/Runs/mnte;s2;r2;auto\n",
        "utf-8",
    )
    present = (_run("MnTe", "r2", "s2", raw_path="1/Runs/mnte"),)  # CrSb run stays absent

    # Run absent: refresh keeps the pending row exactly, no exception, no coupling of it.
    coupled, _ = _build_coupling(
        tables, present, details_dir=_absent_details(tmp_path), collected=5, refresh_coupling=True
    )
    assert "anyt:am-1-0001" not in coupled
    kept = _curated_row(tables)
    assert kept["status"] == "curated" and kept["raw_path"] == "1/Runs/x" and not kept["structure_content_id"]

    # Run absent: a plain build also keeps it (a legal pending curation, not a booby trap).
    _build_coupling(tables, present, details_dir=_absent_details(tmp_path), collected=5)
    assert _curated_row(tables)["status"] == "curated"

    # Run present: refresh fills the ids and the status stays curated.
    coupled, _ = _build_coupling(
        tables,
        present + (_run("CrSb", "r9", "s9", raw_path="1/Runs/x"),),
        details_dir=_absent_details(tmp_path),
        refresh_coupling=True,
    )
    filled = _curated_row(tables)
    assert filled["status"] == "curated"
    assert (filled["structure_content_id"], filled["run_content_id"]) == ("s9", "r9")
    assert coupled["anyt:am-1-0001"].run_id == "r9"


def test_name_only_active_row_without_content_ids_still_raises(tmp_path: Path) -> None:
    # A row with no raw_path has nothing to resolve against; empty ids must raise
    # (in both modes) rather than become a silent pending row.
    tables = _tables(tmp_path / "tables", "CrSb")
    (tables / "amdb_run_content_ids.csv").write_text(_HEADER + "anyt:am-1-0001;CrSb;;;;curated\n", "utf-8")
    with pytest.raises(ValueError, match="active rows require content-ids"):
        _build_coupling(tables, (), details_dir=_absent_details(tmp_path), refresh_coupling=True)
    with pytest.raises(ValueError, match="active rows require content-ids"):
        _build_coupling(tables, (), details_dir=_absent_details(tmp_path))


def test_details_raw_path_absent_malformed_and_wrong_type(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # 1e: never raises; absent shard is debug, present-but-unusable shard warns.
    root = tmp_path / "details"
    assert details_raw_path(root, "anyt:am-1-0001") == ""  # no shard at all

    def _shard(amdb_id: str, contents: str) -> None:
        number = amdb_id.rsplit("-", 1)[1]
        shard = root / "am-1" / number[:1] / number[:2] / number[:3] / f"am-1-{number}"
        shard.mkdir(parents=True)
        (shard / f"am-1-{number}.json").write_text(contents, encoding="utf-8")

    _shard("am-1-0002", "{ this is not json")
    _shard("am-1-0003", json.dumps({"raw_path": 5}))
    with caplog.at_level("WARNING"):
        assert details_raw_path(root, "anyt:am-1-0002") == ""
        assert details_raw_path(root, "anyt:am-1-0003") == ""
    assert "unreadable" in caplog.text
    assert "malformed or missing" in caplog.text
