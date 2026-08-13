"""Browser contract for the separately hosted static site and OPTIMADE API."""

import json
import os
import socket
import threading
import time
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import material_store
import publish_static
import pytest
import serve_optimade
from conftest import write_detail_assets, write_source_tables
from optimade_service import build_service_app

BROWSER_REQUIRED = os.environ.get("ALTERMAGNETS_BROWSER_REQUIRED") == "1"


def _unavailable(message: str) -> None:
    if BROWSER_REQUIRED:
        pytest.fail(message)
    pytest.skip(message)


try:
    from playwright.sync_api import Browser, Page, sync_playwright
    from playwright.sync_api import Error as PlaywrightError
except ImportError as error:
    if BROWSER_REQUIRED:
        pytest.fail(f"Playwright is unavailable: {error}")
    pytest.skip(f"Playwright is unavailable: {error}", allow_module_level=True)

pytestmark = pytest.mark.browser

TIMEOUT_MS = 30_000
SYNTHETIC_DATA_ENVIRONMENT = "ALTERMAGNETS_BROWSER_SYNTHETIC"


@dataclass(frozen=True)
class Origins:
    site_url: str
    api_url: str
    browser: Browser


class StaticHandler(SimpleHTTPRequestHandler):
    extensions_map: ClassVar[dict[str, str]] = {**SimpleHTTPRequestHandler.extensions_map, ".mjs": "text/javascript"}

    def log_message(self, _format: str, *args: object) -> None:
        del args


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_static_server(directory: Path) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticHandler, directory=str(directory)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _stop_static_server(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def _response(url: str, *, headers: dict[str, str] | None = None) -> tuple[dict[str, object], dict[str, str]]:
    request = Request(url, headers=headers or {"Accept": "application/vnd.api+json, application/json"})
    with urlopen(request, timeout=15) as response:
        return json.load(response), {key.lower(): value for key, value in response.headers.items()}


def _api(
    origins: Origins, path: str, params: dict[str, object] | None = None, *, headers: dict[str, str] | None = None
) -> tuple[dict[str, object], dict[str, str]]:
    query = f"?{urlencode(params or {}, doseq=True)}" if params else ""
    return _response(f"{origins.api_url}{path}{query}", headers=headers)


def _wait_until_ready(api_url: str) -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(f"{api_url}/versions"), timeout=15) as response:
                response.read()
            return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"OPTIMADE service at {api_url} did not become ready")


def _rows(page: Page):
    return page.locator('[data-httk-serve-optimade-table] tbody tr:not(.httk-serve-optimade-table__error)')


def _numeric_cell(row, index: int) -> float:
    text = row.locator("td").nth(index).inner_text()
    return float(text.split()[0])


def _detail_record(origins: Origins) -> dict[str, object]:
    body, _ = _api(
        origins,
        "/v1/structures",
        {
            "page_limit": 1000,
            "response_fields": "id,_httk_custom_figures,_anyterial_magndata_variants",
        },
    )
    for record in body["data"]:
        attributes = record["attributes"]
        figures = attributes.get("_httk_custom_figures") or []
        has_dark_figure = any(
            figure.get("available") and figure.get("url") and figure.get("dark_url") != figure.get("url")
            for figure in figures
        )
        if attributes.get("_anyterial_magndata_variants") and has_dark_figure:
            return record
    pytest.fail("The OPTIMADE dataset has no material with variants and a theme-aware figure")


def _has_source_tables() -> bool:
    data_dir = material_store.resolve_data_dir()
    return all(
        (data_dir / filename).is_file()
        for filename in (
            material_store.SCREENING_RESULTS_FILENAME,
            material_store.MAGNDATA_COLLINEAR_FILENAME,
            material_store.MAGNDATA_NONCOLLINEAR_FILENAME,
        )
    )


def _synthetic_app(tmp_path_factory: pytest.TempPathFactory, api_url: str, site_url: str):
    root = tmp_path_factory.mktemp("browser-data")
    source = write_source_tables(root / "tables", material_count=51)
    details = write_detail_assets(root / "details")
    overrides = {
        "ALTERMAGNETS_DATA_DIR": str(source),
        "ALTERMAGNETS_DETAILS_DIR": str(details),
        "ALTERMAGNETS_STORE_PATH": str(root / "missing.duckdb"),
    }
    previous = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    try:
        records: dict[str, object] = {}
        providers = serve_optimade.build_providers(public_base_url=api_url, material_records=records)
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return build_service_app(
        public_base_url=api_url,
        cors_origins=(site_url,),
        providers=providers,
        dataset=records,
        details_root=details,
    )


@pytest.fixture(scope="session")
def two_origins(tmp_path_factory: pytest.TempPathFactory) -> Origins:
    try:
        import uvicorn
    except ImportError as error:
        _unavailable(f"Uvicorn is unavailable: {error}")
    try:
        api_port = _free_port()
    except PermissionError as error:
        _unavailable(f"Localhost sockets are unavailable: {error}")
    api_url = f"http://127.0.0.1:{api_port}"
    static_root = tmp_path_factory.mktemp("static-site")
    publish_static.publish_site(static_root, optimade_base_url=api_url)
    static_server, static_thread = _start_static_server(static_root)
    site_url = f"http://127.0.0.1:{static_server.server_port}"
    use_synthetic = os.environ.get(SYNTHETIC_DATA_ENVIRONMENT) == "1" or not _has_source_tables()
    app = (
        _synthetic_app(tmp_path_factory, api_url, site_url)
        if use_synthetic
        else build_service_app(public_base_url=api_url, cors_origins=(site_url,))
    )
    api_server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=api_port, log_level="warning", access_log=False)
    )
    api_thread = threading.Thread(target=api_server.run, daemon=True)
    api_thread.start()
    browser = None
    try:
        _wait_until_ready(api_url)
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch(
                    headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
            except PlaywrightError as error:
                _unavailable(f"Chromium is unavailable: {error}")
            yield Origins(site_url=site_url, api_url=api_url, browser=browser)
            browser.close()
    finally:
        if browser is not None and browser.is_connected():
            browser.close()
        api_server.should_exit = True
        api_thread.join(timeout=15)
        _stop_static_server(static_server, static_thread)


@pytest.fixture
def page(two_origins: Origins) -> Page:
    context = two_origins.browser.new_context()
    try:
        page = context.new_page()
    except PlaywrightError as error:
        context.close()
        _unavailable(f"This environment cannot run browser pages: {error}")
    try:
        yield page
    finally:
        context.close()


def test_search_paginates_and_renders_formula(two_origins: Origins, page: Page) -> None:
    page.goto(f"{two_origins.site_url}/search.html", wait_until="domcontentloaded")
    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert page.locator('[data-httk-serve-optimade-table] thead th').count() == 9
    formula = rows.first.locator("td").first
    assert formula.locator(".katex").count(), "Formula cell did not render KaTeX."
    first_row = rows.first.inner_text()
    next_button = page.locator("[data-httk-serve-optimade-next]")
    next_button.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert next_button.is_enabled()
    next_button.click()
    page.wait_for_function(
        "previous => document.querySelector('[data-httk-serve-optimade-table] tbody tr')?.innerText !== previous",
        first_row,
        timeout=TIMEOUT_MS,
    )


def test_filter_and_sort_roundtrip(two_origins: Origins, page: Page) -> None:
    body, _ = _api(
        two_origins,
        "/v1/structures",
        {
            "page_limit": 3,
            "response_fields": "_anyterial_max_spin_splitting",
            "sort": "-_anyterial_max_spin_splitting,id",
        },
    )
    threshold = body["data"][2]["attributes"]["_anyterial_max_spin_splitting"]
    filter_query = f"_anyterial_max_spin_splitting >= {threshold}"
    page.goto(
        f"{two_origins.site_url}/search.html?{urlencode({'filter': filter_query, 'sort': '-_anyterial_max_spin_splitting,id'})}",
        wait_until="domcontentloaded",
    )
    rows = _rows(page)
    rows.nth(1).wait_for(state="visible", timeout=TIMEOUT_MS)
    assert 2 <= rows.count() <= 50
    values = [_numeric_cell(rows.nth(index), 4) for index in range(rows.count())]
    assert all(value >= threshold for value in values)
    assert values[0] >= values[1]


def test_detail_loads_variants_references_and_figures(two_origins: Origins, page: Page) -> None:
    record = _detail_record(two_origins)
    material_id = record["id"]
    response, _ = _api(
        two_origins,
        "/v1/structures",
        {
            "filter": f'id = "{material_id}"',
            "include": "references",
            "response_fields": "_httk_custom_figures,_anyterial_magndata_variants",
        },
    )
    expected_dois = {
        item.get("attributes", {}).get("doi")
        for item in response.get("included", [])
        if item.get("attributes", {}).get("doi")
    }
    page.goto(f"{two_origins.site_url}/material.html?{urlencode({'id': material_id})}", wait_until="domcontentloaded")
    header = page.locator("[data-site-material-detail] h2")
    header.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert header.locator(".katex").count() or header.inner_text().strip()
    page.locator(".symmetry-table tbody tr").first.wait_for(state="visible", timeout=TIMEOUT_MS)
    if expected_dois:
        doi_links = page.locator('a[href^="https://doi.org/"]')
        doi_links.first.wait_for(state="visible", timeout=TIMEOUT_MS)
        rendered_dois = doi_links.evaluate_all("links => links.map(link => link.href)")
        assert all(any(doi in href for href in rendered_dois) for doi in expected_dois)
    images = page.locator("img.theme-aware-figure")
    images.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    for index in range(images.count()):
        images.nth(index).scroll_into_view_if_needed()
    page.wait_for_function(
        "() => [...document.querySelectorAll('img.theme-aware-figure')].every(image => image.naturalWidth > 0)",
        timeout=TIMEOUT_MS,
    )
    light_sources = images.evaluate_all("images => images.map(image => image.src)")
    assert all(source.startswith(two_origins.api_url) for source in light_sources)
    page.get_by_role("button", name="Use dark theme").click()
    page.wait_for_function(
        "light => [...document.querySelectorAll('img.theme-aware-figure')].some((image, index) => image.src !== light[index] && image.src === image.dataset.srcDark)",
        light_sources,
        timeout=TIMEOUT_MS,
    )


def test_home_stats_match_api_count(two_origins: Origins, page: Page) -> None:
    body, _ = _api(two_origins, "/v1/structures", {"page_limit": 1000})
    expected = str(body["meta"]["data_available"])
    page.goto(f"{two_origins.site_url}/index.html", wait_until="domcontentloaded")
    total = page.locator('[data-site-stat="total"]')
    total.wait_for(state="visible", timeout=TIMEOUT_MS)
    page.wait_for_function(
        "expected => document.querySelector('[data-site-stat=\"total\"]')?.textContent === expected",
        expected,
        timeout=TIMEOUT_MS,
    )


def test_missing_material_renders_not_found(two_origins: Origins, page: Page) -> None:
    page.goto(f"{two_origins.site_url}/material.html?id=anyt%3Aam-1-9999", wait_until="domcontentloaded")
    page.get_by_text("The requested material entry could not be found.").wait_for(state="visible", timeout=TIMEOUT_MS)


def test_api_down_renders_service_unavailable(two_origins: Origins, page: Page, tmp_path: Path) -> None:
    unused_api_url = f"http://127.0.0.1:{_free_port()}"
    down_root = tmp_path / "api-down"
    publish_static.publish_site(down_root, optimade_base_url=unused_api_url)
    server, thread = _start_static_server(down_root)
    try:
        page.goto(f"http://127.0.0.1:{server.server_port}/search.html", wait_until="domcontentloaded")
        page.get_by_text("OPTIMADE service could not be reached.").wait_for(state="visible", timeout=TIMEOUT_MS)
    finally:
        _stop_static_server(server, thread)


def test_cors_rejects_untrusted_origin(two_origins: Origins) -> None:
    _, denied_headers = _api(two_origins, "/structures", {"page_limit": 1}, headers={"Origin": "http://evil.example"})
    _, allowed_headers = _api(two_origins, "/structures", {"page_limit": 1}, headers={"Origin": two_origins.site_url})
    assert denied_headers.get("access-control-allow-origin") != "http://evil.example"
    assert allowed_headers.get("access-control-allow-origin") == two_origins.site_url
