"""Tests for the altermagnets OPTIMADE service.

These drive the OPTIMADE engine in-process through a Starlette ``TestClient`` (no
network ports are bound) and require the httk modules to be importable (skipped
otherwise, e.g. in a checkout without the workspace ``PYTHONPATH``).
"""

import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("httk.serve.optimade")
pytest.importorskip("httk.atomistic")

from httk.serve.optimade import adapter_from_providers, create_asgi_app
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import serve_optimade


@pytest.fixture(scope="module")
def providers() -> list:
    return serve_optimade.build_providers()


@pytest.fixture(scope="module")
def client(providers: list) -> TestClient:
    app = create_asgi_app(adapter_from_providers(providers), baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_dataset_assembly_counts_and_exact_lattice() -> None:
    structures, properties, _relationships, references = serve_optimade.build_dataset()
    assert len(structures) == 180
    assert len(properties) == 180
    assert references  # DOIs were collected across the symmetry tables

    smfeo3 = structures["anyt:am-1-0039"]
    assert smfeo3 is not None
    row0 = smfeo3.cell.basis.to_floats()[0]
    # First lattice row: float-exact from the CONTCAR strings ("5.3982999999999999").
    assert row0[0] == 5.3982999999999999
    assert row0[1] == 0.0
    assert abs(row0[2]) < 1e-15  # the "~3e-16" residual is numerically zero
    assert properties["anyt:am-1-0039"]["_anyt_magnetic_phase"] == "altermagnet"


def test_null_structure_material_serves_null_lattice(providers: list) -> None:
    structure_provider = providers[0]
    records = {record["__id"]: record for record in structure_provider.records("structures")}
    null_materials = [mid for mid, record in records.items() if record["lattice_vectors"] is None]
    assert null_materials  # some screening rows have no CONTCAR
    assert records[null_materials[0]]["lattice_vectors"] is None
    assert records[null_materials[0]]["species"] is None


def test_moments_are_served_for_the_fixture_structure(providers: list) -> None:
    records = {record["__id"]: record for record in providers[0].records("structures")}
    assert records["anyt:am-1-0039"]["_httk_site_moments"] == [
        [0.0, 0.0, -0.0],
        [0.0, 0.0, -0.0],
        [0.0, 0.0, -0.0],
        [0.0, 0.0, -0.0],
        [0.0, 0.0, 4.208],
        [0.0, 0.0, -4.209],
        [0.0, 0.0, 4.209],
        [0.0, 0.0, -4.209],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.001],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.001],
        [0.0, 0.0, 0.007],
        [0.0, 0.0, -0.007],
        [0.0, 0.0, 0.007],
        [0.0, 0.0, -0.007],
        [0.0, 0.0, 0.007],
        [0.0, 0.0, -0.006],
        [0.0, 0.0, 0.007],
        [0.0, 0.0, -0.006],
    ]
    assert "_httk_magnetism" in records["anyt:am-1-0039"]["structure_features"]


def test_info_structures_lists_anyt_and_standard_definitions(client: TestClient) -> None:
    response = client.get("/info/structures")
    assert response.status_code == 200
    blob = json.dumps(response.json())
    # Custom _anyt_ definitions carry anyterial.se $ids; standard ones stay canonical.
    assert "https://anyterial.se/optimade/defs/properties/_anyt_max_spin_splitting" in blob
    assert "https://schemas.optimade.org/defs/v1.2/properties/optimade/structures/nelements" in blob
    properties = response.json()["data"]["properties"]
    assert "_anyt_magnetic_phase" in properties
    assert "_anyt_wave_class" in properties
    assert "_httk_site_moments" in properties


def test_filter_on_magnetic_phase_returns_rows(client: TestClient) -> None:
    response = client.get(
        "/structures",
        params={"filter": '_anyt_magnetic_phase = "altermagnet"', "response_fields": "_anyt_magnetic_phase"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) > 0
    assert all(item["attributes"]["_anyt_magnetic_phase"] == "altermagnet" for item in data)


def test_references_endpoint_and_include(client: TestClient) -> None:
    references = client.get("/references")
    assert references.status_code == 200
    assert len(references.json()["data"]) > 0

    included_response = client.get("/structures", params={"include": "references", "page_limit": "5"})
    assert included_response.status_code == 200
    payload = included_response.json()
    included = payload.get("included", [])
    assert included, "include=references should embed reference resources"
    assert all(obj["type"] == "references" for obj in included)


def test_validate_all_records_passes(providers: list) -> None:
    assert serve_optimade.run_validation(providers) == 0
