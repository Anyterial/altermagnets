"""Tests for ledger-sourced result ids and the one-time results-family seeding.

The served material ids ``anyt.am-1-N`` come from the ledger's ``results`` family,
keyed by each screening row's normalized MAGNDATA cell. The first build seeds the
family once, in row order, each id asserted against that row's ``AMDBId`` column;
thereafter the column is transition-verification-only and the ids are pure lookups.
"""

import csv
import json
import sqlite3
from pathlib import Path

import duckdb
import material_store
import pytest
from conftest import write_detail_assets, write_source_tables
from httk.core.project.sealing import resolve_seal_keys
from httk.store import IdLedger
from material_store import _normalize_magndata_cell, _open_ledger, _result_key, _seed_result_ids, build_store


def _keys(tables: Path) -> object:
    return resolve_seal_keys(material_store.LEDGER_SIGNER_REFS, project_root=tables).keys


def _served_material_ids(store_path: Path) -> set[str]:
    connection = duckdb.connect(str(store_path), read_only=True)
    try:
        return {row[0] for row in connection.execute("select id from altermagnets_screening_results").fetchall()}
    finally:
        connection.close()


def _served_id_to_formula(store_path: Path) -> dict[str, str]:
    connection = duckdb.connect(str(store_path), read_only=True)
    try:
        return dict(connection.execute("select id, formula from altermagnets_screening_results").fetchall())
    finally:
        connection.close()


def _ledger_records(tables: Path, *, family: str | None = None) -> list[dict[str, str]]:
    connection = sqlite3.connect(tables / material_store.LEDGER_FILENAME)
    try:
        rows = connection.execute("SELECT key, family, id, alias_of, supersedes FROM records ORDER BY seq").fetchall()
    finally:
        connection.close()
    records: list[dict[str, str]] = []
    for key, fam, entry_id, alias_of, supersedes in rows:
        if alias_of is not None:
            record = {"key": key, "alias_of": alias_of}
        else:
            record = {"key": key, "family": fam, "id": entry_id}
        if supersedes is not None:
            record["supersedes"] = supersedes
        records.append(record)
    return records if family is None else [r for r in records if r.get("family") == family]


def _ledger_bases(tables: Path) -> dict[str, str]:
    """Return the ledger's live per-family bases from its latest segment's signed subject."""
    connection = sqlite3.connect(tables / material_store.LEDGER_FILENAME)
    try:
        subject = connection.execute("SELECT subject FROM segments ORDER BY segment DESC LIMIT 1").fetchone()[0]
    finally:
        connection.close()
    return json.loads(subject)["bases"]


def _build(tmp_path: Path, tables: Path) -> Path:
    """Build a non-legacy store (no v1 runs; details supply the structures)."""
    details = write_detail_assets(tmp_path / "details")
    return build_store(
        tmp_path / "store.duckdb",
        data_dir=tables,
        tables_dir=tables,
        details_dir=details,
        runs_dir=tmp_path / "no-runs",
    )


def _screening_path(tables: Path) -> Path:
    return tables / material_store.SCREENING_RESULTS_FILENAME


def _rewrite_screening(tables: Path, rows: list[dict[str, str]]) -> None:
    path = _screening_path(tables)
    fields = list(rows[0]) if rows else ["AMDBId", "MAGNDATA ID", "Material"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def _five_family_ledger(tables: Path) -> bytes:
    """Create a pre-transition (5-family, no ``results``) ledger and return its bytes."""
    bases = {family: base for family, base in material_store.LEDGER_BASES.items() if family != "results"}
    path = tables / material_store.LEDGER_FILENAME
    with IdLedger.create(path, bases=bases, series=material_store.LEDGER_SERIES, keys=_keys(tables)):
        pass
    return path.read_bytes()


# -- happy path: seeding + ledger sourcing -----------------------------------


def test_first_build_seeds_results_family_and_serves_ledger_ids(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    store = _build(tmp_path, tables)
    # The results family is seeded dense in row order.
    results = _ledger_records(tables, family="results")
    assert [r["id"] for r in results] == ["anyt.am-1-1", "anyt.am-1-2", "anyt.am-1-3"]
    # Keyed by the normalized magndata cell (row 1 sorts "0.528,0.800").
    assert {r["key"] for r in results} == {_result_key("0.528,0.800"), _result_key("0.800"), _result_key("0.900")}
    assert _served_material_ids(store) == {"anyt.am-1-1", "anyt.am-1-2", "anyt.am-1-3"}


def test_second_build_leaves_ledger_byte_identical(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    _build(tmp_path, tables)
    after_first = (tables / material_store.LEDGER_FILENAME).read_bytes()
    _build(tmp_path, tables)
    assert (tables / material_store.LEDGER_FILENAME).read_bytes() == after_first


def test_superset_open_adds_results_family_to_a_five_family_ledger(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    _five_family_ledger(tables)
    assert "results" not in _ledger_bases(tables)
    _build(tmp_path, tables)
    assert _ledger_bases(tables)["results"] == "anyt.am"
    assert len(_ledger_records(tables, family="results")) == 3


# -- S1: the AMDBId column is transition-verification-only after seeding ------


def test_swapping_amdb_column_values_keeps_served_ids(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    first = _build(tmp_path, tables)
    # The id->formula mapping, not just the id SET: a permutation leaves the set
    # unchanged, so a regression to column-sourced identity must move this mapping.
    mapping_before = _served_id_to_formula(first)
    ledger_before = (tables / material_store.LEDGER_FILENAME).read_bytes()
    # Swap the AMDBId column values of rows 1 and 2 post-seed. The magndata keys are
    # unchanged, so each formula keeps its ledger id.
    rows = list(csv.DictReader(_screening_path(tables).open(encoding="utf-8"), delimiter=";"))
    rows[0]["AMDBId"], rows[1]["AMDBId"] = rows[1]["AMDBId"], rows[0]["AMDBId"]
    _rewrite_screening(tables, rows)
    second = _build(tmp_path, tables)
    assert _served_id_to_formula(second) == mapping_before
    assert (tables / material_store.LEDGER_FILENAME).read_bytes() == ledger_before


def test_deleting_amdb_column_keeps_served_ids(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    first = _build(tmp_path, tables)
    # The id->formula mapping: a regression to the positional fallback yields the same
    # dense id SET but a different mapping, so compare the mapping.
    mapping_before = _served_id_to_formula(first)
    # Drop the AMDBId column entirely; the build still succeeds off the ledger.
    rows = list(csv.DictReader(_screening_path(tables).open(encoding="utf-8"), delimiter=";"))
    for row in rows:
        del row["AMDBId"]
    _rewrite_screening(tables, rows)
    second = _build(tmp_path, tables)
    assert _served_id_to_formula(second) == mapping_before


# -- seeding pre-validation aborts, leaving the ledger byte-identical ---------


def _bad_screening(tables: Path, rows: list[dict[str, str]]) -> bytes:
    """Install a pre-transition ledger, then overwrite the screening CSV; return ledger bytes."""
    before = _five_family_ledger(tables)
    _rewrite_screening(tables, rows)
    return before


def _row(amdb: str, magndata: str, material: str = "CrSb") -> dict[str, str]:
    return {"AMDBId": amdb, "MAGNDATA ID": magndata, "Material": material, "Space group": "P1"}


def test_seed_aborts_on_non_dense_amdb_column(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    before = _bad_screening(
        tables, [_row("anyt.am-1-1", "0.1"), _row("anyt.am-1-2", "0.2"), _row("anyt.am-1-4", "0.3")]
    )
    with pytest.raises(ValueError, match="dense"):
        _build(tmp_path, tables)
    assert (tables / material_store.LEDGER_FILENAME).read_bytes() == before


def test_seed_aborts_on_duplicate_magndata_keys(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    before = _bad_screening(
        tables, [_row("anyt.am-1-1", "0.5"), _row("anyt.am-1-2", "0.5"), _row("anyt.am-1-3", "0.6")]
    )
    with pytest.raises(ValueError, match="not unique"):
        _build(tmp_path, tables)
    assert (tables / material_store.LEDGER_FILENAME).read_bytes() == before


def test_seed_aborts_on_empty_magndata(tmp_path: Path) -> None:
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    before = _bad_screening(
        tables, [_row("anyt.am-1-1", "0.5"), _row("anyt.am-1-2", ""), _row("anyt.am-1-3", "0.6")]
    )
    with pytest.raises(ValueError, match="empty MAGNDATA"):
        _build(tmp_path, tables)
    assert (tables / material_store.LEDGER_FILENAME).read_bytes() == before


def test_seed_refuses_a_non_empty_results_family(tmp_path: Path) -> None:
    """The seeding gate refuses to seed when a results key is already bound."""
    tables = write_source_tables(tmp_path / "tables", material_count=3)
    with _open_ledger(tables) as ledger:  # creates a fresh 6-family ledger
        ledger.assign(_result_key("0.528,0.800"), "results")
    rows = list(csv.DictReader(_screening_path(tables).open(encoding="utf-8"), delimiter=";"))
    with _open_ledger(tables) as ledger, pytest.raises(ValueError, match="already binds"):
        _seed_result_ids(ledger, rows)


# -- S2: the in-memory fallback uses the AMDBId column, positional when absent -


def test_fallback_uses_amdb_column_then_positional(tmp_path: Path) -> None:
    with_column = [_row("anyt.am-1-9", "0.5"), _row("anyt.am-1-8", "0.6")]
    materials = material_store.build_material_records(with_column, [], [], result_ids=None)
    assert [m.id for m in materials] == ["anyt.am-1-9", "anyt.am-1-8"]  # AMDBId column when present
    without_column = [{"MAGNDATA ID": "0.5", "Material": "CrSb"}, {"MAGNDATA ID": "0.6", "Material": "MnTe"}]
    positional = material_store.build_material_records(without_column, [], [], result_ids=None)
    assert [m.id for m in positional] == ["anyt.am-1-1", "anyt.am-1-2"]  # positional when absent


def test_normalize_magndata_cell_strips_sorts_and_drops_empty_tokens() -> None:
    assert _normalize_magndata_cell("0.296, 0.295") == "0.295,0.296"  # split, strip, sort
    assert _normalize_magndata_cell(" 0.8 ") == "0.8"  # whitespace stripped
    assert _normalize_magndata_cell("0.5, , 0.4,") == "0.4,0.5"  # empty tokens dropped
    assert _normalize_magndata_cell("") == ""  # empty cell -> empty key token
    assert _normalize_magndata_cell("0.30,0.30") == "0.30,0.30"  # duplicate tokens kept (cell is the identity)
