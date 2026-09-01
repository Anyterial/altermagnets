from pathlib import Path

from conftest import write_detail_assets, write_source_tables
from httk.core import File
from httk.store import Backend
from material_store import (
    MaterialRecord,
    build_material_records,
    build_store,
    open_material_store,
    open_prebuilt_store,
)


def test_persistent_build_reconstructs_ordered_links_and_variants(material_store_path: Path) -> None:
    opened = open_prebuilt_store(material_store_path)
    assert opened is not None
    try:
        searcher = opened.store.searcher()
        material = searcher.variable(MaterialRecord)
        searcher.add(material.id == "anyt.am-1-1")
        record = searcher.results(material=material).one()["material"]
        assert [link.ordinal for link in record.links] == [1, 2]
        assert [link.record.id for link in record.links] == ["0.528", "0.800"]
        assert [variant.symprec for variant in record.links[0].record.variants] == [0.001, 0.01]
        assert [variant.source_kind for variant in record.links[1].record.variants] == [
            "collinear",
            "noncollinear-derived",
        ]
        assert [figure.key for figure in record.figures] == ["band"]
        assert isinstance(record.figures[0].light, File)
        assert record.figures[0].light.url.endswith("/band.svg")
        assert record.figures[0].light.name == "band.svg"
        assert record.figures[0].light.media_type == "image/svg+xml"
        assert record.figures[0].light.size is not None
    finally:
        opened.database.dispose()


def test_builder_atomically_replaces_an_existing_target(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    target = tmp_path / "altermagnets.duckdb"
    target.write_text("previous incomplete store", encoding="utf-8")
    assert build_store(target, data_dir=source, legacy=True) == target
    assert target.read_bytes() != b"previous incomplete store"
    opened = open_prebuilt_store(target)
    assert opened is not None
    opened.database.dispose()
    assert not list(tmp_path.glob(".altermagnets.duckdb.*.tmp"))


def test_builder_rejects_duplicates_and_nonfinite_values(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    records = build_material_records(
        [
            {
                "AMDBId": "anyt.am-1-1",
                "MAGNDATA ID": "0.900",
                "Material": "Fe_As",
                "Space group": "P4/nmm",
                "FdeltaPct": "nan",
                "MaxSS": "inf",
                "AvgSS": "-inf",
                "Bandgap": "NaN",
                "MinAbundPpm": "Infinity",
            }
        ],
        [],
        [],
    )
    assert records[0].max_ss is None
    assert records[0].avg_ss is None
    assert records[0].fdelta_pct is None
    assert records[0].bandgap is None
    assert records[0].links[0].record.variants == ()

    duplicate = (source / "high_throughput_screening_results_fixed.csv").read_text(encoding="utf-8")
    (source / "high_throughput_screening_results_fixed.csv").write_text(
        duplicate + duplicate.splitlines()[1] + "\n", encoding="utf-8"
    )
    try:
        build_store(tmp_path / "duplicate.duckdb", data_dir=source, legacy=True)
    except ValueError as error:
        assert "duplicate canonical material ID 'anyt.am-1-1'" in str(error)
    else:
        raise AssertionError("duplicate material ID was accepted")


def test_missing_corrupt_and_zero_stores_are_unavailable(tmp_path: Path) -> None:
    assert open_prebuilt_store(tmp_path / "missing.duckdb") is None
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_text("not a duckdb database", encoding="utf-8")
    assert open_prebuilt_store(corrupt) is None
    zero = tmp_path / "zero.duckdb"
    database = Backend.duckdb(zero)
    database.dispose()
    assert open_prebuilt_store(zero) is None
    assert open_material_store(tmp_path / "missing.duckdb", data_dir=tmp_path / "missing-tables") is None


def test_runtime_falls_back_when_persistent_store_has_the_old_schema(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    legacy_path = tmp_path / "legacy.duckdb"
    legacy_database = Backend.duckdb(legacy_path)
    with legacy_database.engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE altermagnets_material_records (sid BIGINT PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO altermagnets_material_records (sid) VALUES (1)")
    legacy_database.dispose()

    assert open_prebuilt_store(legacy_path) is None
    opened = open_material_store(
        legacy_path,
        data_dir=source,
        details_dir=details,
    )
    assert opened is not None
    try:
        assert opened.mode == "memory"
        searcher = opened.store.searcher()
        material = searcher.variable(MaterialRecord)
        searcher.add(material.id == "anyt.am-1-1")
        record = searcher.results(material=material).first()
        assert record is not None
        assert [figure.key for figure in record["material"].figures] == ["band"]
    finally:
        opened.database.dispose()
