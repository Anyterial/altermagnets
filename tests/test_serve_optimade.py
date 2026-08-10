"""Tests for the altermagnets OPTIMADE service.

These drive the OPTIMADE engine in-process through a Starlette ``TestClient`` (no
network ports are bound) and require the httk modules to be importable (skipped
otherwise, e.g. in a checkout without the workspace ``PYTHONPATH``).
"""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

import httpx
import pytest

pytest.importorskip("httk.serve.optimade")
pytest.importorskip("httk.atomistic")

from httk.serve.optimade import adapter_from_providers, create_asgi_app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import material_store
import serve_optimade
from optimade_service import build_service_app
from search_materials import search_materials

EXPECTED_DEFINITION_PROVENANCE = {
    "_anyterial_formula": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/formula",
        "formula",
    ),
    "_anyterial_elements": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/elements_present",
        "elements_present",
    ),
    "_anyterial_space_group": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/space_group",
        "space_group",
    ),
    "_anyterial_space_group_search": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/space_group_search",
        "space_group_search",
    ),
    "_anyterial_classification": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/classification",
        "classification",
    ),
    "_anyterial_magnetic_phases": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/magnetic_phases",
        "magnetic_phases",
    ),
    "_anyterial_wave_classes": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/wave_classes",
        "wave_classes",
    ),
    "_anyterial_parent_spacegroups": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/parent_spacegroups",
        "parent_spacegroups",
    ),
    "_anyterial_icsd_ids": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/icsd_ids",
        "icsd_ids",
    ),
    "_anyterial_search_text": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/search_text",
        "search_text",
    ),
    "_anyterial_magndata_variants": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/magndata_variants",
        "magndata_variants",
    ),
    "_anyterial_figures": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/figures",
        "figures",
    ),
    "_anyterial_avg_spin_splitting": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/avg_spin_splitting",
        "avg_spin_splitting",
    ),
    "_anyterial_max_spin_splitting": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/max_spin_splitting",
        "max_spin_splitting",
    ),
    "_anyterial_spin_splitting_fraction": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/spin_splitting_fraction",
        "spin_splitting_fraction",
    ),
    "_anyterial_magnetic_phase": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/magnetic_phase",
        "magnetic_phase",
    ),
    "_anyterial_wave_class": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/wave_class",
        "wave_class",
    ),
    "_anyterial_electronic_type": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/electronic_type",
        "electronic_type",
    ),
    "_anyterial_min_crustal_abundance": (
        "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/min_crustal_abundance",
        "min_crustal_abundance",
    ),
    "_httk_dft_band_gap": (
        "https://schemas.httk.org/defs/v0.1/properties/electronic/dft_band_gap",
        "dft_band_gap",
    ),
    "_httk_magnetic_space_group_bns": (
        "https://schemas.httk.org/defs/v0.1/properties/magnetism/magnetic_space_group_bns",
        "magnetic_space_group_bns",
    ),
    "_httk_magndata_ids": (
        "https://schemas.httk.org/defs/v0.1/properties/magnetism/magndata_ids",
        "magndata_ids",
    ),
}


@pytest.fixture(scope="module")
def providers() -> list:
    return serve_optimade.build_providers(public_base_url="https://plots.example.test/api/")


@pytest.fixture(scope="module")
def client(providers: list) -> "ApiClient":
    app = create_asgi_app(
        adapter_from_providers(providers, sortable=serve_optimade.SORTABLE_PROPERTIES),
        baseurl="http://testserver/",
    )
    return ApiClient(app)


class ApiClient:
    """Small synchronous wrapper around httpx's working in-process ASGI transport."""

    def __init__(self, app) -> None:
        self.app = app

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, params=params, headers=headers)

        return asyncio.run(request())

    def get(self, path: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        return self.request("GET", path, params=params)


def _figure_dataset(details_root: Path) -> tuple[dict[str, Any], str, Path, Path]:
    material_id = "anyt:am-1-0001"
    details_dir = details_root / "am-1" / "0" / "00" / "001" / material_id
    details_dir.mkdir(parents=True)
    svg_path = details_dir / "plot.svg"
    png_path = details_dir / "plot.png"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="#000" /></svg>'
    png = b"\x89PNG\r\nfigure"
    svg_path.write_bytes(svg)
    png_path.write_bytes(png)
    (details_dir / "CONTCAR").write_bytes(b"not-a-figure")
    figure = material_store.MaterialFigure(
        "plot",
        material_store.PlotFile(
            url=f"am-1/0/00/001/{material_id}/plot.svg",
            name="plot.svg",
            size=len(svg),
            media_type="image/svg+xml",
        ),
        None,
    )
    png_figure = material_store.MaterialFigure(
        "png",
        material_store.PlotFile(
            url=f"am-1/0/00/001/{material_id}/plot.png",
            name="plot.png",
            size=len(png),
            media_type="image/png",
        ),
        None,
    )
    return {material_id: SimpleNamespace(id=material_id, figures=(figure, png_figure))}, material_id, svg_path, png_path


def test_service_figure_route_whitelist_headers_and_dark_cache(providers: list, tmp_path: Path) -> None:
    dataset, material_id, svg_path, _ = _figure_dataset(tmp_path)
    app = build_service_app(
        public_base_url="http://testserver",
        providers=providers,
        dataset=dataset,
        details_root=tmp_path,
    )
    client = ApiClient(app)

    light = client.get(f"/figures/{material_id}/plot.svg")
    png = client.get(f"/figures/{material_id}/plot.png")
    dark = client.get(f"/figures/{material_id}/dark--plot.svg")
    svg_path.unlink()
    cached_dark = client.get(f"/figures/{material_id}/dark--plot.svg")

    assert light.status_code == 200
    assert light.content.startswith(b"<svg")
    assert light.headers["content-type"] == "image/svg+xml"
    assert light.headers["cache-control"] == "public, max-age=3600"
    assert light.headers["x-content-type-options"] == "nosniff"
    assert light.headers["access-control-allow-origin"] == "*"
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert dark.status_code == 200 and b"#f2f5fb" in dark.content
    assert cached_dark.status_code == 200 and cached_dark.content == dark.content
    assert client.get(f"/figures/{material_id}/CONTCAR").status_code == 404
    assert client.get("/figures/not-a-material/plot.svg").status_code == 404
    assert client.get(f"/figures/{material_id}/../CONTCAR").status_code == 404
    assert client.get(f"/figures/{material_id}/%2e%2e/CONTCAR").status_code == 404


def test_service_figure_route_missing_file_and_recorded_size_cap(providers: list, tmp_path: Path) -> None:
    dataset, material_id, _svg_path, png_path = _figure_dataset(tmp_path)
    record = dataset[material_id]
    capped_file = replace(record.figures[1].light, size=1)
    record.figures = (record.figures[0], replace(record.figures[1], light=capped_file))
    app = build_service_app(
        public_base_url="http://testserver",
        providers=providers,
        dataset=dataset,
        details_root=tmp_path,
    )
    client = ApiClient(app)
    assert client.get(f"/figures/{material_id}/plot.png").status_code == 404
    png_path.unlink()
    assert client.get(f"/figures/{material_id}/plot.png").status_code == 404


def test_service_cors_is_configured_only_for_optimade(providers: list, tmp_path: Path) -> None:
    dataset, material_id, _svg_path, _png_path = _figure_dataset(tmp_path)
    app = build_service_app(
        public_base_url="http://testserver",
        cors_origins=("https://static.example",),
        providers=providers,
        dataset=dataset,
        details_root=tmp_path,
    )
    client = ApiClient(app)
    allowed = client.request(
        "OPTIONS",
        "/v1/info",
        headers={
            "Origin": "https://static.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    allowed_get = client.request("GET", "/v1/info", headers={"Origin": "https://static.example"})
    denied_get = client.request("GET", "/v1/info", headers={"Origin": "https://other.example"})
    figure = client.request("GET", f"/figures/{material_id}/plot.png", headers={"Origin": "https://other.example"})

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://static.example"
    assert allowed_get.headers["access-control-allow-origin"] == "https://static.example"
    assert "access-control-allow-origin" not in denied_get.headers
    assert figure.headers["access-control-allow-origin"] == "*"


def test_standalone_service_api_figure_url_resolves_through_same_app() -> None:
    records: dict[str, Any] = {}
    standalone_providers = serve_optimade.build_providers(
        public_base_url="https://plots.example.test",
        material_records=records,
    )
    app = build_service_app(
        public_base_url="https://plots.example.test",
        providers=standalone_providers,
        dataset=records,
    )
    client = ApiClient(app)
    response = client.get(
        "/structures",
        params={"filter": 'id = "anyt:am-1-0001"', "response_fields": "_anyterial_figures"},
    )
    assert response.status_code == 200
    figures = response.json()["data"][0]["attributes"]["_anyterial_figures"]
    served = [client.get(urlsplit(figure["url"]).path) for figure in figures if figure["available"]]
    assert any(response.status_code == 200 for response in served)


def test_dataset_assembly_counts_and_exact_lattice() -> None:
    structures, properties, _relationships, references = serve_optimade.build_dataset()
    assert len(structures) == 180
    assert len(properties) == 180
    assert references  # DOIs were collected across the symmetry tables
    for property_values in properties.values():
        assert set(property_values) == set(EXPECTED_DEFINITION_PROVENANCE)

    smfeo3 = structures["anyt:am-1-0039"]
    assert smfeo3 is not None
    row0 = smfeo3.cell.basis.to_floats()[0]
    # First lattice row: float-exact from the CONTCAR strings ("5.3982999999999999").
    assert row0[0] == 5.3982999999999999
    assert row0[1] == 0.0
    assert abs(row0[2]) < 1e-15  # the "~3e-16" residual is numerically zero
    assert properties["anyt:am-1-0039"]["_anyterial_magnetic_phase"] == "altermagnet"


def test_live_definition_contract() -> None:
    definitions = serve_optimade.load_schema_definitions()
    assert set(definitions) == set(EXPECTED_DEFINITION_PROVENANCE)
    for served_name, (expected_id, expected_name) in EXPECTED_DEFINITION_PROVENANCE.items():
        document = definitions[served_name].as_optimade()
        assert document["$id"] == expected_id
        assert document["x-optimade-definition"]["name"] == expected_name


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


def test_info_structures_lists_custom_and_standard_definitions(client: ApiClient) -> None:
    response = client.get("/info/structures")
    assert response.status_code == 200
    blob = json.dumps(response.json())
    # Published custom definitions retain their authoritative $ids; standard ones stay canonical.
    assert "https://schemas.anyterial.se/defs/v0.1/properties/altermagnets/max_spin_splitting" in blob
    assert "https://schemas.optimade.org/defs/v1.2/properties/optimade/structures/nelements" in blob
    properties = response.json()["data"]["properties"]
    expected_custom_properties = {
        "_anyterial_classification",
        "_anyterial_elements",
        "_anyterial_figures",
        "_anyterial_formula",
        "_anyterial_icsd_ids",
        "_anyterial_magndata_variants",
        "_anyterial_avg_spin_splitting",
        "_anyterial_electronic_type",
        "_anyterial_magnetic_phase",
        "_anyterial_magnetic_phases",
        "_anyterial_max_spin_splitting",
        "_anyterial_min_crustal_abundance",
        "_anyterial_parent_spacegroups",
        "_anyterial_search_text",
        "_anyterial_space_group",
        "_anyterial_space_group_search",
        "_anyterial_spin_splitting_fraction",
        "_anyterial_wave_class",
        "_anyterial_wave_classes",
    }
    assert {name for name in properties if name.startswith("_anyterial_")} == expected_custom_properties
    assert "_httk_dft_band_gap" in properties
    assert "_httk_magnetic_space_group_bns" in properties
    assert "_httk_magndata_ids" in properties
    assert "_anyterial_magnetic_phase" in properties
    assert "_anyterial_wave_class" in properties
    assert "_httk_site_moments" in properties


def test_filter_on_magnetic_phase_returns_rows(client: ApiClient) -> None:
    response = client.get(
        "/structures",
        params={"filter": '_anyterial_magnetic_phase = "altermagnet"', "response_fields": "_anyterial_magnetic_phase"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert len(data) > 0
    assert all(item["attributes"]["_anyterial_magnetic_phase"] == "altermagnet" for item in data)


def test_references_endpoint_and_include(client: ApiClient) -> None:
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


def test_detail_properties_and_absolute_figures(client: ApiClient) -> None:
    fields = [
        "_anyterial_formula",
        "_anyterial_elements",
        "_anyterial_space_group",
        "_anyterial_space_group_search",
        "_anyterial_classification",
        "_anyterial_magnetic_phases",
        "_anyterial_wave_classes",
        "_anyterial_parent_spacegroups",
        "_anyterial_icsd_ids",
        "_anyterial_search_text",
        "_anyterial_magndata_variants",
        "_anyterial_figures",
    ]
    response = client.get(
        "/structures",
        params={
            "filter": 'id = "anyt:am-1-0001"',
            "response_fields": ",".join(fields),
        },
    )
    assert response.status_code == 200
    attributes = response.json()["data"][0]["attributes"]
    assert attributes["_anyterial_formula"] == "CrSb"
    assert attributes["_anyterial_elements"] == ["Cr", "Sb"]
    assert attributes["_anyterial_space_group_search"] == attributes["_anyterial_space_group"].lower()
    assert attributes["_anyterial_classification"] == "collinear"
    assert attributes["_anyterial_magndata_variants"]
    assert attributes["_anyterial_magndata_variants"][0]["source"] == "collinear"
    assert attributes["_anyterial_figures"][0] == {
        "key": "band",
        "url": "https://plots.example.test/api/figures/anyt:am-1-0001/band.svg",
        "dark_url": "https://plots.example.test/api/figures/anyt:am-1-0001/dark--band.svg",
        "media_type": "image/svg+xml",
        "available": True,
    }


def test_non_default_properties_are_omitted_unless_requested(client: ApiClient) -> None:
    response = client.get("/structures", params={"filter": 'id = "anyt:am-1-0001"'})
    assert response.status_code == 200
    attributes = response.json()["data"][0]["attributes"]
    assert all(
        name not in attributes
        for name in (
            "_anyterial_magndata_variants",
            "_anyterial_figures",
            "_anyterial_search_text",
            "_anyterial_space_group_search",
        )
    )


@pytest.mark.parametrize(
    "property_name",
    (
        "id",
        "_anyterial_max_spin_splitting",
        "_anyterial_avg_spin_splitting",
        "_anyterial_spin_splitting_fraction",
        "_httk_dft_band_gap",
        "_anyterial_min_crustal_abundance",
    ),
)
def test_sortable_properties_put_nulls_last_in_both_directions(client: ApiClient, property_name: str) -> None:
    for sort in (property_name, f"-{property_name}"):
        response = client.get("/structures", params={"sort": sort, "response_fields": property_name})
        assert response.status_code == 200
        values = [
            item["id"] if property_name == "id" else item["attributes"].get(property_name)
            for item in response.json()["data"]
        ]
        next_url = response.json()["links"].get("next")
        while next_url:
            page = client.get(next_url)
            assert page.status_code == 200
            values.extend(
                item["id"] if property_name == "id" else item["attributes"].get(property_name)
                for item in page.json()["data"]
            )
            next_url = page.json()["links"].get("next")
        non_null = [value for value in values if value is not None]
        assert values == non_null + [None] * (len(values) - len(non_null))
        if len(non_null) > 1:
            assert non_null == sorted(non_null, reverse=sort.startswith("-"))


def test_sort_continuation_preserves_order(client: ApiClient) -> None:
    response = client.get(
        "/structures",
        params={"sort": "_httk_dft_band_gap", "page_limit": "2", "response_fields": "_httk_dft_band_gap"},
    )
    assert response.status_code == 200
    first = response.json()
    assert first["links"].get("next")
    next_url = first["links"]["next"]
    next_response = client.get(next_url)
    assert next_response.status_code == 200
    first_values = [item["attributes"].get("_httk_dft_band_gap") for item in first["data"]]
    next_values = [item["attributes"].get("_httk_dft_band_gap") for item in next_response.json()["data"]]
    assert first_values[-1] <= next_values[0]


def test_structureless_and_unresolved_variant_projection(providers: list) -> None:
    records = {record["__id"]: record for record in providers[0].records("structures")}
    structureless = next(record for record in records.values() if record["lattice_vectors"] is None)
    assert structureless["_anyterial_formula"]
    assert structureless["_anyterial_elements"]

    structures, properties, _relationships, _references = serve_optimade.build_dataset()
    assert structures[structureless["__id"]] is None
    assert properties[structureless["__id"]]["_anyterial_formula"]
    assert properties[structureless["__id"]]["_anyterial_elements"]

    # The store graph has no unresolved links in the production fixture. Exercise
    # the same placeholder projection on a copied record to keep this contract explicit.
    opened = material_store.open_in_memory_store()
    assert opened is not None
    try:
        searcher = opened.store.searcher()
        material_variable = searcher.variable(material_store.MaterialRecord)
        material = searcher.results(material=material_variable).first()["material"]
        unresolved = replace(
            material,
            links=tuple(replace(link, record=replace(link.record, variants=())) for link in material.links),
        )
        formula = material.formula
        projected = serve_optimade._material_properties(unresolved, "https://plots.example.test/api")
    finally:
        material_store.cleanup_material_store({"materials_database": opened.database})
    assert projected["_anyterial_magndata_variants"] == []
    assert projected["_anyterial_formula"] == formula


# TEMPORARY: removed with the legacy search path in Phase 3
def test_legacy_search_parity_mapping(client: ApiClient) -> None:
    mappings = (
        ("q", {"q": "CrSb"}, '_anyterial_search_text CONTAINS "crsb"'),
        ("elements", {"elements": "Cr"}, '_anyterial_elements HAS "Cr"'),
        ("classification", {"classification": "collinear"}, '_anyterial_classification = "collinear"'),
        ("phase", {"magnetic_phase": "AM"}, '_anyterial_magnetic_phases HAS "AM"'),
        ("wave", {"wave_class": "d"}, '_anyterial_wave_classes HAS "d"'),
        ("space group", {"space_group": "P6_3"}, '_anyterial_space_group_search CONTAINS "p6_3"'),
        ("numeric", {"min_max_ss": "1"}, "_anyterial_max_spin_splitting >= 1"),
    )
    sort_modes = (
        ("screening_rank", "id"),
        ("max_ss_desc", "-_anyterial_max_spin_splitting,id"),
        ("avg_ss_desc", "-_anyterial_avg_spin_splitting,id"),
        ("bandgap_desc", "-_httk_dft_band_gap,id"),
        (
            "abundance_desc",
            "-_anyterial_min_crustal_abundance,-_anyterial_max_spin_splitting,id",
        ),
    )
    opened = material_store.open_material_store()
    assert opened is not None
    try:
        for _label, legacy_params, optimade_filter in mappings:
            for legacy_sort, optimade_sort in sort_modes:
                # The OPTIMADE mapping is the Phase-3 client contract:
                # normalized q/search text and normalized space groups use CONTAINS;
                # elements/phases/waves use HAS; FdeltaPct is divided by 100; numeric
                # minima use >= over the corresponding served fraction/property.
                legacy_results, _orders = search_materials(opened.store, **{**legacy_params, "sort": legacy_sort})
                legacy_ids = {row["material"].id for row in legacy_results}
                response = client.get(
                    "/structures",
                    params={
                        "filter": optimade_filter,
                        "sort": optimade_sort,
                        "response_fields": "id",
                        "page_limit": "50",
                    },
                )
                assert response.status_code == 200
                page = response.json()
                optimade_ids = {item["id"] for item in page["data"]}
                next_url = page["links"].get("next")
                while next_url:
                    page_response = client.get(next_url)
                    assert page_response.status_code == 200
                    page = page_response.json()
                    optimade_ids.update(item["id"] for item in page["data"])
                    next_url = page["links"].get("next")
                assert optimade_ids == legacy_ids
    finally:
        material_store.cleanup_material_store({"materials_database": opened.database})


# TEMPORARY: removed with the legacy search path in Phase 3
def test_unfiltered_sort_order_parity(client: ApiClient) -> None:
    sort_modes = (
        ("screening_rank", "id"),
        ("max_ss_desc", "-_anyterial_max_spin_splitting,id"),
        ("avg_ss_desc", "-_anyterial_avg_spin_splitting,id"),
        ("bandgap_desc", "-_httk_dft_band_gap,id"),
        (
            "abundance_desc",
            "-_anyterial_min_crustal_abundance,-_anyterial_max_spin_splitting,id",
        ),
    )
    opened = material_store.open_material_store()
    assert opened is not None
    try:
        for legacy_sort, optimade_sort in sort_modes:
            legacy_results, legacy_order = search_materials(opened.store, sort=legacy_sort)
            legacy_ids = [row["material"].id for row in legacy_results.page(size=10_000, order_by=legacy_order).rows]
            response = client.get(
                "/structures",
                params={"sort": optimade_sort, "response_fields": "id", "page_limit": "50"},
            )
            assert response.status_code == 200
            page = response.json()
            optimade_ids = [item["id"] for item in page["data"]]
            next_url = page["links"].get("next")
            while next_url:
                page_response = client.get(next_url)
                assert page_response.status_code == 200
                page = page_response.json()
                optimade_ids.extend(item["id"] for item in page["data"])
                next_url = page["links"].get("next")
            assert optimade_ids == legacy_ids, legacy_sort
    finally:
        material_store.cleanup_material_store({"materials_database": opened.database})
