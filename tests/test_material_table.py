import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from conftest import write_source_tables
from httk.serve.web import ProviderContext, TableRequest, create_asgi_app, publish
from material_store import build_store, open_prebuilt_store
from materials import provide
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
_ROW_ID = re.compile(r'class="id-mono">([^<]+)</span>')
_TOKEN = re.compile(r'data-httk-serve-table-next data-token="([^"]*)"')


def _ids(html: str) -> list[str]:
    return _ROW_ID.findall(html)


def _next_token(html: str) -> str | None:
    match = _TOKEN.search(html)
    if match is None or not match.group(1):
        return None
    return unescape(match.group(1))


def _detail_queries(html: str) -> list[dict[str, list[str]]]:
    return [
        parse_qs(urlparse(unescape(match)).query)
        for match in re.findall(r'href="([^"]+)"', html)
        if urlparse(unescape(match)).path in {"/material", "material"}
    ]


def _store_with_materials(tmp_path: Path, *, count: int) -> Path:
    source = write_source_tables(tmp_path / "tables", material_count=count)
    return build_store(tmp_path / "altermagnets.duckdb", data_dir=source)


def test_provider_fetches_one_page_and_keeps_a_safe_filter_snapshot(tmp_path: Path) -> None:
    store_path = _store_with_materials(tmp_path, count=180)
    opened = open_prebuilt_store(store_path)
    assert opened is not None
    try:
        context = ProviderContext(
            route="search",
            widget_id="materials-results",
            query={"q": "CrSb", "space_group": "P6_3/mmc", "id": "attacker", "sort": "screening_rank"},
            page={"relbaseurl": "."},
            global_data={"materials_store": opened.store, "materials_store_revision": "revision-a"},
        )
        page = provide(context, TableRequest(page_size=1))
        assert len(page.rows) == 1
        assert page.rows[0]["material_id"] == "anyt:am-1-0001"
        assert page.revision == "revision-a"
        query = parse_qs(urlparse(str(page.rows[0]["detail_url"])).query)
        assert query["id"] == ["anyt:am-1-0001"]
        assert query["q"] == ["CrSb"]
        assert query["space_group"] == ["P6_3/mmc"]
        assert "attacker" not in str(page.rows[0]["detail_url"])
    finally:
        opened.database.dispose()


def test_dynamic_table_pages_a_180_record_store_without_materializing(tmp_path: Path, monkeypatch) -> None:
    store_path = _store_with_materials(tmp_path, count=180)
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(store_path))
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic", table_token_secret="s" * 32)

    with TestClient(app) as client:
        filtered = client.get(
            "/search",
            params={"q": "CrSb", "space_group": "P6_3/mmc", "id": "attacker"},
        )
        assert filtered.status_code == 200
        detail_queries = _detail_queries(filtered.text)
        assert detail_queries
        assert all(
            query
            == {
                "id": ["anyt:am-1-0001"],
                "q": ["CrSb"],
                "space_group": ["P6_3/mmc"],
            }
            for query in detail_queries
        )

        response = client.get("/search")
        assert response.status_code == 200
        page_ids = _ids(response.text)
        assert len(page_ids) == 50
        assert page_ids == [f"anyt:am-1-{index:04d}" for index in range(1, 51)]

        all_ids = list(page_ids)
        next_token = _next_token(response.text)
        last_payload: dict[str, object] | None = None
        while next_token is not None:
            page_response = client.post(
                "/_httk/serve/table/page",
                json={"token": next_token, "route": "search", "widget_id": "materials-results"},
            )
            assert page_response.status_code == 200
            last_payload = page_response.json()
            body_ids = _ids(str(last_payload["tbody"]))
            assert 1 <= len(body_ids) <= 50
            all_ids.extend(body_ids)
            next_token = last_payload["next"]

        assert all_ids == [f"anyt:am-1-{index:04d}" for index in range(1, 181)]
        assert len(set(all_ids)) == 180
        assert last_payload is not None
        previous = last_payload["previous"]
        assert isinstance(previous, str)
        backward = client.post(
            "/_httk/serve/table/page",
            json={"token": previous, "route": "search", "widget_id": "materials-results"},
        )
        assert backward.status_code == 200
        assert _ids(backward.json()["tbody"]) == [f"anyt:am-1-{index:04d}" for index in range(101, 151)]


def test_static_publish_has_only_the_first_page_and_no_live_asset(tmp_path: Path, monkeypatch) -> None:
    store_path = _store_with_materials(tmp_path, count=180)
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(store_path))
    output = tmp_path / "public"
    publish(ROOT / "src", output, "https://example.test", config_name="config_dynamic")
    rendered = (output / "search.html").read_text(encoding="utf-8")

    assert _ids(rendered) == [f"anyt:am-1-{index:04d}" for index in range(1, 51)]
    assert 'data-httk-serve-table-next data-token="" disabled' in rendered
    assert "table.js" not in rendered
    assert "Pagination is available on the live site." in rendered


def test_unavailable_store_suppresses_the_table_and_shows_its_notice(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(tmp_path / "not-mounted.duckdb"))
    monkeypatch.setenv("ALTERMAGNETS_DATA_DIR", str(tmp_path / "not-mounted-tables"))
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic", table_token_secret="s" * 32)
    with TestClient(app) as client:
        response = client.get("/search")
    assert response.status_code == 200
    assert "The screening tables are not mounted" in response.text
    assert "data-httk-serve-table" not in response.text


def test_asgi_lifespan_disposes_the_opened_store(tmp_path: Path, monkeypatch) -> None:
    store_path = _store_with_materials(tmp_path, count=3)
    monkeypatch.setenv("ALTERMAGNETS_STORE_PATH", str(store_path))
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic", table_token_secret="s" * 32)
    calls: list[bool] = []

    with TestClient(app) as client:
        assert client.get("/search").status_code == 200
        database = app.state.engine.global_data["materials_database"]
        original_dispose = database.dispose

        def dispose() -> None:
            calls.append(True)
            original_dispose()

        monkeypatch.setattr(database, "dispose", dispose)

    assert calls == [True]


def test_search_migration_removes_the_old_sql_adapter_paths() -> None:
    content = (ROOT / "src" / "content" / "search.md").read_text(encoding="utf-8")
    search_source = (ROOT / "src" / "functions" / "search_materials.py").read_text(encoding="utf-8")
    provider_source = (ROOT / "src" / "functions" / "materials.py").read_text(encoding="utf-8")
    assert "results-function" not in content
    assert "_legacy_materialize" not in search_source
    assert "def execute(" not in search_source
    assert "SELECT " not in provider_source
    assert "OFFSET" not in provider_source
    assert not (ROOT / "src" / "templates" / "search_results.html.j2").exists()
