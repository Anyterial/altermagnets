"""Tests for the AMDB curation tool (``tools/curate.py``).

Covers the two subcommands against the id ledger: ``status`` (a read-only
coupling report) and ``couple`` (the ephemeral ``--list`` review queue, and
the three write paths: ``--assign``, ``--attach``, and ``--assign
--supersede``). ``--list`` is asserted to write nothing: the ledger file's
bytes and its ``bindings()`` snapshot are compared before and after the call.
"""

import hashlib
from pathlib import Path

import pytest
from conftest import write_source_tables
from httk.core.project.sealing import resolve_seal_keys
from httk.store import IdLedger
from material_store import (
    LEDGER_BASES,
    LEDGER_SERIES,
    LEDGER_SIGNER_REFS,
    SCREENING_RESULTS_FILENAME,
    _load_csv_rows,
    _result_ids,
    _run_key,
    _run_source_key,
)
from tools import curate

_POSCAR = """Fixture
1.0
1 0 0
0 1 0
0 0 1
H He Li
1 1 1
Direct
0 0 0
0.5 0.5 0.5
0.25 0.25 0.25
"""
_OUTCAR = """ vasp.5.2.12 synthetic
   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
   free  energy   TOTEN  =       -1.00000000 eV
   energy  without entropy=      -1.00000000  energy(sigma->0) =      -1.00000000
 General timing and accounting informations
"""


def _seeded_ledger(path: Path, tables: Path) -> None:
    """Create a fresh ledger and seed its ``results`` family from *tables*'s screening CSV."""
    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=path.parent).keys
    with IdLedger.create(path, bases=LEDGER_BASES, series=LEDGER_SERIES, keys=keys) as ledger:
        _result_ids(ledger, _load_csv_rows(tables / SCREENING_RESULTS_FILENAME, delimiter=";"))


def _v1_task(root: Path, project: str, task_name: str) -> None:
    """Fabricate a minimal finished v1 task directory collect_finished_tree can read."""
    task = root / project / "Runs" / task_name
    outer = task / "ht.run.2025-01-01_00.00.00"
    step = outer / "ht.task.any.0.cleanup.0.unclaimed.3.finished"
    inner = step / "ht.run.2025-01-01_00.00.01"
    inner.mkdir(parents=True)
    (step / "POSCAR").write_text(_POSCAR, encoding="utf-8")
    (inner / "CONTCAR").write_text(_POSCAR, encoding="utf-8")
    (inner / "OUTCAR").write_text(_OUTCAR, encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- status --------------------------------------------------------------------


def test_status_json_reports_coupled_unbound_unclaimed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tables = write_source_tables(tmp_path / "tables")  # anyt.am-1-1, -2, -3
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    # Manually add: material 1 coupled (both keys, D3 shape), one unclaimed run,
    # materials 2 and 3 left unbound.
    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        run_id = ledger.assign(_run_source_key("s:bound"), "runs")
        ledger.alias(_run_key("anyt.am-1-1"), run_id)
        ledger.assign(_run_source_key("s:orphan"), "runs")  # never claimed by any material

    assert curate.main(["status", "--ledger-path", str(ledger_path), "--json"]) == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["entities"]["total"] == 3
    assert payload["entities"]["coupled"] == 1
    assert payload["entities"]["unbound"] == 2
    assert set(payload["entities"]["unbound_ids"]) == {"anyt.am-1-2", "anyt.am-1-3"}
    assert payload["runs"]["total"] == 2
    assert payload["runs"]["unclaimed"] == 1
    assert payload["runs"]["unclaimed_runs"][0]["source_key"] == "run:s:orphan"


def test_status_plain_text_lists_unbound_and_unclaimed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    assert curate.main(["status", "--ledger-path", str(ledger_path)]) == 0
    out = capsys.readouterr().out
    assert "3 total, 0 coupled, 3 unbound" in out
    assert "anyt.am-1-1" in out and "anyt.am-1-2" in out and "anyt.am-1-3" in out


# -- couple --list ---------------------------------------------------------------


def test_couple_list_reports_ambiguous_material_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tables = write_source_tables(tmp_path / "tables")  # material "CrSb" -> anyt.am-1-1
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)
    details_dir = tmp_path / "details"  # deliberately absent: no authoritative raw_path for anyone

    runs_root = tmp_path / "runs"
    _v1_task(runs_root, "1", "ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.3.finished")
    _v1_task(runs_root, "2", "ht.task.tetralith--default.CrSb_SCF.cleanup.0.unclaimed.3.finished")

    before_hash = _sha256(ledger_path)
    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        before_bindings = dict(ledger.bindings())

    exit_code = curate.main(
        [
            "couple",
            "--list",
            "--ledger-path",
            str(ledger_path),
            "--data-dir",
            str(tables),
            "--details-dir",
            str(details_dir),
            "--runs-dir",
            str(runs_root),
        ]
    )
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "ambiguous: anyt.am-1-1 (CrSb)" in out
    assert out.count("candidate  source_id=") == 2  # both candidate runs are listed

    # Provably no ledger mutation: identical bytes and identical bindings snapshot.
    assert _sha256(ledger_path) == before_hash
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        assert dict(ledger.bindings()) == before_bindings


# -- couple --assign ---------------------------------------------------------------


def test_assign_happy_path_binds_an_unmatched_material(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    exit_code = curate.main(["couple", "--assign", "anyt.am-1-2", "s:manual", "--ledger-path", str(ledger_path)])
    assert exit_code == 0

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        run_id = ledger.lookup(_run_source_key("s:manual"))
        assert run_id is not None
        assert ledger.lookup(_run_key("anyt.am-1-2")) == run_id


def test_assign_unknown_amdb_id_refuses(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)
    before_hash = _sha256(ledger_path)

    exit_code = curate.main(["couple", "--assign", "anyt.am-1-999", "s:manual", "--ledger-path", str(ledger_path)])
    assert exit_code != 0
    assert _sha256(ledger_path) == before_hash


def test_assign_conflict_without_supersede_refuses_and_writes_nothing(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        old_run = ledger.assign(_run_source_key("s:old"), "runs")
        ledger.alias(_run_key("anyt.am-1-3"), old_run)
        ledger.assign(_run_source_key("s:new"), "runs")  # registered, unclaimed by anyone

    before_hash = _sha256(ledger_path)
    exit_code = curate.main(["couple", "--assign", "anyt.am-1-3", "s:new", "--ledger-path", str(ledger_path)])
    assert exit_code != 0
    assert _sha256(ledger_path) == before_hash

    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        assert ledger.lookup(_run_key("anyt.am-1-3")) == old_run


def test_assign_with_supersede_rebinds_and_old_run_stays_resolvable(tmp_path: Path) -> None:
    # S ("s:new") is already registered before the supersede call.
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        old_run = ledger.assign(_run_source_key("s:old"), "runs")
        ledger.alias(_run_key("anyt.am-1-3"), old_run)
        new_run = ledger.assign(_run_source_key("s:new"), "runs")

    exit_code = curate.main(
        ["couple", "--assign", "anyt.am-1-3", "s:new", "--supersede", "--ledger-path", str(ledger_path)]
    )
    assert exit_code == 0

    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        assert ledger.lookup(_run_key("anyt.am-1-3")) == new_run
        # The old run's own intrinsic key is untouched by the entity's re-coupling.
        assert ledger.lookup(_run_source_key("s:old")) == old_run


def test_assign_supersede_with_unregistered_source_mints_a_fresh_run_id(tmp_path: Path) -> None:
    # Regression test for the MAJOR defect: S ("s:new") is NOT registered yet --
    # the normal re-coupling case, since branch E (the hard conflict) can only ever
    # fire once S is already known. The old (buggy) code took the self-migration
    # branch here, silently aliased "run:s:new" onto the OLD id, and never re-bound
    # anything -- a permanent false claim, and every repeat call became a no-op.
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        old_run = ledger.assign(_run_source_key("s:old"), "runs")
        ledger.alias(_run_key("anyt.am-1-3"), old_run)
        # "s:new" is deliberately left unregistered.

    exit_code = curate.main(
        ["couple", "--assign", "anyt.am-1-3", "s:new", "--supersede", "--ledger-path", str(ledger_path)]
    )
    assert exit_code == 0

    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        new_run = ledger.lookup(_run_source_key("s:new"))
        assert new_run is not None
        assert new_run != old_run  # a genuinely fresh id, never the material's old one
        assert ledger.lookup(_run_key("anyt.am-1-3")) == new_run
        # The old run's own intrinsic key is untouched by the entity's re-coupling.
        assert ledger.lookup(_run_source_key("s:old")) == old_run


def test_assign_supersede_on_legacy_assignment_shaped_binding_rebinds(tmp_path: Path) -> None:
    # Production-shaped binding: the binding key IS the assignment (pre-redesign
    # materials that never got a separate "run:<source>" intrinsic key at all).
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        old_run = ledger.assign(_run_key("anyt.am-1-3"), "runs")
        assert not ledger.bindings()[_run_key("anyt.am-1-3")].is_alias

    exit_code = curate.main(
        ["couple", "--assign", "anyt.am-1-3", "s:new", "--supersede", "--ledger-path", str(ledger_path)]
    )
    assert exit_code == 0

    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        new_run = ledger.lookup(_run_source_key("s:new"))
        assert new_run is not None
        assert new_run != old_run
        assert ledger.lookup(_run_key("anyt.am-1-3")) == new_run


# -- couple --attach ---------------------------------------------------------------


def test_attach_happy_path_registers_intrinsic_id_for_an_already_bound_material(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        # Legacy shape: bound directly, no intrinsic run: key registered yet.
        old_run = ledger.assign(_run_key("anyt.am-1-1"), "runs")

    exit_code = curate.main(["couple", "--attach", "anyt.am-1-1", "s:legacy", "--ledger-path", str(ledger_path)])
    assert exit_code == 0

    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        assert ledger.lookup(_run_source_key("s:legacy")) == old_run
        assert ledger.lookup(_run_key("anyt.am-1-1")) == old_run  # unchanged


def test_attach_unbound_material_refuses(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)
    before_hash = _sha256(ledger_path)

    exit_code = curate.main(["couple", "--attach", "anyt.am-1-1", "s:legacy", "--ledger-path", str(ledger_path)])
    assert exit_code != 0
    assert _sha256(ledger_path) == before_hash


def test_attach_source_registered_to_a_different_run_refuses(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        bound_run = ledger.assign(_run_key("anyt.am-1-1"), "runs")
        ledger.assign(_run_source_key("s:other"), "runs")  # a DIFFERENT, already-registered run

    before_hash = _sha256(ledger_path)
    exit_code = curate.main(["couple", "--attach", "anyt.am-1-1", "s:other", "--ledger-path", str(ledger_path)])
    assert exit_code != 0
    assert _sha256(ledger_path) == before_hash

    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES, read_only=True) as ledger:
        assert ledger.lookup(_run_key("anyt.am-1-1")) == bound_run  # unchanged


def test_attach_already_attached_is_a_noop(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    keys = resolve_seal_keys(LEDGER_SIGNER_REFS, project_root=tmp_path).keys
    with IdLedger.open(ledger_path, keys=keys, bases=LEDGER_BASES, series=LEDGER_SERIES) as ledger:
        run_id = ledger.assign(_run_source_key("s:known"), "runs")
        ledger.alias(_run_key("anyt.am-1-1"), run_id)

    exit_code = curate.main(["couple", "--attach", "anyt.am-1-1", "s:known", "--ledger-path", str(ledger_path)])
    assert exit_code == 0


# -- argparse mode exclusivity -------------------------------------------------------


def test_list_and_assign_together_is_an_argparse_error(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables")
    ledger_path = tmp_path / "ids.sqlite"
    _seeded_ledger(ledger_path, tables)

    with pytest.raises(SystemExit) as excinfo:
        curate.main(["couple", "--list", "--assign", "anyt.am-1-1", "s:x", "--ledger-path", str(ledger_path)])
    assert excinfo.value.code == 2
