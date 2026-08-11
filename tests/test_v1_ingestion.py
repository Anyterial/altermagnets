import logging
from pathlib import Path

import pytest
from httk.workflow.compat.v1 import collect_finished_tree, finished_tasks

ROOT = Path(__file__).resolve().parents[1] / "data" / "raw_httk_v1" / "1" / "Runs"
PACKAGE = Path(__file__).resolve().parents[1] / "workflows" / "relax_and_scf_httk_v1"

_REAL_TREE = pytest.mark.skipif(not ROOT.is_dir(), reason="real v1 tree is transfer-managed")


_POSCAR = """synthetic
1.0
1 0 0
0 1 0
0 0 1
Si
1
Direct
0 0 0
"""
_OUTCAR = """ vasp.5.2.12 synthetic
   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
   free  energy   TOTEN  =       -1.00000000 eV
   energy  without entropy=      -1.00000000  energy(sigma->0) =      -1.00000000
 General timing and accounting informations
"""


@_REAL_TREE
def test_real_parenthesized_task_collects() -> None:
    task_name = "ht.task.tetralith--default.(CH3NH3)(Co(COOH)3_SCF.cleanup.0.unclaimed.3.finished"
    root = ROOT / task_name
    item = next(iter(collect_finished_tree(root.parent, workflow_dir=PACKAGE)))
    assert item.missing_collector is None
    assert {"relaxed_structure", "total_energy", "vasprun", "doscar", "splitting_figure"} <= set(item.outputs)
    assert item.outputs["relaxed_structure"].composition.chemical_formula_reduced == "C4CoH9NO6"
    assert item.outputs["total_energy"].value == -503.12232019


@_REAL_TREE
def test_real_tree_has_at_least_thirty_finished_tasks() -> None:
    assert sum(1 for _ in finished_tasks(ROOT)) >= 30


def test_synthetic_tree_collects_one_dated_inner_run(tmp_path: Path) -> None:
    task = tmp_path / "ht.task.tetralith--default.Si_SCF.cleanup.0.unclaimed.3.finished"
    outer = task / "ht.run.2025-01-01_00.00.00"
    step = outer / "ht.task.any.0.cleanup.0.unclaimed.3.finished"
    inner = step / "ht.run.2025-01-01_00.00.01"
    inner.mkdir(parents=True)
    (step / "POSCAR").write_text(_POSCAR, encoding="utf-8")
    (inner / "CONTCAR").write_text(_POSCAR, encoding="utf-8")
    (inner / "OUTCAR").write_text(_OUTCAR, encoding="utf-8")

    item = next(iter(collect_finished_tree(tmp_path, workflow_dir=PACKAGE)))
    assert item.missing_collector is None
    assert item.outputs["relaxed_structure"].composition.chemical_formula_reduced == "Si"
    assert item.outputs["total_energy"].value == -1.0


def test_multiple_finished_steps_degrade_with_named_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    task = tmp_path / "ht.task.tetralith--default.Si_SCF.cleanup.0.unclaimed.3.finished"
    outer = task / "ht.run.2025-01-01_00.00.00"
    for name in ("ht.task.one.0.cleanup.0.unclaimed.3.finished", "ht.task.two.0.cleanup.0.unclaimed.3.finished"):
        (outer / name).mkdir(parents=True)
    with caplog.at_level(logging.WARNING):
        item = next(iter(collect_finished_tree(tmp_path, workflow_dir=PACKAGE)))
    assert item.missing_collector is not None
    assert "ht.task.one.0.cleanup.0.unclaimed.3.finished" in caplog.text
    assert "ht.task.two.0.cleanup.0.unclaimed.3.finished" in caplog.text
