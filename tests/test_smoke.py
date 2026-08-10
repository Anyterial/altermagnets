import asyncio
from pathlib import Path

import httpx
from httk.serve.web import create_asgi_app

ROOT = Path(__file__).resolve().parents[1]
PRIMARY_MATERIAL_ID = "anyt:am-1-0001"
DETAIL_ASSET_PATHS = [
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001" / "band.svg",
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001" / "band.png",
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "anyt:am-1-0001" / "band.svg",
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "anyt:am-1-0001" / "band.png",
    ROOT / "data" / "details" / "am-1" / "0" / "00" / "000" / "anyt:am-1-0001" / "band.svg",
    ROOT / "data" / "details" / "am-1" / "0" / "00" / "000" / "anyt:am-1-0001" / "band.png",
]


def _request(path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
    app = create_asgi_app(ROOT / "src", config_name="config")

    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, params=params)

    return asyncio.run(_call())


def test_home_page_renders_static_placeholders_and_without_cookies() -> None:
    response = _request("/")

    assert response.status_code == 200
    assert "Altermagnets Database" in response.text
    assert 'data-site-stat="total">—' in response.text
    assert "site-stats.mjs" in response.text
    assert 'href="./material?id=anyt:am-1-0001"' in response.text
    assert response.headers.get("set-cookie") is None


def test_highlights_page_renders_curated_static_cards() -> None:
    response = _request("/highlights")

    assert response.status_code == 200
    assert 'href="./material?id=anyt:am-1-0005"' in response.text
    assert 'href="./material?id=anyt:am-1-0101"' in response.text
    assert "current snapshot leader" in response.text


def test_search_page_renders_the_browser_widget() -> None:
    response = _request("/search")

    assert response.status_code == 200
    assert 'data-httk-serve-optimade-table="1"' in response.text
    assert "search-form.js" in response.text


def test_material_detail_page_renders_the_static_widget_shell() -> None:
    response = _request("/material", params={"id": PRIMARY_MATERIAL_ID})

    assert response.status_code == 200
    assert 'data-site-material-detail="1"' in response.text
    assert "site-material-detail.mjs" in response.text
    assert "serve-optimade-table-protocol.mjs" in response.text


def test_search_handles_unexpected_query_payloads_without_crashing() -> None:
    weird_query = "''; DROP TABLE materials; -- \x00 \xff \\u202e"
    params = {
        "q": weird_query,
        "elements": "Mn, O, ');--",
        "classification": "' OR 1=1 --",
        "electronic_type": "metallic');--",
        "magnetic_phase": "AM'); SELECT 1; --",
        "wave_class": "d');--",
        "space_group": "P2_1/c' OR 'x'='x",
        "sort": "screening_rank; DROP TABLE materials;",
    }

    response = _request("/search", params=params)

    assert response.status_code == 200
    assert response.headers.get("set-cookie") is None
    assert "data-httk-serve-optimade-table" in response.text


def test_search_handles_numeric_edge_case_inputs_without_crashing() -> None:
    params = {
        "min_max_ss": "nan",
        "min_avg_ss": "inf",
        "min_fdelta_pct": "-inf",
        "min_bandgap": "1e309",
        "max_bandgap": "-1e309",
        "min_abundance_ppm": "nan",
    }

    response = _request("/search", params=params)

    assert response.status_code == 200
    assert "data-httk-serve-optimade-table" in response.text


def test_material_detail_handles_path_traversal_like_identifier_safely() -> None:
    response = _request("/material", params={"id": "../../etc/passwd"})

    assert response.status_code == 200
    assert 'data-site-material-detail="1"' in response.text
