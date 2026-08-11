"""Collect one finished altermagnets httk-v1 VASP result."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from httk.atomistic import UnitcellStructureView
from httk.core import DataRecord, FileRecord, load
from httk.core.digests import sha256_file
from httk.io.vasp import VASPOutputs
from httk.workflow.collecting import JobRecord
from httk.workflow.compat.v1 import run_directory, task_file
from httk.workflow.compat.v1.reader import parse_v1_task_name

logger = logging.getLogger("httk.altermagnets.relax_and_scf_httk_v1")

_TOTAL_ENERGY_DEFINITION = "https://schemas.httk.org/defs/v0.1/properties/core/total_energy"


def run_material(record: JobRecord) -> str:
    """Return the CSV-facing material name for a collected v1 task."""
    parsed = parse_v1_task_name(record.payload_path.name)
    task_id = record.payload_path.name if parsed is None else parsed["task_id"]
    return task_id.removesuffix("_SCF")


def _finished_step(outer: Path) -> Path:
    steps = sorted(path for path in outer.glob("ht.task.*.finished") if path.is_dir())
    if len(steps) != 1:
        names = ", ".join(path.name for path in steps) or "none"
        logger.warning("%s: expected exactly one finished step, found %d: %s", outer, len(steps), names)
        raise FileNotFoundError(f"expected exactly one finished step under {outer}, found: {names}")
    return steps[0]


def _inner_run(step: Path) -> Path:
    runs: list[tuple[datetime, Path]] = []
    for path in step.glob("ht.run.*"):
        if not path.is_dir():
            continue
        try:
            stamp = datetime.strptime(path.name.removeprefix("ht.run."), "%Y-%m-%d_%H.%M.%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        runs.append((stamp, path))
    if not runs:
        raise FileNotFoundError(f"no dated inner run under {step}")
    return max(runs, key=lambda item: (item[0], item[1].name))[1]


def _file_record(root: Path, path: Path, *, name: str | None = None) -> FileRecord:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    return FileRecord(
        url=relative,
        name=path.name if name is None else name,
        size=path.stat().st_size,
        sha256=sha256_file(path),
    )


def _optional_file(root: Path, directory: Path, names: tuple[str, ...], role: str) -> FileRecord | None:
    for name in names:
        path = directory / name
        if path.is_file():
            return _file_record(root, path, name=name.removesuffix(".bz2"))
    logger.warning("%s: missing %s in %s", directory, role, directory)
    return None


def _splitting_figure(root: Path, directory: Path) -> FileRecord | None:
    paths = sorted(directory.glob("*-3D-splitting.png"))
    if paths:
        return _file_record(root, paths[0])
    logger.warning("%s: missing splitting_figure in %s", directory, directory)
    return None


def collect(record: JobRecord) -> Mapping[str, object]:
    """Extract structures, energy, and selected file metadata from one task."""
    root = record.workspace_root.resolve()
    outer = run_directory(record)
    step = _finished_step(outer)
    inner = _inner_run(step)

    try:
        input_structure = load(str(task_file(step, "POSCAR")))
        # Keep this read as a validation of the input role; the v1 framework's
        # synthesized Run has no input edge to attach it to.
        UnitcellStructureView(input_structure)
    except Exception as error:
        logger.warning("%s: cannot read input_structure: %s", outer, error)

    try:
        relaxed = UnitcellStructureView(load(str(task_file(inner, "CONTCAR"))))
    except Exception as error:
        logger.warning("%s: relaxed_structure unavailable: %s", outer, error)
        raise

    outputs: dict[str, object] = {"relaxed_structure": relaxed}
    try:
        vasp_outputs = VASPOutputs(inner)
        if vasp_outputs.outcar is None:
            raise FileNotFoundError(f"no OUTCAR under {inner}")
        final_energies: Any = vasp_outputs.outcar.final_energies
        # total_energy.json describes the computed total energy in eV. VASP's
        # energy_sigma0 is the T->0 extrapolated member, not free_energy.
        outputs["total_energy"] = DataRecord.from_value(
            _TOTAL_ENERGY_DEFINITION,
            "_httk_total_energy",
            float(final_energies.energy_sigma0),
        )
    except Exception as error:
        logger.warning("%s: total_energy unavailable: %s", outer, error)

    for role, names in {
        "vasprun": ("vasprun.xml", "vasprun.xml.bz2"),
        "doscar": ("DOSCAR", "DOSCAR.bz2"),
    }.items():
        value = _optional_file(root, inner, names, role)
        if value is not None:
            outputs[role] = value
    figure = _splitting_figure(root, inner)
    if figure is not None:
        outputs["splitting_figure"] = figure
    return outputs
