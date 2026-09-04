"""In-process coverage for the DSP 2025-1 minimal catalogue and its mount."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("httk.serve.dsp")

from starlette.applications import Starlette
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import serve_combined
from serve import build_dsp_app
from serve.dsp import IANA_MEDIA_TYPE_HTML, OPTIMADE_SPEC_IRI, WEBSITE_STANDARD_IRI

HTTPS_ORIGIN = "https://altermagnets.anyterial.se"
CATALOG_REQUEST = {
    "@context": ["https://w3id.org/dspace/2025/1/context.jsonld"],
    "@type": "CatalogRequestMessage",
}


def _conforms_to(node: dict[str, object]) -> list[str]:
    entries = node.get("dct:conformsTo", [])
    assert isinstance(entries, list)
    return [entry["@id"] for entry in entries]


def test_build_dsp_app_returns_starlette() -> None:
    assert isinstance(build_dsp_app("https://example.test"), Starlette)


def test_dsp_version_discovery_and_catalogue() -> None:
    app = build_dsp_app(HTTPS_ORIGIN)
    with TestClient(app) as client:
        version = client.get("/.well-known/dspace-version")
        catalog = client.post("/2025-1/catalog/request", json=CATALOG_REQUEST)

    assert version.status_code == 200
    assert any(item["version"] == "2025-1" for item in version.json()["protocolVersions"])

    assert catalog.status_code == 200
    body = catalog.json()
    dataset = body["dataset"][0]
    assert dataset["@type"] == "Dataset"
    distribution = dataset["distribution"][0]
    assert distribution["dcat:accessURL"]["@id"] == f"{HTTPS_ORIGIN}/"
    assert distribution["dcat:mediaType"]["@id"] == IANA_MEDIA_TYPE_HTML
    assert IANA_MEDIA_TYPE_HTML.endswith("text/html")

    services = body["service"]
    optimade = next(s for s in services if s["endpointURL"] == f"{HTTPS_ORIGIN}/optimade/amdb")
    assert OPTIMADE_SPEC_IRI in _conforms_to(optimade)
    website = next(s for s in services if WEBSITE_STANDARD_IRI in _conforms_to(s))
    assert website["endpointURL"] == f"{HTTPS_ORIGIN}/"


def test_combined_https_mounts_dsp() -> None:
    app = serve_combined.create_combined_app(public_base_url=HTTPS_ORIGIN)
    with TestClient(app) as client:
        version = client.get("/dsp/.well-known/dspace-version")
        catalog = client.post("/dsp/2025-1/catalog/request", json=CATALOG_REQUEST)

    assert version.status_code == 200
    assert any(item["version"] == "2025-1" for item in version.json()["protocolVersions"])
    assert catalog.status_code == 200
    assert catalog.json()["dataset"][0]["distribution"][0]["dcat:accessURL"]["@id"] == f"{HTTPS_ORIGIN}/"


def test_combined_http_default_does_not_mount_dsp() -> None:
    app = serve_combined.create_combined_app()
    with TestClient(app, base_url="http://testserver") as client:
        dsp = client.get("/dsp/.well-known/dspace-version")
        home = client.get("/")
        amdb_info = client.get("/optimade/amdb/v1/info")

    assert dsp.status_code == 404
    assert home.status_code == 200
    assert amdb_info.status_code == 200
