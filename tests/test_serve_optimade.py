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

from httk.core import EntryTypeDefinition, PropertyDefinition
from httk.serve.optimade import adapter_from_providers, create_asgi_app

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import material_store
from conftest import write_detail_assets, write_source_tables
from optimade import adapter, build_providers, build_service_app, run_validation, service
from optimade import dataset as dataset_module
from starlette.testclient import TestClient

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

LOCAL_DEFINITION_NAMES = {"_httk_custom_figures"}
EXPECTED_PROPERTY_NAMES = set(EXPECTED_DEFINITION_PROVENANCE) | LOCAL_DEFINITION_NAMES


@pytest.fixture(scope="module")
def providers() -> list:
    return build_providers(public_base_url="https://plots.example.test/api/")


@pytest.fixture(scope="module")
def client(providers: list) -> "ApiClient":
    app = create_asgi_app(
        adapter_from_providers(providers, sortable=adapter.SORTABLE_PROPERTIES),
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


def test_store_native_service_is_live_and_does_not_own_caller_store(tmp_path: Path) -> None:
    source = write_source_tables(tmp_path / "tables")
    opened = material_store.open_in_memory_store(source, details_dir=tmp_path / "details")
    assert opened is not None
    app = build_service_app(
        public_base_url="https://api.example.test/optimade/amdb",
        store=opened.store,
        details_root=tmp_path / "details",
    )
    assert app.state.entry_store is opened.store
    assert app.state.owns_entry_store is False

    with TestClient(app, base_url="http://testserver") as live:
        info = live.get("/v1/info/structures")
        assert info.status_code == 200
        assert "_httk_custom_public_id" not in info.json()["data"]["properties"]
        assert "_httk_custom_reference_ids" not in info.json()["data"]["properties"]
        first = live.get("/v1/structures", params={"sort": "id", "response_fields": "id"})
        assert first.status_code == 200
        assert [item["id"] for item in first.json()["data"]] == [
            "anyt:am-1-0001",
            "anyt:am-1-0002",
            "anyt:am-1-0003",
        ]

        searcher = opened.store.searcher()
        material = searcher.variable(material_store.MaterialRecord)
        record = searcher.results(material=material).first()["material"]
        opened.store.save(replace(record, id="anyt:am-1-9999", screening_rank=9999))

        later = live.get("/v1/structures", params={"sort": "id", "response_fields": "id"})
        assert later.status_code == 200
        assert later.json()["meta"]["data_returned"] == 4
        assert later.json()["data"][-1]["id"] == "anyt:am-1-9999"
        included = live.get("/v1/structures/anyt:am-1-0001", params={"include": "references"})
        assert included.status_code == 200
        assert included.json()["included"]

    # The service did not close a store supplied by its caller.
    assert opened.store.searcher().variable(material_store.MaterialRecord) is not None
    opened.database.dispose()


# The 9 direct MaterialRecord projections the site's search table requests; none is
# nested under the structure sub-record, so serving them must never SELECT it.
SEARCH_TABLE_COLUMNS = (
    "_anyterial_formula",
    "_httk_magndata_ids",
    "_anyterial_classification",
    "_anyterial_space_group",
    "_anyterial_max_spin_splitting",
    "_anyterial_avg_spin_splitting",
    "_anyterial_spin_splitting_fraction",
    "_httk_dft_band_gap",
    "_anyterial_min_crustal_abundance",
)


def _structure_table_names() -> set[str]:
    """Resolve the structure record's table and its child tables without hardcoding."""
    from httk.store.backend.sql.schema import resolve_schema

    structure_schema = resolve_schema(resolve_schema(material_store.MaterialRecord).field("structure").target)
    tables = {structure_schema.table_name}
    tables.update(
        field.child.table_name for field in structure_schema.fields if field.role == "child" and field.child is not None
    )
    return tables


def test_search_table_columns_skip_structure_hydration(tmp_path: Path) -> None:
    import sqlalchemy

    structure_tables = _structure_table_names()
    source = write_source_tables(tmp_path / "tables")
    details = write_detail_assets(tmp_path / "details")
    opened = material_store.open_in_memory_store(source, details_dir=details)
    assert opened is not None
    app = build_service_app(
        public_base_url="https://api.example.test/optimade/amdb",
        store=opened.store,
        details_root=details,
    )

    def collector(statements: list[str]):
        def record(conn, cursor, statement, parameters, context, executemany):  # type: ignore[no-untyped-def]
            statements.append(statement)

        return record

    # Positive control: a full request (no response_fields) must SELECT the structure
    # table, proving the fixture actually hydrates structures when a request reads them.
    full_statements: list[str] = []
    full_record = collector(full_statements)
    sqlalchemy.event.listen(opened.database.engine, "before_cursor_execute", full_record)
    try:
        with TestClient(app, base_url="http://testserver") as live:
            full = live.get("/v1/structures", params={"page_limit": "180"})
    finally:
        sqlalchemy.event.remove(opened.database.engine, "before_cursor_execute", full_record)
    assert full.status_code == 200
    full_data = full.json()["data"]
    assert any(item["attributes"].get("lattice_vectors") is not None for item in full_data)
    assert any(table in statement for table in structure_tables for statement in full_statements)

    statements: list[str] = []
    pruned_record = collector(statements)
    sqlalchemy.event.listen(opened.database.engine, "before_cursor_execute", pruned_record)
    try:
        with TestClient(app, base_url="http://testserver") as live:
            response = live.get(
                "/v1/structures",
                params={"response_fields": ",".join(SEARCH_TABLE_COLUMNS), "page_limit": "180"},
            )
    finally:
        sqlalchemy.event.remove(opened.database.engine, "before_cursor_execute", pruned_record)

    assert response.status_code == 200
    payload = response.json()
    data = payload["data"]
    assert data
    for item in data:
        assert all(column in item["attributes"] for column in SEARCH_TABLE_COLUMNS)
    # am-1-0001 is the fixture row with every search-table property populated.
    first = next(item for item in data if item["id"] == "anyt:am-1-0001")["attributes"]
    assert first["_anyterial_formula"] == "CrSb"
    assert first["_anyterial_classification"]
    assert first["_anyterial_max_spin_splitting"] is not None
    assert first["_httk_dft_band_gap"] is not None
    # The search table never reads the nested structure, so no SQL touches its tables.
    assert statements
    assert not any(table in statement for table in structure_tables for statement in statements)
    # Phase 2: the adapter always attaches references relationships and include was
    # defaulted on this multi-row page, so the reply must not embed an included section.
    assert len(data) > 1
    assert "included" not in payload

    opened.database.dispose()


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

    light = client.get(f"/extensions/figures/{material_id}/plot.svg")
    png = client.get(f"/extensions/figures/{material_id}/plot.png")
    dark = client.get(f"/extensions/figures/{material_id}/dark--plot.svg")
    assert client.get(f"/figures/{material_id}/plot.svg").status_code == 404
    svg_path.unlink()
    cached_dark = client.get(f"/extensions/figures/{material_id}/dark--plot.svg")

    assert light.status_code == 200
    assert light.content.startswith(b"<svg")
    assert light.headers["content-type"] == "image/svg+xml"
    assert light.headers["cache-control"] == "public, max-age=3600"
    assert light.headers["x-content-type-options"] == "nosniff"
    assert light.headers["access-control-allow-origin"] == "*"
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG")
    assert dark.status_code == 200 and b"#f2f5fb" in dark.content
    assert cached_dark.status_code == 200 and cached_dark.content == dark.content
    assert client.get(f"/extensions/figures/{material_id}/CONTCAR").status_code == 404
    assert client.get("/extensions/figures/not-a-material/plot.svg").status_code == 404
    assert client.get(f"/extensions/figures/{material_id}/../CONTCAR").status_code == 404
    assert client.get(f"/extensions/figures/{material_id}/%2e%2e/CONTCAR").status_code == 404


def test_service_dark_cache_respects_byte_budget(
    providers: list, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset, material_id, svg_path, _ = _figure_dataset(tmp_path)
    monkeypatch.setattr(service, "DARK_CACHE_MAX_BYTES", 1)
    app = build_service_app(
        public_base_url="http://testserver",
        providers=providers,
        dataset=dataset,
        details_root=tmp_path,
    )
    client = ApiClient(app)

    assert client.get(f"/extensions/figures/{material_id}/dark--plot.svg").status_code == 200
    svg_path.unlink()
    assert client.get(f"/extensions/figures/{material_id}/dark--plot.svg").status_code == 404


def test_size_none_is_unavailable_in_projection_and_route(providers: list, tmp_path: Path) -> None:
    dataset, material_id, _svg_path, _ = _figure_dataset(tmp_path)
    record = dataset[material_id]
    record.figures = (
        replace(record.figures[0], light=replace(record.figures[0].light, size=None)),
        *record.figures[1:],
    )
    projected = dataset_module._figure_payload(record, "http://testserver")
    assert projected[0] == {
        "key": "band",
        "url": None,
        "dark_url": None,
        "media_type": None,
        "available": False,
    }
    app = build_service_app(
        public_base_url="http://testserver",
        providers=providers,
        dataset=dataset,
        details_root=tmp_path,
    )
    assert ApiClient(app).get(f"/extensions/figures/{material_id}/plot.svg").status_code == 404


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
    assert client.get(f"/extensions/figures/{material_id}/plot.png").status_code == 404
    png_path.unlink()
    assert client.get(f"/extensions/figures/{material_id}/plot.png").status_code == 404


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
    figure = client.request(
        "GET", f"/extensions/figures/{material_id}/plot.png", headers={"Origin": "https://other.example"}
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://static.example"
    assert allowed_get.headers["access-control-allow-origin"] == "https://static.example"
    assert "access-control-allow-origin" not in denied_get.headers
    assert figure.headers["access-control-allow-origin"] == "*"


def test_service_info_exposes_license_configuration(providers: list, tmp_path: Path) -> None:
    app = build_service_app(
        public_base_url="http://testserver",
        providers=providers,
        dataset={},
        details_root=tmp_path,
    )
    attributes = ApiClient(app).get("/v1/info").json()["data"]["attributes"]

    assert attributes["license"] == "https://altermagnets.anyterial.se/about#legal"
    assert attributes["available_licenses"] == []
    assert attributes["available_licenses_for_entries"] == ["CC-BY-4.0"]


def test_standalone_service_links_advertise_one_root_self_link(providers: list, tmp_path: Path) -> None:
    app = build_service_app(
        public_base_url="https://amdb.example.test/optimade/amdb",
        providers=providers,
        dataset={},
        details_root=tmp_path,
    )
    links = ApiClient(app).get("/v1/links").json()["data"]
    configured = [item for item in links if item["id"] != "optimade"]

    assert len(configured) == 1
    assert configured[0]["id"] == "amdb"
    assert configured[0]["attributes"]["name"] == "Anyterial Altermagnets Database"
    assert configured[0]["attributes"]["description"] == (
        "A database of materials computationally predicted to exhibit altermagnetism."
    )
    assert configured[0]["attributes"]["base_url"] == "https://amdb.example.test/optimade/amdb"
    assert configured[0]["attributes"]["homepage"] == "https://altermagnets.anyterial.se"
    assert configured[0]["attributes"]["link_type"] == "root"


def test_standalone_service_api_figure_url_resolves_through_same_app() -> None:
    records: dict[str, Any] = {}
    standalone_providers = build_providers(
        public_base_url="https://plots.example.test",
        material_records=records,
    )
    app = build_service_app(
        public_base_url="https://plots.example.test",
        providers=standalone_providers,
        dataset=records,
    )
    client = ApiClient(app)
    all_data: list[dict[str, Any]] = []
    next_url: str | None = "/structures"
    params = {"response_fields": "_httk_custom_figures", "page_limit": "200"}
    while next_url is not None:
        response = client.get(next_url, params=params)
        assert response.status_code == 200
        all_data.extend(response.json()["data"])
        next_url = response.json()["links"].get("next")
        params = None

    assert len(all_data) == 180
    for item in all_data:
        for figure in item["attributes"]["_httk_custom_figures"]:
            if not figure["available"]:
                assert figure["url"] is None and figure["dark_url"] is None
                continue
            assert client.get(urlsplit(figure["url"]).path).status_code == 200
            if figure["dark_url"] is not None:
                assert client.get(urlsplit(figure["dark_url"]).path).status_code == 200


def test_dataset_assembly_counts_and_exact_lattice() -> None:
    structures, properties, _relationships, references = dataset_module.build_dataset()
    assert len(structures) == 180
    assert len(properties) == 180
    assert references  # DOIs were collected across the symmetry tables
    for property_values in properties.values():
        assert set(property_values) == EXPECTED_PROPERTY_NAMES

    smfeo3 = structures["anyt:am-1-0039"]
    assert smfeo3 is not None
    row0 = smfeo3.cell.basis.to_floats()[0]
    # First lattice row: float-exact from the CONTCAR strings ("5.3982999999999999").
    assert row0[0] == 5.3982999999999999
    assert row0[1] == 0.0
    assert abs(row0[2]) < 1e-15  # the "~3e-16" residual is numerically zero
    assert properties["anyt:am-1-0039"]["_anyterial_magnetic_phase"] == "altermagnet"


def test_live_definition_contract() -> None:
    definitions = dataset_module.load_schema_definitions()
    assert set(definitions) == EXPECTED_PROPERTY_NAMES
    for served_name, (expected_id, expected_name) in EXPECTED_DEFINITION_PROVENANCE.items():
        document = definitions[served_name].as_optimade()
        assert document["$id"] == expected_id
        assert document["x-optimade-definition"]["name"] == expected_name

    assert "_anyterial_figures" not in dataset_module.ANYTERIAL_DEFINITION_PATHS
    figures = definitions["_httk_custom_figures"].as_optimade()
    assert figures["$id"] == "https://schemas.httk.org/ad-hoc/defs/properties/_httk_custom_figures"
    assert figures["x-optimade-definition"]["name"] == "_httk_custom_figures"
    assert figures["x-optimade-type"] == "list"
    assert figures["items"]["x-optimade-type"] == "dictionary"
    assert figures["x-optimade-requirements"]["response-level"] == "should not"


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


def test_info_structures_figure_definition_is_local_and_lightweight(client: ApiClient) -> None:
    response = client.get("/info/structures")
    assert response.status_code == 200
    definition = response.json()["data"]["properties"]["_httk_custom_figures"]
    assert definition["$id"] == "https://schemas.httk.org/ad-hoc/defs/properties/_httk_custom_figures"
    assert definition["x-optimade-definition"]["name"] == "_httk_custom_figures"
    assert "examples" not in definition


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
    assert run_validation(providers) == 0


class _ValidationProvider:
    def __init__(self, definition: EntryTypeDefinition, records: list[dict[str, Any]]) -> None:
        self.definition = definition
        self._records = records

    def entry_types(self) -> dict[str, EntryTypeDefinition]:
        return {"structures": self.definition}

    def property_keys(self, _: str) -> dict[str, str]:
        return {"id": "__id", "type": "__type", "value": "value"}

    def records(self, _: str) -> list[dict[str, Any]]:
        return self._records


def test_validation_retains_nulls_and_rejects_empty_entry_types(capsys: pytest.CaptureFixture[str]) -> None:
    definition = EntryTypeDefinition(
        "structures",
        "test",
        {
            name: PropertyDefinition.from_simple(name, description=name, fulltype="string")
            for name in ("id", "type", "value")
        },
    )
    valid = _ValidationProvider(
        definition,
        [{"__id": "m-1", "__type": "structures", "value": None}],
    )
    assert run_validation([valid]) == 0

    empty = _ValidationProvider(definition, [])
    assert run_validation([empty]) == 1
    assert "no records were served" in capsys.readouterr().err
    assert run_validation([]) == 1
    assert "no records were served by any provider" in capsys.readouterr().err


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
        "_httk_custom_figures",
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
    assert attributes["_httk_custom_figures"][0] == {
        "key": "band",
        "url": "https://plots.example.test/api/extensions/figures/anyt:am-1-0001/band.svg",
        "dark_url": "https://plots.example.test/api/extensions/figures/anyt:am-1-0001/dark--band.svg",
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
            "_httk_custom_figures",
            "_anyterial_search_text",
            "_anyterial_space_group_search",
        )
    )


@pytest.mark.parametrize(
    "property_name",
    (
        "id",
        "_anyterial_formula",
        "_anyterial_classification",
        "_anyterial_space_group",
        "_httk_magndata_ids",
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
            # _httk_magndata_ids serves a list; the store sorts it by the scalar
            # mirror (the ids joined with ", "), so order must be checked against
            # that join-key, not Python list comparison. The join is injective on
            # comma-free ids, so equal keys imply equal lists and == still holds.
            sort_key = (
                (lambda value: ", ".join(value)) if property_name == "_httk_magndata_ids" else (lambda value: value)
            )
            assert non_null == sorted(non_null, key=sort_key, reverse=sort.startswith("-"))


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

    structures, properties, _relationships, _references = dataset_module.build_dataset()
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
        projected = dataset_module._material_properties(unresolved, "https://plots.example.test/api")
    finally:
        material_store.cleanup_material_store({"materials_database": opened.database})
    assert projected["_anyterial_magndata_variants"] == []
    assert projected["_anyterial_formula"] == formula


# Golden full orderings captured from the legacy search before its deletion.
def _material_ids(numbers):
    return [f"anyt:am-1-{number:04d}" for number in numbers]


GOLDEN_SORT_ORDERS = {
    "screening_rank": _material_ids(list(range(1, 181))),
    "max_ss_desc": _material_ids(list(range(1, 181))),
    # sort=_anyterial_formula,id ascending: chemical-formula strings compared
    # lexicographically (all ASCII, no nulls in the fixture), id breaking ties.
    "formula_asc": _material_ids(
        [
            159,
            132,
            139,
            76,
            172,
            167,
            125,
            149,
            171,
            177,
            147,
            135,
            127,
            61,
            102,
            6,
            118,
            85,
            160,
            163,
            133,
            114,
            91,
            34,
            83,
            169,
            86,
            53,
            8,
            77,
            69,
            27,
            129,
            79,
            179,
            33,
            119,
            56,
            1,
            4,
            17,
            157,
            165,
            140,
            155,
            164,
            178,
            55,
            7,
            161,
            131,
            148,
            47,
            123,
            170,
            42,
            143,
            25,
            106,
            23,
            176,
            32,
            68,
            174,
            111,
            173,
            156,
            31,
            98,
            50,
            22,
            122,
            45,
            101,
            24,
            75,
            72,
            89,
            29,
            40,
            15,
            141,
            19,
            99,
            145,
            20,
            112,
            58,
            109,
            51,
            94,
            117,
            73,
            158,
            152,
            12,
            95,
            87,
            74,
            124,
            103,
            81,
            107,
            11,
            146,
            30,
            130,
            88,
            162,
            90,
            70,
            16,
            13,
            166,
            9,
            59,
            28,
            2,
            175,
            153,
            48,
            142,
            71,
            67,
            49,
            41,
            138,
            14,
            10,
            63,
            136,
            46,
            60,
            82,
            126,
            150,
            3,
            35,
            121,
            37,
            39,
            154,
            78,
            92,
            96,
            66,
            116,
            128,
            115,
            137,
            36,
            110,
            105,
            97,
            104,
            144,
            18,
            180,
            151,
            134,
            120,
            52,
            21,
            62,
            26,
            43,
            54,
            44,
            100,
            5,
            168,
            108,
            38,
            84,
            93,
            65,
            113,
            80,
            64,
            57,
        ]
    ),
    "avg_ss_desc": _material_ids(
        [
            1,
            4,
            2,
            6,
            3,
            5,
            9,
            23,
            7,
            8,
            10,
            24,
            22,
            14,
            12,
            17,
            15,
            16,
            29,
            11,
            31,
            18,
            26,
            13,
            41,
            25,
            59,
            21,
            27,
            50,
            33,
            30,
            35,
            45,
            19,
            79,
            74,
            53,
            54,
            39,
            76,
            49,
            34,
            40,
            20,
            46,
            47,
            43,
            83,
            38,
            36,
            28,
            70,
            63,
            75,
            64,
            56,
            32,
            60,
            67,
            51,
            57,
            69,
            42,
            61,
            98,
            86,
            62,
            44,
            82,
            109,
            52,
            58,
            37,
            88,
            77,
            89,
            87,
            107,
            84,
            78,
            94,
            95,
            130,
            66,
            99,
            68,
            48,
            92,
            123,
            72,
            118,
            110,
            90,
            116,
            73,
            103,
            101,
            85,
            121,
            112,
            97,
            114,
            96,
            80,
            81,
            120,
            104,
            131,
            125,
            106,
            108,
            105,
            100,
            124,
            93,
            136,
            65,
            144,
            133,
            111,
            122,
            126,
            135,
            142,
            146,
            134,
            141,
            132,
            152,
            129,
            113,
            127,
            158,
            128,
            143,
            91,
            155,
            71,
            119,
            138,
            55,
            140,
            157,
            151,
            149,
            102,
            115,
            153,
            148,
            163,
            117,
            159,
            165,
            137,
            167,
            161,
            166,
            154,
            162,
            145,
            139,
            156,
            160,
            173,
            150,
            147,
            172,
            164,
            169,
            170,
            175,
            171,
            168,
            174,
            178,
            177,
            176,
            180,
            179,
        ]
    ),
    "bandgap_desc": _material_ids(
        [
            10,
            117,
            73,
            114,
            89,
            162,
            171,
            14,
            70,
            88,
            178,
            155,
            72,
            11,
            153,
            75,
            166,
            16,
            132,
            175,
            8,
            129,
            94,
            109,
            74,
            32,
            43,
            80,
            38,
            37,
            52,
            58,
            149,
            42,
            67,
            35,
            95,
            50,
            17,
            25,
            164,
            39,
            19,
            179,
            49,
            21,
            46,
            34,
            53,
            136,
            151,
            28,
            61,
            40,
            122,
            24,
            64,
            29,
            127,
            159,
            31,
            86,
            62,
            15,
            172,
            69,
            141,
            120,
            124,
            115,
            112,
            57,
            147,
            170,
            78,
            148,
            135,
            98,
            167,
            45,
            100,
            65,
            113,
            163,
            56,
            81,
            180,
            68,
            47,
            150,
            177,
            143,
            27,
            33,
            134,
            111,
            77,
            103,
            128,
            55,
            126,
            44,
            106,
            131,
            108,
            138,
            142,
            144,
            107,
            140,
            13,
            152,
            145,
            22,
            146,
            41,
            121,
            118,
            99,
            84,
            51,
            90,
            161,
            87,
            92,
            125,
            110,
            123,
            66,
            139,
            97,
            96,
            59,
            104,
            105,
            116,
            130,
            60,
            158,
            2,
            54,
            160,
            26,
            30,
            93,
            63,
            23,
            18,
            12,
            36,
            48,
            165,
            133,
            1,
            3,
            4,
            5,
            6,
            7,
            9,
            20,
            71,
            79,
            82,
            83,
            85,
            91,
            101,
            102,
            119,
            137,
            154,
            156,
            157,
            168,
            169,
            176,
            76,
            173,
            174,
        ]
    ),
    "abundance_desc": _material_ids(
        [
            31,
            50,
            101,
            6,
            118,
            45,
            98,
            62,
            130,
            16,
            72,
            89,
            122,
            127,
            132,
            18,
            134,
            15,
            29,
            70,
            75,
            153,
            166,
            61,
            120,
            135,
            151,
            17,
            107,
            171,
            126,
            150,
            10,
            14,
            40,
            56,
            114,
            80,
            34,
            7,
            47,
            55,
            123,
            147,
            41,
            49,
            67,
            138,
            51,
            58,
            109,
            112,
            38,
            65,
            84,
            108,
            113,
            8,
            27,
            53,
            69,
            77,
            86,
            149,
            163,
            175,
            35,
            121,
            9,
            12,
            71,
            73,
            90,
            94,
            95,
            117,
            119,
            11,
            28,
            111,
            129,
            170,
            179,
            63,
            146,
            24,
            148,
            46,
            60,
            102,
            37,
            39,
            25,
            42,
            32,
            68,
            106,
            140,
            157,
            164,
            165,
            178,
            5,
            168,
            82,
            131,
            155,
            13,
            30,
            57,
            64,
            83,
            159,
            19,
            99,
            22,
            141,
            143,
            21,
            52,
            156,
            161,
            169,
            26,
            74,
            81,
            103,
            124,
            43,
            44,
            54,
            100,
            162,
            33,
            174,
            1,
            91,
            167,
            172,
            4,
            59,
            79,
            88,
            173,
            176,
            48,
            137,
            154,
            158,
            2,
            3,
            20,
            23,
            36,
            66,
            76,
            78,
            87,
            92,
            93,
            96,
            97,
            104,
            105,
            110,
            115,
            116,
            125,
            128,
            133,
            136,
            139,
            142,
            144,
            152,
            160,
            177,
            180,
            85,
            145,
        ]
    ),
}


def test_search_filter_and_sort_goldens(client: ApiClient) -> None:
    mappings = {
        "q": '_anyterial_search_text CONTAINS "crsb"',
        "elements": '_anyterial_elements HAS ALL "Cr","Sb"',
        "classification": '_anyterial_classification = "collinear"',
        "electronic_type": '_anyterial_electronic_type = "unknown"',
        "phase": '_anyterial_magnetic_phases HAS "AM"',
        "wave": '_anyterial_wave_classes HAS "d"',
        "space_group": '_anyterial_space_group_search CONTAINS "p6_3"',
        "min_max_ss": "_anyterial_max_spin_splitting >= 1",
        "min_fdelta_pct": "_anyterial_spin_splitting_fraction >= 0.2",
    }
    # These are the exact strings emitted by search-form.js; the browser owns composition.
    source = (ROOT / "src" / "static" / "search-form.js").read_text(encoding="utf-8")
    assert 'HAS ALL ${elements.map(literal).join(",")}' in source
    assert "Number(value[name]) / scale" in source
    for expression in mappings.values():
        assert expression.split(" ", 1)[0] in source

    sort_expressions = {
        "screening_rank": "id",
        "formula_asc": "_anyterial_formula,id",
        "max_ss_desc": "-_anyterial_max_spin_splitting,id",
        "avg_ss_desc": "-_anyterial_avg_spin_splitting,id",
        "bandgap_desc": "-_httk_dft_band_gap,id",
        "abundance_desc": "-_anyterial_min_crustal_abundance,-_anyterial_max_spin_splitting,id",
    }
    for mode, expression in sort_expressions.items():
        response = client.get("/structures", params={"sort": expression, "response_fields": "id", "page_limit": "50"})
        assert response.status_code == 200
        page = response.json()
        ids = [item["id"] for item in page["data"]]
        while page["links"].get("next"):
            page_response = client.get(page["links"]["next"])
            assert page_response.status_code == 200
            page = page_response.json()
            ids.extend(item["id"] for item in page["data"])
        assert ids == GOLDEN_SORT_ORDERS[mode]
