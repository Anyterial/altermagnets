from pathlib import Path

from starlette.testclient import TestClient

from httk.web import create_asgi_app

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "data" / "tables" / "high_throughput_screening_results_fixed.csv"
DETAIL_ASSET_PATHS = [
    ROOT / "data" / "details" / "amdb-1" / "0" / "00" / "000" / "amdb-1-0001" / "band.svg",
]


def test_home_page_renders_without_cookies() -> None:
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic")

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Anyterial altermagnets" in response.text
    assert response.headers.get("set-cookie") is None


def test_search_page_handles_local_dataset_or_missing_mount() -> None:
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic")

    with TestClient(app) as client:
        response = client.get("/search", params={"q": "CrSb"})
        trailing_zero_response = client.get("/search", params={"q": "0.800"})

    assert response.status_code == 200
    if DATASET_PATH.exists():
        assert "CrSb" in response.text
        assert trailing_zero_response.status_code == 200
        assert "0.800" in trailing_zero_response.text
    else:
        assert "screening tables are not mounted" in response.text.lower()


def test_material_detail_page_handles_local_dataset_or_missing_mount() -> None:
    app = create_asgi_app(ROOT / "src", config_name="config_dynamic")

    with TestClient(app) as client:
        response = client.get("/material", params={"id": "amdb-1-0001"})

    assert response.status_code == 200
    if DATASET_PATH.exists():
        assert "MAGNDATA" in response.text
        assert "index=0.528" in response.text
        if any(path.exists() for path in DETAIL_ASSET_PATHS):
            assert "Calculated figures" in response.text
            assert "Spin-split band structure" in response.text
            assert "data:image/svg+xml;base64," in response.text
    else:
        assert "screening tables are not mounted" in response.text.lower()
