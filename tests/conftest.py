import bz2
import csv
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FUNCTIONS = ROOT / "src" / "functions"
SERVER = ROOT / "server"
for _path in (FUNCTIONS, SERVER):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import material_store
from material_store import build_store

# Structural guard: no store-building test may fall through to the production
# tables/ ledger. resolve_tables_dir raises if a non-legacy build reaches it without
# an explicit tables_dir (or ALTERMAGNETS_TABLES_DIR), so every such test must pass a
# tmp fixture dir. Set here because conftest is imported only under pytest. Also drop
# any exported ALTERMAGNETS_TABLES_DIR so it cannot silently bypass the guard (that env
# var is consulted before the guard in resolve_tables_dir).
os.environ.pop(material_store.TABLES_PATH_ENVIRONMENT, None)
material_store._GUARD_DEFAULT_TABLES_DIR = True

_SYMMETRY_FIELDS = (
    "Filename",
    "ChemicalFormula",
    "Disordered",
    "Noncommensurate",
    "Noncollinear",
    "EqualAmplitude",
    "NonzeroTotalMagnetisation",
    "MagneticSpacegroupType",
    "Symprec",
    "atol",
    "patol",
    "SpinBasis",
    "Collinearize",
    "Equalize",
    "UsePymatgen",
    "Standardize",
    "Type",
    "AmcheckIsAltermagnet",
    "BNSmcif",
    "BNS",
    "EffectiveBNS",
    "ParentSpacegroup",
    "MagneticPhase",
    "MagneticPhaseShort",
    "GMagneticSystemLaueClass",
    "HHalvingSubgroupLaueClass",
    "AGenopConnectingElement",
    "WaveClass",
    "WaveClassSimple",
    "SpinAngleMismatch",
    "SpinLengthMismatch",
    "MAGNDATAId",
    "ICSDId",
    "ReferenceDOI",
    "Notes",
    "Warnings",
)


def _write_rows(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]], *, delimiter: str = ",") -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=delimiter)
        writer.writeheader()
        writer.writerows(rows)


def write_source_tables(directory: Path, *, material_count: int = 3) -> Path:
    if material_count < 3:
        raise ValueError("synthetic material_count must be at least the three fixture records")
    directory.mkdir(parents=True, exist_ok=True)
    _write_rows(
        directory / "high_throughput_screening_results_fixed.csv",
        ("AMDBId", "MAGNDATA ID", "Material", "Space group", "FdeltaPct", "MaxSS", "AvgSS", "Bandgap", "MinAbundPpm"),
        [
            {
                "AMDBId": "anyt.am-1-1",
                "MAGNDATA ID": "0.528,0.800",
                "Material": "CrSb",
                "Space group": "P6_3/mmc",
                "FdeltaPct": "34.375",
                "MaxSS": "1.8724",
                "AvgSS": "0.763170313",
                "Bandgap": "0.0",
                "MinAbundPpm": "0.2",
            },
            {
                "AMDBId": "anyt.am-1-2",
                "MAGNDATA ID": "0.800",
                "Material": "MnTe",
                "Space group": "P6_3/mmc",
                "FdeltaPct": "20",
                "MaxSS": "0.9227",
                "AvgSS": "0.449",
                "Bandgap": "0.7637",
                "MinAbundPpm": "0.001",
            },
            {
                "AMDBId": "anyt.am-1-3",
                "MAGNDATA ID": "0.900",
                "Material": "P6Fe_As",
                "Space group": "P4/nmm",
                "FdeltaPct": "nan",
                "MaxSS": "inf",
                "AvgSS": "-inf",
                "Bandgap": "?",
                "MinAbundPpm": "NaN",
            },
        ],
        delimiter=";",
    )
    if material_count > 3:
        screening_path = directory / "high_throughput_screening_results_fixed.csv"
        with screening_path.open("a", newline="", encoding="utf-8") as handle:
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
            for index in range(4, material_count + 1):
                writer.writerow(
                    {
                        "AMDBId": f"anyt.am-1-{index}",
                        "MAGNDATA ID": f"synthetic-{index:04d}",
                        "Material": f"Fe{index}O",
                        "Space group": "P4/nmm",
                        "FdeltaPct": str(index),
                        "MaxSS": f"{index / 100:.3f}",
                        "AvgSS": f"{index / 200:.3f}",
                        "Bandgap": f"{index / 300:.3f}",
                        "MinAbundPpm": f"{index / 1_000:.3f}",
                    }
                )
    common = {
        "ChemicalFormula": "CrSb",
        "Symprec": "0.001",
        "BNSmcif": r"${\mathrm{P}_{C}\mathrm{c}2_{1}}$",
        "BNS": r"${\mathrm{P}_{C}\mathrm{c}2_{1}}$",
        "EffectiveBNS": r"${\mathrm{P}_{C}\mathrm{c}2_{1}}$",
        "ParentSpacegroup": r"${\mathrm{P}6_{3}/\mathrm{mmc}}$",
        "MagneticPhaseShort": "AM",
        "GMagneticSystemLaueClass": "6/mmm",
        "HHalvingSubgroupLaueClass": "mmm",
        "AGenopConnectingElement": r"C_{2z}",
        "WaveClass": "d",
        "WaveClassSimple": "d",
        "SpinAngleMismatch": "0.5",
        "SpinLengthMismatch": "0.1",
        "ICSDId": "123",
        "ReferenceDOI": "10.1000/example-1",
        "Warnings": "source warning",
        "Notes": "source note",
    }
    _write_rows(
        directory / "altermagnets_collinear.csv",
        _SYMMETRY_FIELDS,
        [
            {**common, "MAGNDATAId": "0.528", "Symprec": "0.001"},
            {**common, "MAGNDATAId": "0.528", "Symprec": "0.010", "WaveClass": "g"},
            {**common, "MAGNDATAId": "0.800", "Symprec": "0.001", "ChemicalFormula": "MnTe"},
        ],
    )
    _write_rows(
        directory / "altermagnets_noncollinear.csv",
        _SYMMETRY_FIELDS,
        [{**common, "MAGNDATAId": "0.800", "Symprec": "0.001", "ChemicalFormula": "MnTe", "WaveClass": "s"}],
    )
    return directory


def write_detail_assets(directory: Path) -> Path:
    target = directory / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001"
    target.mkdir(parents=True, exist_ok=True)
    (target / "amdb-1-0001.json").write_text(
        json.dumps({"raw_path": "fixture/raw/material-0001"}) + "\n",
        encoding="utf-8",
    )
    (target / "band.svg").write_text(
        "<svg xmlns='http://www.w3.org/2000/svg'><text>band</text></svg>",
        encoding="utf-8",
    )
    contcar = """Fixture POSCAR
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
    matching_magn = """ magnetization (x)

# of ion       s       p       d       tot
------------------------------------------
    1        0.100   0.200   0.300   0.600
    2       -0.100   0.000   0.100   0.000
    3        0.000   0.000   1.250   1.250
------------------------------------------
tot         0.000   0.200   1.650   1.850
"""
    mismatched_magn = matching_magn.replace(
        "    3        0.000   0.000   1.250   1.250\n", ""
    )
    for number in range(1, 4):
        material_dir = directory / "amdb-1" / "0" / "00" / "000" / f"amdb-1-000{number}"
        material_dir.mkdir(parents=True, exist_ok=True)
        with bz2.open(material_dir / "CONTCAR.bz2", "wt", encoding="utf-8") as handle:
            handle.write(contcar)
        if number == 1:
            with bz2.open(material_dir / "MAGN.bz2", "wt", encoding="utf-8") as handle:
                handle.write(matching_magn)
        elif number == 2:
            with bz2.open(material_dir / "MAGN.bz2", "wt", encoding="utf-8") as handle:
                handle.write(mismatched_magn)
    return directory


@pytest.fixture
def material_store_path(tmp_path: Path) -> Path:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    target = tmp_path / "altermagnets.duckdb"
    return build_store(target, data_dir=source, details_dir=details, legacy=True)
