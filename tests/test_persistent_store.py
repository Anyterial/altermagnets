from pathlib import Path
from typing import Any

import get_material
import init
import pytest
import search_materials
from conftest import write_detail_assets, write_source_tables
from httk.core import File
from httk.data.db import Database
from httk.serve.web import SITE_RESOURCES_KEY, SiteResources
from material_store import (
    MaterialRecord,
    build_material_records,
    build_store,
    cleanup_material_store,
    open_material_store,
    open_prebuilt_store,
)


def test_persistent_build_reconstructs_ordered_links_and_variants(material_store_path: Path) -> None:
    opened = open_prebuilt_store(material_store_path)
    assert opened is not None
    try:
        searcher = opened.store.searcher()
        material = searcher.variable(MaterialRecord)
        searcher.add(material.id == "anyt:am-1-0001")
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
    assert build_store(target, data_dir=source) == target
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
                "AMDBId": "anyt:am-1-0001",
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
        build_store(tmp_path / "duplicate.duckdb", data_dir=source)
    except ValueError as error:
        assert "duplicate canonical material ID 'anyt:am-1-0001'" in str(error)
    else:
        raise AssertionError("duplicate material ID was accepted")


def test_runtime_prefers_prebuilt_store_and_resources_clean_it_up(
    material_store_path: Path, tmp_path: Path, monkeypatch
) -> None:
    (material_store_path.parent / "tables").rename(tmp_path / "source-removed")
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(material_store_path))
    resources = SiteResources()
    global_data: dict[str, Any] = {SITE_RESOURCES_KEY: resources}
    init.execute(global_data)
    assert global_data["site_stats"]["dataset_available"] is True
    assert global_data["materials_store_mode"] == "persistent"
    assert global_data["materials_store_source"] == material_store_path
    assert global_data["site_stats"]["store_mode"] == "persistent"
    assert str(global_data["materials_store_revision"]).startswith("duckdb-")
    results, order = search_materials.search_materials(global_data["materials_store"], q="CrSb")
    assert [row["material"].id for row in results.page(size=1, order_by=order).rows] == ["anyt:am-1-0001"]
    resources.close()
    cleanup_material_store(global_data)
    assert "materials_database" not in global_data


def test_runtime_falls_back_to_in_memory_store_when_persistent_store_is_absent(
    tmp_path: Path, monkeypatch
) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    missing_store = tmp_path / "not-built.duckdb"
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(missing_store))
    monkeypatch.setenv("ALTERMAGNETS_DATA_DIR", str(source))
    monkeypatch.setenv("ALTERMAGNETS_DETAILS_DIR", str(details))
    resources = SiteResources()
    global_data: dict[str, Any] = {SITE_RESOURCES_KEY: resources}

    init.execute(global_data)
    try:
        assert global_data["site_stats"]["dataset_available"] is True
        assert global_data["materials_store_mode"] == "memory"
        assert global_data["materials_store_source"] == source
        assert global_data["site_stats"]["store_mode"] == "memory"
        assert str(global_data["materials_store_revision"]).startswith("memory-")
        results, order = search_materials.search_materials(global_data["materials_store"], q="CrSb")
        assert [row["material"].id for row in results.page(size=1, order_by=order).rows] == [
            "anyt:am-1-0001"
        ]
        material = results.page(size=1, order_by=order).rows[0]["material"]
        assert [figure.key for figure in material.figures] == ["band"]
        assert isinstance(material.figures[0].light, File)
        assert not missing_store.exists()
    finally:
        resources.close()


def test_missing_corrupt_and_zero_stores_are_unavailable(tmp_path: Path) -> None:
    assert open_prebuilt_store(tmp_path / "missing.duckdb") is None
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_text("not a duckdb database", encoding="utf-8")
    assert open_prebuilt_store(corrupt) is None
    zero = tmp_path / "zero.duckdb"
    database = Database.duckdb(zero)
    database.dispose()
    assert open_prebuilt_store(zero) is None
    assert open_material_store(tmp_path / "missing.duckdb", data_dir=tmp_path / "missing-tables") is None


def test_runtime_falls_back_when_persistent_store_has_the_old_schema(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    legacy_path = tmp_path / "legacy.duckdb"
    legacy_database = Database.duckdb(legacy_path)
    with legacy_database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE altermagnets_material_records (sid BIGINT PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            "INSERT INTO altermagnets_material_records (sid) VALUES (1)"
        )
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
        searcher.add(material.id == "anyt:am-1-0001")
        record = searcher.results(material=material).first()
        assert record is not None
        assert [figure.key for figure in record["material"].figures] == ["band"]
    finally:
        opened.database.dispose()


def test_initialization_registers_cleanup_before_feature_queries(material_store_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(material_store_path))
    resources = SiteResources()
    global_data: dict[str, Any] = {SITE_RESOURCES_KEY: resources}

    def fail_stats(*args, **kwargs):
        raise ValueError("synthetic startup failure")

    monkeypatch.setattr(init, "_site_stats", fail_stats)
    with pytest.raises(ValueError, match="synthetic startup failure"):
        init.execute(global_data)
    assert "materials_database" in global_data
    resources.close()
    assert "materials_database" not in global_data


def test_store_search_filters_sorts_and_literal_like_characters(material_store_path: Path) -> None:
    opened = open_prebuilt_store(material_store_path)
    assert opened is not None
    try:
        def material_ids(**query: str) -> list[str]:
            results, order = search_materials.search_materials(opened.store, **query)
            return [row["material"].id for row in results.page(size=20, order_by=order).rows]

        assert len(material_ids(elements="Cr Sb")) == 1
        assert material_ids(elements="Cr Te") == []
        assert len(material_ids(classification="mixed")) == 2
        assert len(material_ids(electronic_type="unknown")) == 1
        assert len(material_ids(magnetic_phase="AM")) == 2
        assert len(material_ids(wave_class="g")) == 1
        assert len(material_ids(space_group="p6_3/MMC")) == 2
        assert len(material_ids(space_group="p6")) == 2
        assert len(material_ids(min_max_ss="1.0")) == 1
        assert len(material_ids(max_bandgap="0")) == 1
        assert len(material_ids(min_abundance_ppm="0.01")) == 1
        assert len(material_ids(q="P6Fe_")) == 1
        assert material_ids(q="%") == []
        assert material_ids(sort="max_ss_desc") == [
            "anyt:am-1-0001",
            "anyt:am-1-0002",
            "anyt:am-1-0003",
        ]
        assert material_ids(sort="bandgap_desc") == [
            "anyt:am-1-0002",
            "anyt:am-1-0001",
            "anyt:am-1-0003",
        ]
        results, order = search_materials.search_materials(opened.store, q="CrSb")
        page = results.page(size=1, order_by=order, include_total=True)
        assert page.total == 1
        assert page.rows[0]["material"].id == "anyt:am-1-0001"
    finally:
        opened.database.dispose()


def test_detail_alias_order_decoration_and_unresolved_link(material_store_path: Path) -> None:
    opened = open_prebuilt_store(material_store_path)
    assert opened is not None
    try:
        detail = get_material.execute(
            {"materials_store": opened.store, "detail_assets_root": material_store_path.parent / "details"},
            id="amdb-1-0001",
        )
        assert detail is not None
        assert detail["material_id"] == "anyt:am-1-0001"
        assert [entry["magndata_id"] for entry in detail["linked_entries"]] == ["0.528", "0.528", "0.800", "0.800"]
        unresolved = get_material.execute({"materials_store": opened.store}, id="anyt:am-1-0003")
        assert unresolved is not None
        assert unresolved["linked_entries"][0]["source_label"] == "No symmetry table entry"
    finally:
        opened.database.dispose()


def test_init_stats_and_features_use_open_store(material_store_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(material_store_path))
    resources = SiteResources()
    global_data: dict[str, Any] = {SITE_RESOURCES_KEY: resources}
    init.execute(global_data)
    try:
        assert global_data["site_stats"]["classification_counts"] == {
            "collinear": 0,
            "noncollinear-derived": 0,
            "mixed": 2,
            "unclassified": 1,
        }
        assert global_data["site_stats"]["electronic_counts"]["unknown"] == 1
        assert global_data["featured_materials"]["largest_splitting"][0]["material_id"] == "anyt:am-1-0001"
    finally:
        resources.close()
