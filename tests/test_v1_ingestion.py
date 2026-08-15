import bz2
import importlib.util
import logging
from pathlib import Path

import pytest
from conftest import write_detail_assets
from httk.atomistic import UnitcellStructureView
from httk.core import load
from httk.workflow.compat.v1 import collect_finished_tree, finished_tasks
from material_store import load_material_structure, parse_magnetization_moments

RAW_ROOT = Path(__file__).resolve().parents[1] / "data" / "raw_httk_v1"
ROOT = RAW_ROOT / "1" / "Runs"
PACKAGE = Path(__file__).resolve().parents[1] / "workflows" / "relax_and_scf_httk_v1"

_COLLECT_SPEC = importlib.util.spec_from_file_location("amdb_v1_collect", PACKAGE / "collect.py")
assert _COLLECT_SPEC is not None and _COLLECT_SPEC.loader is not None
_COLLECT = importlib.util.module_from_spec(_COLLECT_SPEC)
_COLLECT_SPEC.loader.exec_module(_COLLECT)

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
    # Root at the raw_httk_v1 top so the payload is the <project>/Runs/<task> shape
    # collect() requires; the parenthesized task sorts first, so this is cheap.
    item = next(
        entry
        for entry in collect_finished_tree(RAW_ROOT, workflow_dir=PACKAGE)
        if entry.record.payload_path.name == task_name
    )
    assert item.missing_collector is None
    assert {"relaxed_structure", "total_energy", "vasprun", "doscar", "splitting_figure"} <= set(item.outputs)
    assert item.outputs["relaxed_structure"].composition.chemical_formula_reduced == "C4CoH9NO6"
    assert item.outputs["total_energy"].value == -503.12232019


@_REAL_TREE
def test_real_tree_has_at_least_thirty_finished_tasks() -> None:
    assert sum(1 for _ in finished_tasks(ROOT)) >= 30


def test_synthetic_tree_collects_one_dated_inner_run(tmp_path: Path) -> None:
    task = tmp_path / "1" / "Runs" / "ht.task.tetralith--default.Si_SCF.cleanup.0.unclaimed.3.finished"
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


_POSCAR_3 = """Fixture
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
_OUTCAR_HEAD = """ vasp.5.2.12 synthetic
   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
   free  energy   TOTEN  =       -1.00000000 eV
   energy  without entropy=      -1.00000000  energy(sigma->0) =      -1.00000000
"""
_MAGN_3 = """ magnetization (x)

# of ion       s       p       d       tot
------------------------------------------
    1        0.100   0.200   0.300   0.600
    2       -0.100   0.000   0.100   0.000
    3        0.000   0.000   1.250   1.250
------------------------------------------
tot         0.000   0.200   1.650   1.850
 General timing and accounting informations
"""
_OUTCAR_MAGN_3 = _OUTCAR_HEAD + _MAGN_3
# Two ion rows for a three-site structure: a length mismatch.
_OUTCAR_MAGN_2 = _OUTCAR_HEAD + _MAGN_3.replace("    3        0.000   0.000   1.250   1.250\n", "")
# A noncollinear run prints (y) and (z) blocks after the final (x) block.
_XBLOCK = _MAGN_3.replace(" General timing and accounting informations\n", "")
_OUTCAR_NONCOLLINEAR = (
    _OUTCAR_HEAD
    + _XBLOCK
    + _XBLOCK.replace("(x)", "(y)")
    + _XBLOCK.replace("(x)", "(z)")
    + " General timing and accounting informations\n"
)


def test_with_site_moments_attaches_z_vectors(tmp_path: Path) -> None:
    poscar = tmp_path / "POSCAR"
    poscar.write_text(_POSCAR_3, encoding="utf-8")
    structure = load(str(poscar))
    assert structure.site_moments is None
    with_moments = _COLLECT._with_site_moments(structure, (0.6, 0.0, 1.25))
    assert with_moments.site_moments is not None
    assert with_moments.site_moments.cartesian_moments.to_floats() == [
        [0.0, 0.0, 0.6],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 1.25],
    ]


def _magn_tree(tmp_path: Path, outcar_text: str) -> Path:
    task = tmp_path / "1" / "Runs" / "ht.task.tetralith--default.HHeLi_SCF.cleanup.0.unclaimed.3.finished"
    step = task / "ht.run.2025-01-01_00.00.00" / "ht.task.any.0.cleanup.0.unclaimed.3.finished"
    inner = step / "ht.run.2025-01-01_00.00.01"
    inner.mkdir(parents=True)
    (step / "POSCAR").write_text(_POSCAR_3, encoding="utf-8")
    (inner / "CONTCAR").write_text(_POSCAR_3, encoding="utf-8")
    (inner / "OUTCAR").write_text(outcar_text, encoding="utf-8")
    return tmp_path


def test_collect_attaches_matching_moments(tmp_path: Path) -> None:
    tree = _magn_tree(tmp_path, _OUTCAR_MAGN_3)
    item = next(iter(collect_finished_tree(tree, workflow_dir=PACKAGE)))
    moments = item.outputs["relaxed_structure"].site_moments
    assert moments is not None
    assert moments.cartesian_moments.to_floats() == [[0.0, 0.0, 0.6], [0.0, 0.0, 0.0], [0.0, 0.0, 1.25]]


def test_collect_length_mismatch_omits_moments(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    tree = _magn_tree(tmp_path, _OUTCAR_MAGN_2)
    with caplog.at_level(logging.WARNING):
        item = next(iter(collect_finished_tree(tree, workflow_dir=PACKAGE)))
    relaxed = item.outputs["relaxed_structure"]
    assert len(relaxed.sites) == 3
    assert relaxed.site_moments is None
    assert "2 moments for 3 sites" in caplog.text


def test_collect_declines_noncollinear_moments(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    tree = _magn_tree(tmp_path, _OUTCAR_NONCOLLINEAR)
    with caplog.at_level(logging.WARNING):
        item = next(iter(collect_finished_tree(tree, workflow_dir=PACKAGE)))
    relaxed = item.outputs["relaxed_structure"]
    assert len(relaxed.sites) == 3
    assert relaxed.site_moments is None
    assert "noncollinear" in caplog.text


def test_run_and_details_moments_share_content_id(tmp_path: Path) -> None:
    # The whole design rests on the run-ingested and details-built structures having
    # one content id for the same CONTCAR bytes and moment list.
    details = write_detail_assets(tmp_path / "details")
    from_details = load_material_structure(details, "anyt:am-1-0001")
    assert from_details is not None and from_details.site_moments is not None
    shard = details / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    with bz2.open(shard / "MAGN.bz2", "rt", encoding="utf-8") as handle:
        moments = tuple(parse_magnetization_moments(handle.read()))
    from_run = _COLLECT._with_site_moments(load(str(shard / "CONTCAR.bz2")), moments)
    assert UnitcellStructureView(from_details).id == UnitcellStructureView(from_run).id


def test_collect_rejects_non_scf_task(tmp_path: Path) -> None:
    # A band/failed/scratch task (not <project>/Runs/<task>) must degrade silently.
    task = (
        tmp_path / "1" / "band_step" / "Runs" / "ht.task.tetralith--default.Si_SCF_BAND.cleanup.0.unclaimed.3.finished"
    )
    step = task / "ht.run.2025-01-01_00.00.00" / "ht.task.any.0.cleanup.0.unclaimed.3.finished"
    inner = step / "ht.run.2025-01-01_00.00.01"
    inner.mkdir(parents=True)
    (step / "POSCAR").write_text(_POSCAR_3, encoding="utf-8")
    (inner / "CONTCAR").write_text(_POSCAR_3, encoding="utf-8")
    (inner / "OUTCAR").write_text(_OUTCAR_MAGN_3, encoding="utf-8")
    items = [item for item in collect_finished_tree(tmp_path, workflow_dir=PACKAGE) if item.missing_collector is None]
    assert items == []


def test_multiple_finished_steps_degrade_with_named_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    task = tmp_path / "1" / "Runs" / "ht.task.tetralith--default.Si_SCF.cleanup.0.unclaimed.3.finished"
    outer = task / "ht.run.2025-01-01_00.00.00"
    for name in ("ht.task.one.0.cleanup.0.unclaimed.3.finished", "ht.task.two.0.cleanup.0.unclaimed.3.finished"):
        (outer / name).mkdir(parents=True)
    with caplog.at_level(logging.WARNING):
        item = next(iter(collect_finished_tree(tmp_path, workflow_dir=PACKAGE)))
    assert item.missing_collector is not None
    assert "ht.task.one.0.cleanup.0.unclaimed.3.finished" in caplog.text
    assert "ht.task.two.0.cleanup.0.unclaimed.3.finished" in caplog.text
