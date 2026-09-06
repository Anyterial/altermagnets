"""Regression: a narrow ``response_fields`` query must read only the requested
property's field(s) off the lazy record row, not materialize the record's full
property closure first.

Previously ``material_store._provider_response(name)`` routed through
``serve.dataset._material_properties(record, "")``, which built every served
property before indexing one name out -- touching every field on the lazy row
regardless of what was asked for. The fix is ``serve.dataset._PROPERTY_READERS``:
one reader per served property, each touching only its own field(s); the
provider response for a property now resolves directly to its reader.
"""

from pathlib import Path
from typing import Any

import material_store
import sqlalchemy
from conftest import write_detail_assets, write_source_tables
from serve import adapter
from serve import dataset as dataset_module

#: Measured: the old full-`_material_properties`-build code ran 45 statements
#: for this single-property, single-row query; the per-property reader fix
#: runs 15. The ceiling is deliberately looser than the measured 15 so this
#: stays stable across incidental backend detail, while still comfortably
#: catching a regression back toward the full-dict-build pattern (45).
_STATEMENT_CEILING = 20


def test_narrow_response_fields_query_skips_full_property_build(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    opened = material_store.open_in_memory_store(source, details_dir=details)
    assert opened is not None
    try:
        query = adapter.AltermagnetStoreAdapter(opened.store, "https://api.example.test/optimade/amdb").query_function()

        statements: list[str] = []

        def record(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool) -> None:
            statements.append(statement)

        sqlalchemy.event.listen(opened.database.engine, "before_cursor_execute", record)
        try:
            rows = list(query([adapter.RESULT_TYPE], ["_anyterial_formula"], [], 1, 0))
        finally:
            sqlalchemy.event.remove(opened.database.engine, "before_cursor_execute", record)

        assert rows
        assert rows[0].values["_anyterial_formula"]
        assert len(statements) < _STATEMENT_CEILING, (
            f"expected a single-property response_fields query to stay lean "
            f"(<{_STATEMENT_CEILING} statements), saw {len(statements)}: {statements}"
        )
    finally:
        opened.database.dispose()


def test_default_fields_query_matches_material_properties_values(tmp_path: Path) -> None:
    """Restructuring into per-property readers must not change any served value
    (fraction convention, figure URLs, variants shaping, ...): the store-native
    response for every served property must equal ``_material_properties``'s
    in-memory projection of the same record."""
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    opened = material_store.open_in_memory_store(source, details_dir=details)
    assert opened is not None
    try:
        public_base_url = "https://api.example.test/optimade/amdb"
        query = adapter.AltermagnetStoreAdapter(opened.store, public_base_url).query_function()
        all_fields = sorted(dataset_module._PROPERTY_READERS)
        served = {row.values["id"]: row.values for row in query([adapter.RESULT_TYPE], all_fields, [], 200, 0)}
        assert served

        searcher = opened.store.searcher()
        material = searcher.variable(material_store.AltermagnetScreeningResult)
        checked = 0
        for result in searcher.results(material=material):
            record = result["material"]
            expected = dataset_module._material_properties(record, public_base_url)
            for name, value in expected.items():
                assert served[record.id].get(name) == value, (
                    f"{record.id}.{name}: {served[record.id].get(name)!r} != {value!r}"
                )
            checked += 1
        assert checked
    finally:
        opened.database.dispose()
