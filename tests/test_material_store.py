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
    structure = load_material_structure(details, "anyt:am-1-0001")
    assert structure is not None
    assert structure.site_moments is not None
    assert structure.site_moments == CartesianSiteMoments([[0.0, 0.0, 0.6], [0.0, 0.0, 0.0], [0.0, 0.0, 1.25]])


def test_load_material_structure_warns_and_omits_invalid_moments(tmp_path: Path, caplog) -> None:
    details = write_detail_assets(tmp_path / "details")
    with caplog.at_level("WARNING"):
        structure = load_material_structure(details, "anyt:am-1-0002")
    assert structure is not None
    assert structure.site_moments is None
    assert "2 rows for 3 sites" in caplog.text


def test_material_structure_round_trips_through_store(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    expected = load_material_structure(details, "anyt:am-1-0001")
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
