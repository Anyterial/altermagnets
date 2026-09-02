from pathlib import Path

import material_store
import pytest
from conftest import write_detail_assets, write_source_tables
from httk.atomistic import CartesianSiteMoments
from material_store import (
    MaterialRecord,
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
        material = searcher.variable(MaterialRecord)
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

        def immutable_ids(*, only_main_alt: bool) -> set[str]:
            searcher = opened.store.searcher(only_main_alt=only_main_alt)
            material = searcher.variable(MaterialRecord)
            return {record["material"].immutable_id for record in searcher.results(material=material)}

        mains = immutable_ids(only_main_alt=True)
        every = immutable_ids(only_main_alt=False)
        # Default (mains-only) queries hide the alternatives; asking for them reveals more.
        assert mains == immutable_ids(only_main_alt=True) == {"anyt.am-1-1~1", "anyt.am-1-2~1", "anyt.am-1-3~1"}
        alternatives = every - mains
        # Not silently alternatives-free: the fixture cells derive both kinds.
        assert alternatives, "build stored no alternative cell records"
        assert alternatives == {
            f"anyt.am-1-{number}~{kind}~1" for number in (1, 2, 3) for kind in ("conventional", "primitive")
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


def _materials_by_id(store_path: Path) -> dict[str, MaterialRecord]:
    opened = open_prebuilt_store(store_path)
    assert opened is not None
    try:
        searcher = opened.store.searcher()
        variable = searcher.variable(MaterialRecord)
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


def _material(store: object, amdb_id: str) -> MaterialRecord:
    searcher = store.searcher()  # type: ignore[attr-defined]
    variable = searcher.variable(MaterialRecord)
    searcher.add(variable.id == amdb_id)
    return searcher.results(material=variable).first()["material"]


def test_coupled_material_links_to_run_and_carries_total_energy(tmp_path: Path) -> None:
    """A coupled build asserts the produced_by weak link and the total-energy scalar."""
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    runs = tmp_path / "runs"
    _scf_run(runs, "CrSb")  # couples anyt.am-1-1; OUTCAR TOTEN is -1.0 eV
    target = build_store(tmp_path / "store.duckdb", data_dir=source, details_dir=details, runs_dir=runs)

    opened = open_prebuilt_store(target)
    assert opened is not None
    try:
        coupled = _material(opened.store, "anyt.am-1-1")
        assert coupled.total_energy == -1.0
        linked = opened.store.linked(coupled, "produced_by", eager=True)
        assert len(linked) == 1
        assert linked[0].source_id  # the collected run carries a non-empty source id
        # An uncoupled material carries neither the scalar nor a producing run.
        uncoupled = _material(opened.store, "anyt.am-1-2")
        assert uncoupled.total_energy is None
        assert opened.store.linked(uncoupled, "produced_by") == ()
    finally:
        opened.database.dispose()
