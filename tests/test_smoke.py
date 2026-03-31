import asyncio
from pathlib import Path

import httpx

from httk.web import create_asgi_app

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "tables" / "high_throughput_screening_results_fixed.csv"
PRIMARY_MATERIAL_ID = "anyt:amdb-1-0001"
DETAIL_ASSET_PATHS = [
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001" / "band.svg",
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001" / "band.png",
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "anyt:amdb-1-0001" / "band.svg",
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "anyt:amdb-1-0001" / "band.png",
]


def _request(path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic")

    async def _call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, params=params)

    return asyncio.run(_call())


def test_home_page_renders_without_cookies() -> None:
    response = _request("/")

    assert response.status_code == 200
    assert "Altermagnets Database" in response.text
    assert response.headers.get("set-cookie") is None


def test_search_page_handles_local_dataset_or_missing_mount() -> None:
    response = _request("/search", params={"q": "CrSb"})
    trailing_zero_response = _request("/search", params={"q": "0.800"})

    assert response.status_code == 200
    if DATASET_PATH.exists():
        assert "CrSb" in response.text
        assert trailing_zero_response.status_code == 200
        assert "0.800" in trailing_zero_response.text
    else:
        assert "screening tables are not mounted" in response.text.lower()


def test_material_detail_page_handles_local_dataset_or_missing_mount() -> None:
    response = _request("/material", params={"id": PRIMARY_MATERIAL_ID})

    assert response.status_code == 200
    if DATASET_PATH.exists():
        assert "MAGNDATA" in response.text
        assert "index=0.528" in response.text
        if any(path.exists() for path in DETAIL_ASSET_PATHS):
            assert "Figures" in response.text
            assert "Spin-split band structure" in response.text
            assert "data:image/svg+xml;base64," in response.text
    else:
        assert "screening tables are not mounted" in response.text.lower()


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
    if DATASET_PATH.exists():
        assert "Results" in response.text or "Search" in response.text
    else:
        assert "screening tables are not mounted" in response.text.lower()


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
    if DATASET_PATH.exists():
        assert "Search" in response.text
    else:
        assert "screening tables are not mounted" in response.text.lower()


def test_material_detail_handles_path_traversal_like_identifier_safely() -> None:
    response = _request("/material", params={"id": "../../etc/passwd"})

    assert response.status_code == 200
    assert "Material id not found." in response.text
