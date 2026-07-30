from pathlib import Path

import get_material
import init
import search_materials
from conftest import write_source_tables
from httk.data.db import Database
from material_store import (
    MaterialRecord,
    build_material_records,
    build_store,
    cleanup_material_store,
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
        assert record.links[0].record.id == "0.528"
        assert record.links[1].record.id == "0.800"
        assert [variant.symprec for variant in record.links[0].record.variants] == [0.001, 0.01]
        assert record.links[1].record.variants[0].source_kind == "collinear"
        assert record.links[1].record.variants[1].source_kind == "noncollinear-derived"
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


def test_runtime_opens_only_prebuilt_store_and_cleanup_is_idempotent(
    material_store_path: Path, tmp_path: Path, monkeypatch
) -> None:
    source = material_store_path.parent / "tables"
    source.rename(tmp_path / "source-removed")
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(material_store_path))
    global_data = {}
    init.execute(global_data)
    assert global_data["site_stats"]["dataset_available"] is True
    assert search_materials.execute(global_data, q="CrSb")["count"] == 1
    cleanup_material_store(global_data)
    cleanup_material_store(global_data)
    assert "materials_database" not in global_data


def test_missing_corrupt_and_zero_stores_are_unavailable(tmp_path: Path) -> None:
    assert open_prebuilt_store(tmp_path / "missing.duckdb") is None
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_text("not a duckdb database", encoding="utf-8")
    assert open_prebuilt_store(corrupt) is None
    zero = tmp_path / "zero.duckdb"
    database = Database.duckdb(zero)
    database.dispose()
    assert open_prebuilt_store(zero) is None


def test_store_search_filters_sorts_and_literal_like_characters(material_store_path: Path) -> None:
    opened = open_prebuilt_store(material_store_path)
    assert opened is not None
    try:
        global_data = {"materials_store": opened.store, "site_stats": {"dataset_available": True, "total_materials": 3}}
        assert search_materials.execute(global_data, elements="Cr Sb")["count"] == 1
        assert search_materials.execute(global_data, elements="Cr Te")["count"] == 0
        assert search_materials.execute(global_data, classification="mixed")["count"] == 2
        assert search_materials.execute(global_data, electronic_type="unknown")["count"] == 1
        assert search_materials.execute(global_data, magnetic_phase="AM")["count"] == 2
        assert search_materials.execute(global_data, wave_class="g")["count"] == 1
        assert search_materials.execute(global_data, space_group="p6_3/MMC")["count"] == 2
        assert search_materials.execute(global_data, space_group="p6")["count"] == 2
        assert search_materials.execute(global_data, min_max_ss="1.0")["count"] == 1
        assert search_materials.execute(global_data, max_bandgap="0")["count"] == 1
        assert search_materials.execute(global_data, min_abundance_ppm="0.01")["count"] == 1
        assert search_materials.execute(global_data, q="P6Fe_")["count"] == 1
        assert search_materials.execute(global_data, q="%")["count"] == 0
        assert [item["material_id"] for item in search_materials.execute(global_data, sort="max_ss_desc")["items"]] == [
            "anyt:am-1-0001",
            "anyt:am-1-0002",
            "anyt:am-1-0003",
        ]
        assert [item["material_id"] for item in search_materials.execute(global_data, sort="bandgap_desc")["items"]] == [
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
        assert detail["linked_entries"][0]["bns_mcif_label"].startswith("$")
        unresolved = get_material.execute({"materials_store": opened.store}, id="anyt:am-1-0003")
        assert unresolved is not None
        assert unresolved["linked_entries"][0]["source_label"] == "No symmetry table entry"
        assert unresolved["linked_entries"][0]["magndata_id"] == "0.900"
    finally:
        opened.database.dispose()


def test_init_stats_and_features_use_open_store(material_store_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(material_store_path))
    global_data = {}
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
        cleanup_material_store(global_data)
