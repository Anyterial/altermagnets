from dataclasses import replace
from pathlib import Path

import material_store
import pytest
from conftest import write_detail_assets, write_source_tables
from httk.atomistic import CartesianSiteMoments, Cell, Sites, Species, UnitcellStructure
from httk.core import DataRecord, FileRecord
from httk.core.provenance import ProductLink, Run
from material_store import (
    AltermagnetScreeningResult,
    _magnetic_alternative_cell,
    build_store,
    load_material_structure,
    material_structure,
    open_prebuilt_store,
    parse_magnetization_moments,
)


def test_parse_magnetization_moments() -> None:
    text = """ magnetization (x)
# of ion       s       p       d       tot
------------------------------------------
1 0.1 0.2 0.3 0.6
2 0.0 0.0 0.0 0.0
------------------------------------------
tot 0.1 0.2 0.3 0.6
"""
    assert parse_magnetization_moments(text) == [0.6, 0.0]


def test_parse_magnetization_moments_accepts_f_column() -> None:
    text = """ magnetization (x)
# of ion       s       p       d       f       tot
------------------------------------------
1 0.1 0.2 0.3 0.4 1.0
------------------------------------------
tot 0.1 0.2 0.3 0.4 1.0
"""
    assert parse_magnetization_moments(text) == [1.0]


@pytest.mark.parametrize("text", ("", "magnetization (x)\n# of ion s p d tot\n1 bad"))
def test_parse_magnetization_moments_rejects_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        parse_magnetization_moments(text)


def test_load_material_structure_reads_vasp_z_axis_moments(tmp_path: Path) -> None:
    details = write_detail_assets(tmp_path / "details")
    structure = load_material_structure(details, "anyt.am-1-1")
    assert structure is not None
    assert structure.site_moments is not None
    assert structure.site_moments == CartesianSiteMoments([[0.0, 0.0, 0.6], [0.0, 0.0, 0.0], [0.0, 0.0, 1.25]])


def test_load_material_structure_warns_and_omits_invalid_moments(tmp_path: Path, caplog) -> None:
    details = write_detail_assets(tmp_path / "details")
    with caplog.at_level("WARNING"):
        structure = load_material_structure(details, "anyt.am-1-2")
    assert structure is not None
    assert structure.site_moments is None
    assert "2 rows for 3 sites" in caplog.text


def test_material_structure_round_trips_through_store(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    expected = load_material_structure(details, "anyt.am-1-1")
    assert expected is not None
    store_path = build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, legacy=True)
    opened = open_prebuilt_store(store_path)
    assert opened is not None
    try:
        searcher = opened.store.searcher()
        material = searcher.variable(AltermagnetScreeningResult)
        record = searcher.results(material=material).first()
        assert record is not None
        actual = material_structure(record["material"])
        assert actual == expected
        assert actual is not None
        assert actual.site_moments == expected.site_moments
    finally:
        opened.database.dispose()


def test_stale_layout_store_is_rejected_and_falls_back(tmp_path: Path, monkeypatch, caplog) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    store_path = build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, legacy=True)

    # A store from an older schema generation carries an older (or no) layout
    # stamp; it must be treated as stale rather than silently adopted.
    monkeypatch.setattr(material_store, "STORE_LAYOUT_VERSION", material_store.STORE_LAYOUT_VERSION + 1)
    with caplog.at_level("INFO", logger="httk.altermagnets.material_store"):
        assert open_prebuilt_store(store_path) is None
    assert "stale" in caplog.text

    fallback = material_store.open_material_store(store_path, data_dir=source, details_dir=details)
    assert fallback is not None
    try:
        assert fallback.mode == "memory"
    finally:
        fallback.database.dispose()


def test_build_stores_conventional_and_primitive_alternatives(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    store_path = build_store(
        tmp_path / "store.duckdb", data_dir=source, details_dir=details, runs_dir=tmp_path / "runs"
    )
    opened = open_prebuilt_store(store_path)
    assert opened is not None
    try:
        from httk.atomistic.storage.records import UnitcellStructureRecord

        def immutable_ids(*, only_main_alt: bool) -> set[str]:
            # Alternatives now re-parent to the slim structures family, not the result.
            searcher = opened.store.searcher(only_main_alt=only_main_alt)
            structure = searcher.variable(UnitcellStructureRecord)
            return {record["structure"].immutable_id for record in searcher.results(structure=structure)}

        mains = immutable_ids(only_main_alt=True)
        every = immutable_ids(only_main_alt=False)
        # The fixture reuses one CONTCAR: mat1 (with moments) and the shared mat2/mat3
        # (moment-free) collapse to two distinct structure mains by content-id dedup.
        assert mains == immutable_ids(only_main_alt=True) == {"anyt.am.structure-1-1~1", "anyt.am.structure-1-2~1"}
        alternatives = every - mains
        # Not silently alternatives-free: the fixture cells derive both kinds per structure.
        assert alternatives, "build stored no alternative cell records"
        assert alternatives == {
            f"anyt.am.structure-1-{number}~{kind}~1"
            for number in (1, 2)
            for kind in ("conventional", "primitive")
        }
    finally:
        opened.database.dispose()


def test_build_reports_structure_summary(tmp_path: Path, caplog) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    with caplog.at_level("INFO", logger="httk.altermagnets.material_store"):
        build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, legacy=True)
    assert "material records" in caplog.text and "with structures" in caplog.text


def test_two_successive_builds_replace_the_same_target(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    target = tmp_path / "store.duckdb"
    runs = tmp_path / "runs"
    build_store(target, data_dir=source, details_dir=details, runs_dir=runs)
    build_store(target, data_dir=source, details_dir=details, runs_dir=runs)
    assert target.is_file() and target.stat().st_size > 0


_RUN_POSCAR = """Fixture POSCAR
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
# Energy but no magnetization block: a coupled run that yields a moment-free structure.
_RUN_OUTCAR_ENERGY_ONLY = """ vasp.5.2.12 synthetic
   FREE ENERGIE OF THE ION-ELECTRON SYSTEM (eV)
   free  energy   TOTEN  =       -1.00000000 eV
   energy  without entropy=      -1.00000000  energy(sigma->0) =      -1.00000000
 General timing and accounting informations
"""


def _scf_run(runs: Path, material: str) -> None:
    task = runs / "1" / "Runs" / f"ht.task.tetralith--default.{material}_SCF.cleanup.0.unclaimed.3.finished"
    step = task / "ht.run.2025-01-01_00.00.00" / "ht.task.any.0.cleanup.0.unclaimed.3.finished"
    inner = step / "ht.run.2025-01-01_00.00.01"
    inner.mkdir(parents=True)
    (step / "POSCAR").write_text(_RUN_POSCAR, encoding="utf-8")
    (inner / "CONTCAR").write_text(_RUN_POSCAR, encoding="utf-8")
    (inner / "OUTCAR").write_text(_RUN_OUTCAR_ENERGY_ONLY, encoding="utf-8")
    # A file output so the coupled build exercises the ``files`` edge/product path.
    (inner / "vasprun.xml").write_text("<modeling/>\n", encoding="utf-8")


def _materials_by_id(store_path: Path) -> dict[str, AltermagnetScreeningResult]:
    opened = open_prebuilt_store(store_path)
    assert opened is not None
    try:
        searcher = opened.store.searcher()
        variable = searcher.variable(AltermagnetScreeningResult)
        return {row["material"].id: row["material"] for row in searcher.results(material=variable)}
    finally:
        opened.database.dispose()


def test_build_saves_only_coupled_runs_and_recovers_moments(tmp_path: Path) -> None:
    import duckdb

    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    runs = tmp_path / "runs"
    _scf_run(runs, "CrSb")  # couples anyt.am-1-1 by name; OUTCAR has no moments
    _scf_run(runs, "Zzz")  # collected but no CSV material: never coupled or saved
    target = build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, runs_dir=runs)

    # Only the one coupled run is saved, not every collected task.
    connection = duckdb.connect(str(target), read_only=True)
    try:
        assert connection.execute('select count(*) from "core_run"').fetchone()[0] == 1
    finally:
        connection.close()

    materials = _materials_by_id(target)
    # 2a lost-moments fallback: the coupled run is moment-free, details supplies moments.
    recovered = material_structure(materials["anyt.am-1-1"])
    assert recovered is not None and recovered.site_moments is not None
    # 2a no-structure fallback: MnTe has no run at all, details supplies the structure.
    assert material_structure(materials["anyt.am-1-2"]) is not None


def _material(store: object, amdb_id: str) -> AltermagnetScreeningResult:
    searcher = store.searcher()  # type: ignore[attr-defined]
    variable = searcher.variable(AltermagnetScreeningResult)
    searcher.add(variable.id == amdb_id)
    return searcher.results(material=variable).first()["material"]


def _all(store: object, cls: type) -> list:
    searcher = store.searcher()  # type: ignore[attr-defined]
    variable = searcher.variable(cls)
    return [row["record"] for row in searcher.results(record=variable)]


def _single(store: object, cls: type):
    rows = _all(store, cls)
    assert len(rows) == 1, f"expected exactly one {cls.__name__}, found {len(rows)}"
    return rows[0]


def test_coupled_material_reconstructs_run_with_resolvable_edges(tmp_path: Path) -> None:
    """The coupled build reconstructs the run with store-resolvable edges and rewrites its products.

    Replaces the retired ``produced_by`` weak link: the run's ``relaxed_structure``
    edge targets the material's own served id (S1), the record/file edges carry the
    minted ids of the outputs the bulk pass saved, and ``item.products`` are rewritten
    through the same id map -- so no collection-time content id survives on any edge.
    """
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    runs = tmp_path / "runs"
    _scf_run(runs, "CrSb")  # couples anyt.am-1-1; OUTCAR TOTEN is -1.0 eV; also writes a vasprun output
    target = build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, runs_dir=runs)

    opened = open_prebuilt_store(target)
    assert opened is not None
    try:
        store = opened.store
        coupled = _material(store, "anyt.am-1-1")
        assert coupled.total_energy == -1.0

        run = _single(store, Run)
        assert run.source_id  # carried over unchanged from the collected run
        # DataRecord outputs are stored as the AMDB records-family subclass (served at
        # _httk_records); the base DataRecord table holds none.
        assert _all(store, DataRecord) == []
        record_id = _single(store, material_store.AltermagnetDataRecord).id  # minted id of the bulk-saved output
        file_id = _single(store, FileRecord).id
        assert record_id and file_id
        structure_id = coupled.structure_id
        assert structure_id is not None

        # The run's outputs carry the relaxed structure (retargeted at its stamped id) plus
        # the record/file edges; the artifacts add the fresh has_artifact edge to the result.
        by_output = {edge.label: (edge.entry_type, edge.entry_id) for edge in run.outputs}
        assert by_output["relaxed_structure"] == ("structures", structure_id)
        assert by_output["total_energy"] == ("records", record_id)
        assert by_output["vasprun"] == ("files", file_id)
        assert "screening_result" not in by_output
        by_artifact = {edge.label: (edge.entry_type, edge.entry_id) for edge in run.artifacts}
        assert by_artifact["screening_result"] == ("altermagnet_screening_result", "anyt.am-1-1")
        # Every output is also an artifact (this workflow), plus the appended result edge.
        assert set(run.outputs) < set(run.artifacts)

        # The product links are rewritten through the same id map -- no content ids remain.
        products = {link.label: (link.source_id, link.target_id) for link in _all(store, ProductLink)}
        assert products["total_energy"] == (structure_id, record_id)
        assert products["vasprun"] == (structure_id, file_id)

        # An uncoupled material carries neither the scalar nor any run edge targeting it.
        uncoupled = _material(store, "anyt.am-1-2")
        assert uncoupled.total_energy is None
        assert not any(edge.entry_id == "anyt.am-1-2" for edge in run.outputs)
    finally:
        opened.database.dispose()


def test_resolve_edge_id_rejects_non_relaxed_structures_edge() -> None:
    """Only the relaxed_structure output maps to the material; a foreign structures edge raises."""
    with pytest.raises(ValueError, match="input_structure"):
        material_store._resolve_edge_id(
            None,  # type: ignore[arg-type]  # the structures guard fires before the store is touched
            "input_structure",
            "structures",
            "some-content-id",
            structure_id="anyt.am.structure-1-1",
            memo={},
        )


def _collinear_structure(
    lattice: list[list[float]], coords: list[list[float]], moments: list[list[float]]
) -> UnitcellStructure:
    species = [Species(name="Fe", chemical_symbols=("Fe",), concentration=(1.0,))]
    return UnitcellStructure(
        Cell(lattice),
        Sites(coords),
        species,
        ["Fe"] * len(coords),
        site_moments=CartesianSiteMoments(moments),
    )


def _z_moments(structure: UnitcellStructure) -> list[float]:
    assert structure.site_moments is not None
    return [row[2] for row in structure.site_moments.cartesian_moments.to_floats()]


def test_magnetic_alternative_cell_keeps_afm_primitive_cell() -> None:
    pytest.importorskip("spglib")
    # 2a x a x b doubled simple cubic with a +/- pair: the nuclear primitive would
    # halve it, but this cell already is the magnetic primitive.
    structure = _collinear_structure(
        [[2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0]],
        [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0]],
    )
    input_volume = float(abs(structure.cell.volume.to_float()))

    primitive = _magnetic_alternative_cell(structure, "primitive")
    assert len(primitive.sites) == 2
    assert float(abs(primitive.cell.volume.to_float())) == pytest.approx(input_volume)
    assert sorted(_z_moments(primitive)) == pytest.approx([-1.0, 1.0])

    conventional = _magnetic_alternative_cell(structure, "conventional")
    assert len(conventional.sites) == 2
    assert sorted(_z_moments(conventional)) == pytest.approx([-1.0, 1.0])
    # The projected record (which needs structure.composition) must accept the result.
    material_store._material_structure_record(conventional)


def test_magnetic_alternative_cell_reduces_afm_chain() -> None:
    pytest.importorskip("spglib")
    # A genuine reduction: a 4-site +/- chain folds to its 2-site magnetic primitive.
    structure = _collinear_structure(
        [[4.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[0.0, 0.0, 0.0], [0.25, 0.0, 0.0], [0.5, 0.0, 0.0], [0.75, 0.0, 0.0]],
        [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]],
    )
    input_volume = float(abs(structure.cell.volume.to_float()))

    primitive = _magnetic_alternative_cell(structure, "primitive")
    assert len(primitive.sites) == 2
    assert float(abs(primitive.cell.volume.to_float())) == pytest.approx(input_volume / 2.0)
    assert sorted(_z_moments(primitive)) == pytest.approx([-1.0, 1.0])


def test_magnetic_alternative_cell_rejects_noncollinear_moments() -> None:
    pytest.importorskip("spglib")
    structure = _collinear_structure(
        [[4.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[0.0, 0.0, 0.0], [0.25, 0.0, 0.0], [0.5, 0.0, 0.0], [0.75, 0.0, 0.0]],
        [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]],
    )
    with pytest.raises(ValueError, match="collinear"):
        _magnetic_alternative_cell(structure, "primitive")


def test_magnetic_alternative_cell_folds_non_orthogonal_lattice() -> None:
    pytest.importorskip("spglib")
    # A non-orthogonal (hexagonal) lattice quadrupled along a1 exercises the fold on a
    # cell where inv(L) != inv(L).T. (A transposed-inverse fold is caught by
    # test_magnetic_alternative_cell_reduces_afm_chain via the site-count check, since
    # spglib's primitive_lattice has a non-symmetric inverse even there; this test pins
    # the non-orthogonal input path.) The +/- chain folds 4 -> 2 at half volume.
    structure = _collinear_structure(
        [[12.0, 0.0, 0.0], [-1.5, 2.598076, 0.0], [0.0, 0.0, 5.0]],
        [[0.0, 0.0, 0.0], [0.25, 0.0, 0.0], [0.5, 0.0, 0.0], [0.75, 0.0, 0.0]],
        [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]],
    )
    input_volume = float(abs(structure.cell.volume.to_float()))

    primitive = _magnetic_alternative_cell(structure, "primitive")
    assert len(primitive.sites) == 2
    assert float(abs(primitive.cell.volume.to_float())) == pytest.approx(input_volume / 2.0)
    assert sorted(_z_moments(primitive)) == pytest.approx([-1.0, 1.0])


class _RecordingStore:
    """Minimal stand-in that records the alternative saves `_save_alternative_cells` makes."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []

    def save(self, record: object, *, alternative_of: str, alternative_kind: str) -> None:
        self.saved.append((alternative_of, alternative_kind))


def test_save_alternative_cells_uses_magnetic_fallback(tmp_path: Path) -> None:
    pytest.importorskip("spglib")
    # A real AltermagnetScreeningResult (from the fixture build) whose structure is swapped for the
    # 4-site +/- chain: its nuclear conventional/primitive derivations both refuse, so the
    # magnetic fallback must supply both kinds with nothing skipped.
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    target = build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, legacy=True)
    base = _materials_by_id(target)["anyt.am-1-1"]
    chain = _collinear_structure(
        [[4.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [[0.0, 0.0, 0.0], [0.25, 0.0, 0.0], [0.5, 0.0, 0.0], [0.75, 0.0, 0.0]],
        [[0.0, 0.0, 1.0], [0.0, 0.0, -1.0], [0.0, 0.0, 1.0], [0.0, 0.0, -1.0]],
    )
    material = replace(base, structure=material_store._material_structure_record(chain), id="anyt.am-1-fold")

    store = _RecordingStore()
    structure_ids = {"anyt.am-1-fold": "anyt.am.structure-1-1"}
    derived, skipped = material_store._save_alternative_cells(store, [material], structure_ids)  # type: ignore[arg-type]

    assert (derived, skipped) == (2, 0)
    assert sorted(kind for _, kind in store.saved) == ["conventional", "primitive"]
    # Alternatives re-parent to the structure main (its stamped id), not the result id.
    assert all(alternative_of == "anyt.am.structure-1-1" for alternative_of, _ in store.saved)
