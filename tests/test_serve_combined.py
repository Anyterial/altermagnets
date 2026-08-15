"""In-process coverage for the website, OPTIMADE index, and AMDB composition."""

import json
import re
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest

pytest.importorskip("httk.serve.optimade")
pytest.importorskip("httk.atomistic")

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import serve_combined
from httk.serve.web import create_asgi_app as create_web_asgi_app
from optimade import combined


def _widget_configuration(document: str) -> dict[str, object]:
    match = re.search(
        r'<script id="httk-serve-optimade-table-[^"]+-config" type="application/json">(.*?)</script>',
        document,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_combined_discovery_mounts_index_and_amdb_and_paginates() -> None:
    app = serve_combined.create_combined_app()

    with TestClient(app, base_url="http://testserver") as client:
        home = client.get("/")
        search = client.get("/search")
        index_versions = client.get("/optimade/index/versions")
        index_info = client.get("/optimade/index/v1/info")
        index_links = client.get("/optimade/index/v1/links")
        index_structures = client.get("/optimade/index/v1/structures")
        amdb_versions = client.get("/optimade/amdb/versions")
        amdb_info = client.get("/optimade/amdb/v1/info")
        structures_info = client.get("/optimade/amdb/v1/info/structures")
        first = client.get("/optimade/amdb/v1/structures", params={"page_limit": "2"})
        second = client.get(first.json()["links"]["next"])
        table_script = client.get("/_httk/serve/assets/serve-optimade-table.mjs")

    assert home.status_code == search.status_code == 200
    configuration = _widget_configuration(search.text)
    assert configuration["base_url"] == "/optimade/amdb"
    assert configuration["entry_type"] == "structures"
    assert configuration["filter_query"] == "filter"
    assert configuration["sort_query"] == "sort"
    assert configuration["detail_route"] == "material"
    assert configuration["detail_column"] == "_anyterial_formula"
    assert configuration["detail_query"] == "id"
    assert configuration["page_size"] == 50
    columns = cast(list[dict[str, object]], configuration["columns"])
    assert [column["key"] for column in columns] == [
        "_anyterial_formula",
        "_httk_magndata_ids",
        "_anyterial_classification",
        "_anyterial_space_group",
        "_anyterial_max_spin_splitting",
        "_anyterial_avg_spin_splitting",
        "_anyterial_spin_splitting_fraction",
        "_httk_dft_band_gap",
        "_anyterial_min_crustal_abundance",
    ]
    assert index_versions.status_code == amdb_versions.status_code == 200
    assert index_versions.text == amdb_versions.text == "version\n1\n"
    assert index_info.status_code == index_links.status_code == 200
    index_attributes = index_info.json()["data"]["attributes"]
    assert index_attributes["is_index"] is True
    assert index_attributes["entry_types_by_format"] == {"json": []}
    assert index_attributes["available_endpoints"] == ["info", "links"]
    assert index_info.json()["data"]["relationships"]["default"] == {"data": {"type": "links", "id": "amdb"}}
    configured_links = index_links.json()["data"][:2]
    assert [item["id"] for item in configured_links] == ["index", "amdb"]
    assert [item["attributes"]["link_type"] for item in configured_links] == ["root", "child"]
    assert [item["attributes"]["homepage"] for item in configured_links] == [
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8080",
    ]
    assert configured_links[0]["attributes"]["base_url"] == "http://127.0.0.1:8080/optimade/index"
    assert configured_links[1]["attributes"]["base_url"] == "http://127.0.0.1:8080/optimade/amdb"
    assert index_structures.status_code == 404
    assert amdb_info.status_code == structures_info.status_code == 200
    assert amdb_info.json()["data"]["attributes"]["is_index"] is False
    assert first.status_code == 200
    assert len(first.json()["data"]) == 2
    assert urlsplit(first.json()["links"]["next"]).path == "/optimade/amdb/v1/structures"
    assert second.status_code == 200
    assert len(second.json()["data"]) == 2
    assert table_script.status_code == 200


@pytest.mark.parametrize("path", ["/", "/structures", "/info/structures", "/partial_data/structures/a/x"])
def test_combined_index_refuses_entry_listing_endpoints(path: str) -> None:
    with TestClient(serve_combined.create_combined_app()) as client:
        response = client.get("/optimade/index/v1" + path)
    assert response.status_code == 404


def test_combined_amdb_links_have_one_root_back_to_index() -> None:
    with TestClient(serve_combined.create_combined_app(), base_url="http://testserver") as client:
        response = client.get("/optimade/amdb/v1/links")
    assert response.status_code == 200
    links = response.json()["data"]
    configured = [item for item in links if item["id"] != "optimade"]
    assert len(configured) == 1
    assert configured[0]["attributes"] == {
        "name": "Anyterial OPTIMADE Index",
        "description": "Index meta-database for the Anyterial collection of materials databases.",
        "base_url": "http://127.0.0.1:8080/optimade/index",
        "homepage": "http://127.0.0.1:8080",
        "link_type": "root",
    }


def test_combined_public_origin_is_used_by_all_discovery_links() -> None:
    public_origin = "https://site.example/"
    with TestClient(serve_combined.create_combined_app(public_base_url=public_origin)) as client:
        search = client.get("/search")
        index_links = client.get("/optimade/index/v1/links").json()["data"][:2]
        amdb_links = [item for item in client.get("/optimade/amdb/v1/links").json()["data"] if item["id"] != "optimade"]
        page = client.get("/optimade/amdb/v1/structures", params={"page_limit": "2"})

    assert _widget_configuration(search.text)["base_url"] == "/optimade/amdb"
    assert urlsplit(page.json()["links"]["next"]).path == "/optimade/amdb/v1/structures"
    assert index_links == [
        {
            "type": "links",
            "id": "index",
            "attributes": {
                "name": "Anyterial OPTIMADE Index",
                "description": "Index meta-database for the Anyterial collection of materials databases.",
                "base_url": "https://site.example/optimade/index",
                "homepage": "https://site.example",
                "link_type": "root",
            },
        },
        {
            "type": "links",
            "id": "amdb",
            "attributes": {
                "name": "Anyterial Altermagnets Database",
                "description": "A database of materials computationally predicted to exhibit altermagnetism.",
                "base_url": "https://site.example/optimade/amdb",
                "homepage": "https://site.example",
                "link_type": "child",
            },
        },
    ]
    assert amdb_links == [
        {
            "type": "links",
            "id": "index",
            "attributes": {
                "name": "Anyterial OPTIMADE Index",
                "description": "Index meta-database for the Anyterial collection of materials databases.",
                "base_url": "https://site.example/optimade/index",
                "homepage": "https://site.example",
                "link_type": "root",
            },
        }
    ]


def test_combined_public_origin_validation_happens_before_child_creation() -> None:
    created: list[str] = []

    def factory() -> Starlette:
        created.append("called")
        return Starlette()

    for value in (
        "",
        "https://site.example/root",
        "https://site.example?query=1",
        "https://site.example#fragment",
        "https://user:pass@site.example",
        "https://site.example:not-a-port",
        "ftp://site.example",
        "site.example",
        "https://",
    ):
        with pytest.raises(ValueError, match="public_base_url"):
            serve_combined.create_combined_app(
                public_base_url=value,
                web_factory=factory,
                index_factory=factory,
                amdb_factory=factory,
            )

    assert created == []


def test_combined_figure_route_and_nested_public_base() -> None:
    app = serve_combined.create_combined_app()

    with TestClient(app, base_url="http://testserver") as client:
        response = client.get(
            "/optimade/amdb/v1/structures",
            params={
                "filter": 'id = "anyt:am-1-0001"',
                "response_fields": "_httk_custom_figures",
            },
        )
        assert response.status_code == 200
        figures = response.json()["data"][0]["attributes"]["_httk_custom_figures"]
        figure = next(item for item in figures if item["key"] == "structure")
        assert figure["url"].startswith("http://127.0.0.1:8080/optimade/amdb/extensions/figures/")
        served = client.get(urlsplit(figure["url"]).path)
        old_route = client.get("/optimade/amdb/figures/anyt:am-1-0001/structure.svg")

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/svg+xml"
    assert old_route.status_code == 404


def test_combined_public_origin_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(serve_combined, "create_combined_app", lambda **kwargs: kwargs)
    monkeypatch.setattr(serve_combined, "run_dev_server", lambda **kwargs: captured.update(kwargs))

    assert serve_combined.main(["--host", "127.0.0.1", "--port", "9000"]) == 0
    assert captured["app"] == {"public_base_url": "http://127.0.0.1:9000"}

    assert serve_combined.main(["--host", "::1", "--port", "9000"]) == 0
    assert captured["app"] == {"public_base_url": "http://[::1]:9000"}

    with pytest.raises(SystemExit):
        serve_combined.main(["--host", "0.0.0.0"])
    assert serve_combined.main(["--host", "0.0.0.0", "--public-base-url", "https://site.example"]) == 0
    assert captured["app"] == {"public_base_url": "https://site.example"}


def test_standalone_static_site_does_not_advertise_the_combined_pilot() -> None:
    app = create_web_asgi_app(ROOT / "src", config_name="config")

    with TestClient(app, base_url="http://testserver") as client:
        home = client.get("/")

    assert home.status_code == 200
    assert ">OPTIMADE</a>" not in home.text


def _child_app(name: str, events: list[str]) -> Starlette:
    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        events.append(f"enter:{name}")
        try:
            yield
        finally:
            events.append(f"exit:{name}")

    async def response(_: object) -> PlainTextResponse:
        return PlainTextResponse(name)

    return Starlette(routes=[Route("/{path:path}", response)], lifespan=lifespan)


def test_generic_composition_owns_distinct_child_lifespans_once() -> None:
    events: list[str] = []
    app = serve_combined.create_combined_app(
        index_app=_child_app("index", events),
        amdb_app=_child_app("amdb", events),
        web_app=_child_app("web", events),
    )

    with TestClient(app) as client:
        assert client.get("/optimade/index/v1/info").text == "index"
        assert client.get("/optimade/amdb/v1/info").text == "amdb"
        assert client.get("/search").text == "web"

    assert events == ["enter:amdb", "enter:index", "enter:web", "exit:web", "exit:index", "exit:amdb"]


@pytest.mark.parametrize("failure", ["index", "amdb"])
def test_factory_failure_closes_only_factory_created_web_app(failure: str) -> None:
    closed: list[str] = []
    factory_web = Starlette()
    factory_web.state.engine = type("Engine", (), {"close": lambda self: closed.append("factory")})()
    injected_web = Starlette()
    injected_web.state.engine = type("Engine", (), {"close": lambda self: closed.append("injected")})()

    def fail() -> Starlette:
        raise RuntimeError(f"{failure} construction failed")

    kwargs = {"index_factory": fail} if failure == "index" else {"amdb_factory": fail}
    with pytest.raises(RuntimeError, match=f"{failure} construction failed"):
        serve_combined.create_combined_app(web_factory=lambda: factory_web, **kwargs)
    with pytest.raises(RuntimeError, match=f"{failure} construction failed"):
        serve_combined.create_combined_app(web_app=injected_web, **kwargs)

    assert closed == ["factory"]


def test_composition_failure_closes_only_factory_created_web_app(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str] = []
    factory_web = Starlette()
    factory_web.state.engine = type("Engine", (), {"close": lambda self: closed.append("factory")})()
    injected_web = Starlette()
    injected_web.state.engine = type("Engine", (), {"close": lambda self: closed.append("injected")})()

    def fail_compose(*_: object, **__: object) -> Starlette:
        raise RuntimeError("composition failed")

    monkeypatch.setattr(combined, "compose_asgi_apps", fail_compose)
    with pytest.raises(RuntimeError, match="composition failed"):
        serve_combined.create_combined_app(
            web_factory=lambda: factory_web,
            index_factory=Starlette,
            amdb_factory=Starlette,
        )
    with pytest.raises(RuntimeError, match="composition failed"):
        serve_combined.create_combined_app(
            web_app=injected_web,
            index_factory=Starlette,
            amdb_factory=Starlette,
        )

    assert closed == ["factory"]
