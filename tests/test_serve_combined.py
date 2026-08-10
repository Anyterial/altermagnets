"""In-process coverage for the opt-in website plus OPTIMADE composition."""

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


def _widget_configuration(document: str) -> dict[str, object]:
    match = re.search(
        r'<script id="httk-serve-optimade-table-[^"]+-config" type="application/json">(.*?)</script>',
        document,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_combined_app_mounts_the_search_widget_and_preserves_versioned_pagination() -> None:
    app = serve_combined.create_combined_app()

    with TestClient(app, base_url="http://testserver") as client:
        search = client.get("/search")
        versions = client.get("/optimade/versions")
        info = client.get("/optimade/v1/info")
        structures_info = client.get("/optimade/v1/info/structures")
        table_script = client.get("/_httk/serve/assets/serve-optimade-table.mjs")
        first = client.get("/optimade/v1/structures", params={"page_limit": "2"})
        second = client.get(first.json()["links"]["next"])

    assert search.status_code == 200
    assert 'data-httk-serve-optimade-table="1"' in search.text
    assert "/_httk/serve/assets/serve-optimade-table.mjs" in search.text
    configuration = _widget_configuration(search.text)
    assert configuration["base_url"] == "/optimade"
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
    assert "next" not in configuration and "previous" not in configuration
    assert versions.status_code == 200
    assert info.status_code == 200
    assert structures_info.status_code == 200
    structures_properties = structures_info.json()["data"]["properties"]
    assert "_httk_dft_band_gap" in structures_properties
    assert "_httk_magnetic_space_group_bns" in structures_properties
    assert "_httk_magndata_ids" in structures_properties
    assert table_script.status_code == 200
    assert table_script.headers["content-type"].startswith("text/javascript")
    assert first.status_code == 200
    assert len(first.json()["data"]) == 2
    assert urlsplit(first.json()["links"]["next"]).path == "/optimade/v1/structures"
    assert second.status_code == 200
    assert len(second.json()["data"]) == 2


def test_combined_figure_route_and_public_base() -> None:
    app = serve_combined.create_combined_app()

    with TestClient(app, base_url="http://testserver") as client:
        response = client.get(
            "/optimade/v1/structures",
            params={
                "filter": 'id = "anyt:am-1-0001"',
                "response_fields": "_anyterial_figures",
            },
        )
        assert response.status_code == 200
        figures = response.json()["data"][0]["attributes"]["_anyterial_figures"]
        figure = next(item for item in figures if item["key"] == "structure")
        assert figure["url"].startswith("http://127.0.0.1:8080/optimade/figures/")
        served = client.get(urlsplit(figure["url"]).path)

    assert served.status_code == 200
    assert served.headers["content-type"] == "image/svg+xml"


def test_combined_public_base_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(serve_combined, "create_combined_app", lambda **kwargs: kwargs)
    monkeypatch.setattr(serve_combined, "run_dev_server", lambda **kwargs: captured.update(kwargs))

    assert serve_combined.main(["--host", "127.0.0.1", "--port", "9000"]) == 0
    assert captured["app"] == {"public_base_url": "http://127.0.0.1:9000/optimade"}

    assert serve_combined.main(["--host", "::1", "--port", "9000"]) == 0
    assert captured["app"] == {"public_base_url": "http://[::1]:9000/optimade"}

    with pytest.raises(SystemExit):
        serve_combined.main(["--host", "0.0.0.0"])
    assert serve_combined.main(
        ["--host", "0.0.0.0", "--public-base-url", "https://site.example/optimade"]
    ) == 0
    assert captured["app"] == {"public_base_url": "https://site.example/optimade"}


def test_standalone_static_site_does_not_advertise_the_combined_pilot() -> None:
    app = serve_combined.create_web_asgi_app(ROOT / "src", config_name="config")

    with TestClient(app, base_url="http://testserver") as client:
        home = client.get("/")

    assert home.status_code == 200
    assert ">OPTIMADE</a>" not in home.text


def _child_app(name: str, events: list[str], *, fail_startup: bool = False) -> Starlette:
    @asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        events.append(f"enter:{name}")
        try:
            if fail_startup:
                raise RuntimeError(f"{name} startup failed")
            yield
        finally:
            events.append(f"exit:{name}")

    async def response(_: object) -> PlainTextResponse:
        return PlainTextResponse(name)

    return Starlette(routes=[Route("/{path:path}", response)], lifespan=lifespan)


def test_combined_lifespan_owns_children_once_in_reverse_shutdown_order() -> None:
    events: list[str] = []
    app = serve_combined.create_combined_app(
        optimade_app=_child_app("optimade", events), web_app=_child_app("web", events)
    )

    with TestClient(app) as client:
        assert client.get("/optimade/v1/info").text == "optimade"
        assert client.get("/search").text == "web"

    assert events == ["enter:web", "enter:optimade", "exit:optimade", "exit:web"]


def test_combined_lifespan_closes_web_when_optimade_startup_fails() -> None:
    events: list[str] = []
    app = serve_combined.create_combined_app(
        optimade_app=_child_app("optimade", events, fail_startup=True),
        web_app=_child_app("web", events),
    )

    with pytest.raises(RuntimeError, match="optimade startup failed"), TestClient(app):
        pass

    assert events == ["enter:web", "enter:optimade", "exit:optimade", "exit:web"]


def test_combined_lifespan_does_not_enter_optimade_when_web_startup_fails() -> None:
    events: list[str] = []
    app = serve_combined.create_combined_app(
        optimade_app=_child_app("optimade", events),
        web_app=_child_app("web", events, fail_startup=True),
    )

    with pytest.raises(RuntimeError, match="web startup failed"), TestClient(app):
        pass

    assert events == ["enter:web", "exit:web"]


def test_combined_construction_closes_only_a_factory_created_web_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []
    factory_web = Starlette()
    factory_web.state.engine = type("Engine", (), {"close": lambda self: closed.append("factory")})()
    injected_web = Starlette()
    injected_web.state.engine = type("Engine", (), {"close": lambda self: closed.append("injected")})()

    def fail_optimade() -> Starlette:
        raise RuntimeError("optimade construction failed")

    with pytest.raises(RuntimeError, match="optimade construction failed"):
        serve_combined.create_combined_app(web_factory=lambda: factory_web, optimade_factory=fail_optimade)
    with pytest.raises(RuntimeError, match="optimade construction failed"):
        serve_combined.create_combined_app(web_app=injected_web, optimade_factory=fail_optimade)

    def fail_parent(**_: object) -> Starlette:
        raise RuntimeError("parent construction failed")

    monkeypatch.setattr(serve_combined, "Starlette", fail_parent)
    with pytest.raises(RuntimeError, match="parent construction failed"):
        serve_combined.create_combined_app(web_factory=lambda: factory_web, optimade_factory=Starlette)

    assert closed == ["factory", "factory"]
