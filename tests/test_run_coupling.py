"""Tests for run<->material matching and its ledger-binding resolution.

The old CSV-backed coupling document is gone (run-coupling-ledger-bindings design):
matching (details ``raw_path`` authority, name matching, the one-run-one-material
invariant, the diagnostics) is unchanged and tested here exactly as before, but a
match's disposition is now resolved against an open :class:`~httk.store.IdLedger`
(:func:`material_store._resolve_run_binding`) instead of written to a CSV row.
"""

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httk.core.project.sealing import resolve_seal_keys
from httk.store import IdLedger
from material_store import (
    LEDGER_BASES,
    LEDGER_SERIES,
    LEDGER_SIGNER_REFS,
    _couple_runs,
    _run_key,
    _run_source_key,
    details_raw_path,
)


def _ledger(tmp_path: Path) -> IdLedger:
    """Create and return a fresh, open, write-mode ledger under *tmp_path*."""
    path = tmp_path / "ids.sqlite"
    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    return IdLedger.create(path, bases=LEDGER_BASES, series=LEDGER_SERIES, keys=keys)


def _tables(
    root: Path,
    material: str = "Ba3CoSb2O9",
    amdb_ids: tuple[str, ...] = ("anyt.am-1-1",),
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


def _run(material: str, source_id: str = "httk-v1:run-1", raw_path: str = "") -> SimpleNamespace:
    """Return a fake ``_RunObservation``-shaped item (material, structure, raw_path, item)."""
    return SimpleNamespace(
        material=material,
        structure=object(),
        raw_path=raw_path,
        item=SimpleNamespace(run=SimpleNamespace(source_id=source_id), missing_collector=None),
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
    with _ledger(tmp_path) as ledger:
        coupled, counts = _couple_runs(
            _tables(tmp_path / "tables", "CrSb"),
            (_run("CrSb", "httk-v1:run-1"),),
            details_dir=_absent_details(tmp_path),
            ledger=ledger,
        )
        assert counts == {"auto": 1}
        assert ledger.lookup(_run_key("anyt.am-1-1")) == ledger.lookup(_run_source_key("httk-v1:run-1"))
    assert coupled["anyt.am-1-1"].item.run.source_id == "httk-v1:run-1"


def test_suffixed_variant_is_ambiguous(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with _ledger(tmp_path) as ledger:
        with caplog.at_level("WARNING"):
            coupled, counts = _couple_runs(
                _tables(tmp_path / "tables"),
                (_run("Ba3CoSb2O9", "s:1"), _run("Ba3CoSb2O9-2", "s:2")),
                details_dir=_absent_details(tmp_path),
                ledger=ledger,
            )
        assert not coupled
        assert counts == {"ambiguous": 1}
        assert ledger.lookup(_run_key("anyt.am-1-1")) is None
    assert "Ambiguous run match" in caplog.text


def test_duplicate_csv_formula_is_ambiguous_for_each_amdb_id(tmp_path: Path) -> None:
    tables = _tables(tmp_path / "tables", "TmVO3", ("anyt.am-1-1", "anyt.am-1-2"))
    with _ledger(tmp_path) as ledger:
        coupled, counts = _couple_runs(
            tables, (_run("TmVO3", "s:1"),), details_dir=_absent_details(tmp_path), ledger=ledger
        )
        assert not coupled
        assert counts == {"ambiguous": 2}


def test_details_raw_path_creates_binding(tmp_path: Path) -> None:
    # The details raw_path couples a run whose derived name never matches the CSV.
    tables = _tables(tmp_path / "tables", "Cu2H3ClO3")
    details = _details(tmp_path / "details", "am-1-0001", "9/Runs/ht.task.foo.Cu2O3Cl_SCF.finished")
    with _ledger(tmp_path) as ledger:
        coupled, counts = _couple_runs(
            tables,
            (_run("Cu2O3Cl", "s:x", raw_path="9/Runs/ht.task.foo.Cu2O3Cl_SCF.finished"),),
            details_dir=details,
            ledger=ledger,
        )
        assert counts == {"auto": 1}
        run_id = ledger.lookup(_run_source_key("s:x"))
        assert run_id is not None
        assert ledger.lookup(_run_key("anyt.am-1-1")) == run_id
    assert coupled["anyt.am-1-1"].item.run.source_id == "s:x"


@pytest.mark.parametrize("order", [("anyt.am-1-1", "anyt.am-1-2"), ("anyt.am-1-2", "anyt.am-1-1")])
def test_raw_path_beats_name_match_both_orders(tmp_path: Path, order: tuple[str, str]) -> None:
    # details owns run P for A; B (no details) name-matches P. A must win, B must not,
    # regardless of the row/screening order.
    tables = _tables(
        tmp_path / "tables",
        amdb_ids=order,
        materials={"anyt.am-1-1": "Amat", "anyt.am-1-2": "Bmat"},
    )
    details = _details(tmp_path / "details", "am-1-0001", "1/Runs/P")
    observations = (_run("Bmat", "s:p", raw_path="1/Runs/P"),)
    with _ledger(tmp_path) as ledger:
        coupled, _ = _couple_runs(tables, observations, details_dir=details, ledger=ledger)
        assert "anyt.am-1-1" in coupled
        assert "anyt.am-1-2" not in coupled
        assert ledger.lookup(_run_key("anyt.am-1-1")) is not None
        assert ledger.lookup(_run_key("anyt.am-1-2")) is None


def test_one_run_one_material_invariant(tmp_path: Path) -> None:
    # Two materials whose details shard both (erroneously) point at the same raw_path:
    # only the first (sorted) claims it; the second is warned off, never coupled.
    tables = _tables(
        tmp_path / "tables",
        amdb_ids=("anyt.am-1-1", "anyt.am-1-2"),
        materials={"anyt.am-1-1": "Amat", "anyt.am-1-2": "Bmat"},
    )
    details = tmp_path / "details"
    _details(details, "am-1-0001", "1/Runs/shared")
    _details(details, "am-1-0002", "1/Runs/shared")
    observations = (_run("Amat", "s:shared", raw_path="1/Runs/shared"),)
    with _ledger(tmp_path) as ledger:
        coupled, counts = _couple_runs(tables, observations, details_dir=details, ledger=ledger)
        assert set(coupled) == {"anyt.am-1-1"}
        assert counts == {"auto": 1}


def test_wrong_runs_root_raises_even_when_all_tasks_are_filtered(tmp_path: Path) -> None:
    # A wrong root (e.g. <root>/1/Runs) collects tasks whose one-part payloads are all
    # rejected by collect()'s 3a check, so observations is empty but collected > 0. The
    # guard must discriminate on collected vs. observations, not on any raw_path source.
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger, pytest.raises(ValueError, match="no runs observed"):
        _couple_runs(
            tables,
            (),  # every collected task was filtered out by 3a
            details_dir=_absent_details(tmp_path),
            ledger=ledger,
            runs_root=tmp_path / "1" / "Runs",
            collected=30,
        )


def test_empty_tree_stays_silent(tmp_path: Path) -> None:
    # An absent or genuinely empty tree collects nothing and must not raise, even when
    # a material already has a bound run from an earlier build.
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        ledger.assign(_run_key("anyt.am-1-1"), "runs")  # pre-existing binding, run absent this build
        coupled, counts = _couple_runs(tables, (), details_dir=_absent_details(tmp_path), ledger=ledger, collected=0)
        assert not coupled and not counts


def test_partial_raw_path_miss_only_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # A partial transfer (some runs present) preserves the absent ones without raising.
    tables = _tables(tmp_path / "tables", "CrSb", ("anyt.am-1-1", "anyt.am-1-2"))
    details = tmp_path / "details"
    _details(details, "am-1-0001", "1/Runs/a")
    _details(details, "am-1-0002", "1/Runs/b")
    with _ledger(tmp_path) as ledger:
        with caplog.at_level("WARNING"):
            coupled, _ = _couple_runs(
                tables, (_run("CrSb", "s:a", raw_path="1/Runs/a"),), details_dir=details, ledger=ledger
            )
        assert "anyt.am-1-1" in coupled
        assert "anyt.am-1-2" not in coupled
    assert "not collected in this build: 1/Runs/b" in caplog.text


def test_details_raw_path_absent_malformed_and_wrong_type(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    # Never raises: an absent shard is debug, a present-but-unusable shard warns.
    root = tmp_path / "details"
    assert details_raw_path(root, "anyt.am-1-1") == ""  # no shard at all

    def _shard(amdb_id: str, contents: str) -> None:
        number = amdb_id.rsplit("-", 1)[1]
        shard = root / "am-1" / number[:1] / number[:2] / number[:3] / f"am-1-{number}"
        shard.mkdir(parents=True)
        (shard / f"am-1-{number}.json").write_text(contents, encoding="utf-8")

    _shard("am-1-0002", "{ this is not json")
    _shard("am-1-0003", json.dumps({"raw_path": 5}))
    with caplog.at_level("WARNING"):
        assert details_raw_path(root, "anyt.am-1-2") == ""
        assert details_raw_path(root, "anyt.am-1-3") == ""
    assert "unreadable" in caplog.text
    assert "malformed or missing" in caplog.text


# -- binding resolution matrix (design §2/§6) --------------------------------


def test_new_coupling_assigns_intrinsic_and_aliases_binding(tmp_path: Path) -> None:
    """Both keys unset: a fresh run gets its intrinsic id, the entity key aliases onto it."""
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        coupled, _ = _couple_runs(
            tables, (_run("CrSb", "s:new"),), details_dir=_absent_details(tmp_path), ledger=ledger
        )
        assert "anyt.am-1-1" in coupled
        run_id = ledger.lookup(_run_source_key("s:new"))
        assert run_id is not None
        bindings = ledger.bindings()
        assert bindings[_run_source_key("s:new")] == (run_id, "runs", False)  # the assignment
        assert bindings[_run_key("anyt.am-1-1")] == (run_id, "runs", True)  # the alias


def test_self_migration_aliases_intrinsic_onto_the_pre_existing_id(tmp_path: Path) -> None:
    """existing set, intrinsic None: the pre-redesign material's own run id is kept untouched."""
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        pre_existing_id = ledger.assign(_run_key("anyt.am-1-1"), "runs")  # pre-redesign shape: a plain assignment
        coupled, counts = _couple_runs(
            tables, (_run("CrSb", "s:legacy"),), details_dir=_absent_details(tmp_path), ledger=ledger
        )
        assert counts == {"auto": 1}
        assert coupled["anyt.am-1-1"].item.run.source_id == "s:legacy"
        # The run id is UNCHANGED -- purely additive migration.
        assert ledger.lookup(_run_key("anyt.am-1-1")) == pre_existing_id
        bindings = ledger.bindings()
        assert bindings[_run_source_key("s:legacy")] == (pre_existing_id, "runs", True)  # the new intrinsic alias
        assert bindings[_run_key("anyt.am-1-1")] == (pre_existing_id, "runs", False)  # unchanged assignment


def test_run_already_known_binds_entity_to_its_intrinsic_id(tmp_path: Path) -> None:
    """existing None, intrinsic set: a run already registered, entity newly bound to it."""
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        run_id = ledger.assign(_run_source_key("s:known"), "runs")
        coupled, _ = _couple_runs(
            tables, (_run("CrSb", "s:known"),), details_dir=_absent_details(tmp_path), ledger=ledger
        )
        assert "anyt.am-1-1" in coupled
        assert ledger.lookup(_run_key("anyt.am-1-1")) == run_id


def test_already_bound_and_matching_is_a_no_op(tmp_path: Path) -> None:
    """both set and equal: nothing new is written, the material is still coupled."""
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        run_id = ledger.assign(_run_source_key("s:same"), "runs")
        ledger.alias(_run_key("anyt.am-1-1"), run_id)
        before = dict(ledger.bindings())
        coupled, _ = _couple_runs(
            tables, (_run("CrSb", "s:same"),), details_dir=_absent_details(tmp_path), ledger=ledger
        )
        assert "anyt.am-1-1" in coupled
        assert dict(ledger.bindings()) == before  # no new record appended


def test_conflicting_binding_raises(tmp_path: Path) -> None:
    """both set and different: a genuine conflict must never silently re-bind."""
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        ledger.assign(_run_key("anyt.am-1-1"), "runs")  # material already bound to SOME run
        ledger.assign(_run_source_key("s:other"), "runs")  # a DIFFERENT run's own intrinsic id
        with pytest.raises(ValueError, match="anyt.am-1-1.*conflicts with run"):
            _couple_runs(tables, (_run("CrSb", "s:other"),), details_dir=_absent_details(tmp_path), ledger=ledger)


def test_claimed_run_refuses_rather_than_steals(tmp_path: Path) -> None:
    """A run already bound (via _run_key) to material A must never bind to material B too."""
    tables = _tables(
        tmp_path / "tables",
        amdb_ids=("anyt.am-1-1", "anyt.am-1-2"),
        materials={"anyt.am-1-1": "Amat", "anyt.am-1-2": "Bmat"},
    )
    with _ledger(tmp_path) as ledger:
        # Material A already owns this run's intrinsic id via its binding key.
        run_id = ledger.assign(_run_source_key("s:shared"), "runs")
        ledger.alias(_run_key("anyt.am-1-1"), run_id)
        coupled, counts = _couple_runs(
            tables, (_run("Bmat", "s:shared"),), details_dir=_absent_details(tmp_path), ledger=ledger
        )
        assert "anyt.am-1-2" not in coupled
        assert not counts  # refused, not ambiguous: no candidate was even offered a count
        assert ledger.lookup(_run_key("anyt.am-1-2")) is None  # never bound


def test_absent_bound_run_warns_without_ledger_mutation(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A material's bound run missing from this build's tree: warn, no ledger write."""
    tables = _tables(tmp_path / "tables", "CrSb")
    with _ledger(tmp_path) as ledger:
        ledger.assign(_run_key("anyt.am-1-1"), "runs")
        before = dict(ledger.bindings())
        with caplog.at_level("WARNING"):
            coupled, counts = _couple_runs(tables, (), details_dir=_absent_details(tmp_path), ledger=ledger)
        assert not coupled and not counts
        assert dict(ledger.bindings()) == before
    assert "not collected in this build" in caplog.text
