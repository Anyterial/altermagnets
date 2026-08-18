"""Collect one finished altermagnets httk-v1 VASP result."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from httk.atomistic import CartesianSiteMoments, UnitcellStructure, UnitcellStructureView
from httk.atomistic.integrations.vasp.io import VASPOutputs
from httk.core import DataRecord, FileRecord, load
from httk.core.digests import sha256_file
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


def _with_site_moments(structure: UnitcellStructure, moments: tuple[float, ...]) -> UnitcellStructure:
    """Rebuild a structure with collinear VASP moments as Cartesian ``(0, 0, m)`` vectors.

    This mirrors ``material_store.load_material_structure`` so a run-ingested
    structure and its legacy details counterpart share one content id.
    """
    return UnitcellStructure(
        structure.cell,
        structure.sites,
        structure.species,
        structure.species_at_sites,
        site_moments=CartesianSiteMoments([[0.0, 0.0, moment] for moment in moments]),
        molecular=structure.molecular,
        assemblies=structure.assemblies,
        symmetry=structure.symmetry,
        chemical_composition=structure.chemical_composition,
        chemical_formula_descriptive=structure.chemical_formula_descriptive,
        chemical_formula_hill=structure.chemical_formula_hill,
        optimization_type=structure.optimization_type,
        immutable_id=structure.immutable_id,
        last_modified=structure.last_modified,
    )


def collect(record: JobRecord) -> Mapping[str, object]:
    """Extract structures, energy, and selected file metadata from one task."""
    # This package collects the dataset's top-level SCF runs, whose path relative to
    # the raw_httk_v1 collection root is exactly <project>/Runs/<task-dir>. Band,
    # template, scratch (e.g. test-percentage), failed-job and the inner per-step
    # subtrees are all deeper and are other work; reject them before any file I/O so
    # the sweep does not parse (or name-match a failed job into) runs it will
    # discard. collect_finished_tree catches this and degrades the task silently.
    parts = record.payload_path.parts
    if len(parts) != 3 or parts[1] != "Runs":
        raise ValueError(f"not a dataset SCF run: {record.payload_path}")
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
        relaxed_structure = load(str(task_file(inner, "CONTCAR")))
    except Exception as error:
        logger.warning("%s: relaxed_structure unavailable: %s", outer, error)
        raise

    outputs: dict[str, object] = {}
    # One OUTCAR handle feeds both the energy and the moments; a failure in either
    # must not silently skip the other, so the read is separated from each use.
    try:
        outcar: Any = VASPOutputs(inner).outcar
        if outcar is None:
            raise FileNotFoundError(f"no OUTCAR under {inner}")
    except Exception as error:
        logger.warning("%s: OUTCAR unavailable: %s", outer, error)
        outcar = None

    if outcar is not None:
        try:
            final_energies = outcar.final_energies
            # total_energy.json describes the computed total energy in eV. VASP's
            # energy_sigma0 is the T->0 extrapolated member, not free_energy.
            outputs["total_energy"] = DataRecord.from_value(
                _TOTAL_ENERGY_DEFINITION,
                "_httk_total_energy",
                float(final_energies.energy_sigma0),
            )
        except Exception as error:
            logger.warning("%s: total_energy unavailable: %s", outer, error)

        # Collinear VASP moments (per-ion total column) become site moments so the
        # run-ingested structure matches the legacy details build's content id.
        try:
            noncollinear = outcar.noncollinear_magnetization
            moments = outcar.magnetization
        except Exception as error:
            # magnetization triggers _ensure_full(), which caches only on success:
            # if the earlier energy access already failed inside _ensure_full (a
            # corrupt/undecodable OUTCAR), this re-reads the file and re-raises.
            logger.warning("%s: magnetization unavailable: %s", outer, error)
            noncollinear = False
            moments = None
        if moments is None:
            logger.debug("%s: no magnetization; emitting relaxed_structure without moments", outer)
        elif noncollinear:
            # site_moments as (0, 0, m) would misrepresent the x-projection as a
            # z-component; a noncollinear run needs the full vectors we do not have.
            logger.warning("%s: noncollinear magnetization; emitting relaxed_structure without moments", outer)
        elif len(moments) != len(relaxed_structure.sites):
            logger.warning(
                "%s: %d moments for %d sites; emitting relaxed_structure without moments",
                outer,
                len(moments),
                len(relaxed_structure.sites),
            )
        else:
            relaxed_structure = _with_site_moments(relaxed_structure, moments)

    outputs["relaxed_structure"] = UnitcellStructureView(relaxed_structure)

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
