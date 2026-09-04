"""Regression tests for the review fixes.

These assert deployment-level behavior: the internal ``_httk_custom_public_id``
property is not filterable (400) through the store-backed OPTIMADE service, while
an ordinary queryable property is accepted (200). Driven in-process through a
Starlette ``TestClient`` (no ports bound); skipped without the workspace httk
modules on the path.
"""

import logging
import sys
from pathlib import Path

import pytest

pytest.importorskip("httk.serve.optimade")
pytest.importorskip("httk.atomistic")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import material_store
from conftest import write_source_tables
from optimade import RESULT_TYPE, build_service_app
from starlette.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path):
    """A store-backed service (the deployment path) over a small seeded fixture."""
    source = write_source_tables(tmp_path / "tables")
    opened = material_store.open_in_memory_store(source, details_dir=tmp_path / "details")
    assert opened is not None
    app = build_service_app(
        public_base_url="https://api.example.test/optimade/amdb",
        store=opened.store,
        details_root=tmp_path / "details",
    )
    try:
        with TestClient(app, base_url="http://testserver") as live:
            yield live
    finally:
        opened.database.dispose()


def _is_optimade_error(response) -> bool:
    errors = response.json().get("errors")
    return isinstance(errors, list) and bool(errors)


def test_internal_public_id_is_not_filterable_on_structures(client: TestClient) -> None:
    # The public-id remap follows the screening result main entity now.
    response = client.get(f"/v1/{RESULT_TYPE}", params={"filter": '_httk_custom_public_id="anyt.am-1-1"'})
    assert response.status_code == 400
    assert _is_optimade_error(response)


def test_public_id_filter_drives_internal_remap_and_returns_row(client: TestClient) -> None:
    # Control guarding exactly the regression class: a client filter on the public
    # `id` drives the adapter's `_rewrite_id_filter` (id -> _httk_custom_public_id)
    # and must still serve the row, while a direct external filter on
    # _httk_custom_public_id (above) still 400s. This exercises the internal remap
    # path that broke earlier; a non-id filter would not.
    response = client.get(f"/v1/{RESULT_TYPE}", params={"filter": 'id="anyt.am-1-1"', "response_fields": "id"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["anyt.am-1-1"]


def test_unusable_prebuilt_store_is_rejected_with_a_logged_reason(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # An existing-but-unusable store (here: zero-size) must be refused, so the
    # caller falls back, and must log why. The operator-facing WARNING is emitted
    # once by the application at the fallback decision, not here.
    empty = tmp_path / "altermagnets.duckdb"
    empty.touch()
    with caplog.at_level(logging.INFO, logger="httk.altermagnets.material_store"):
        assert material_store.open_prebuilt_store(empty) is None
    assert str(empty) in caplog.text
