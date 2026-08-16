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
from conftest import write_detail_assets, write_source_tables
from httk.serve import ASGIAppMount, compose_asgi_apps
from optimade import build_providers, build_service_app

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


def _display_floor(threshold: float) -> float:
    # Column 4 renders at 3 digits; floor a full-precision API threshold to its display value less
    # a half-ULP so a rounded cell (e.g. 0.634 for 0.63419) never spuriously fails a `>=` check.
    return round(threshold, 3) - 5e-4


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
        providers = build_providers(public_base_url=api_url, material_records=records)
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
    api_origin = f"http://127.0.0.1:{api_port}"
    api_url = f"{api_origin}/optimade/amdb"
    static_root = tmp_path_factory.mktemp("static-site")
    publish_static.publish_site(static_root, optimade_base_url=api_url)
    static_server, static_thread = _start_static_server(static_root)
    site_url = f"http://127.0.0.1:{static_server.server_port}"
    use_synthetic = os.environ.get(SYNTHETIC_DATA_ENVIRONMENT) == "1" or not _has_source_tables()
    service_app = (
        _synthetic_app(tmp_path_factory, api_url, site_url)
        if use_synthetic
        else build_service_app(public_base_url=api_url, cors_origins=(site_url,))
    )
    app = compose_asgi_apps([ASGIAppMount("/optimade/amdb", service_app)])
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
    assert all(value >= _display_floor(threshold) for value in values)
    assert values[0] >= values[1]


def _column_floats(rows, index: int) -> list[float]:
    values: list[float] = []
    for i in range(rows.count()):
        text = rows.nth(i).locator("td").nth(index).inner_text().strip()
        head = text.split()[0] if text else ""
        try:
            values.append(float(head))
        except ValueError:
            # A missing value renders as an accessible dash; skip it and check the numeric run.
            pass
    return values


def test_header_sort_link_roundtrip(two_origins: Origins, page: Page) -> None:
    # Column 4 (_anyterial_max_spin_splitting) is advertised sortable server-side
    # (optimade/adapter.py SORTABLE_PROPERTIES). Column 0 (_anyterial_formula,
    # "Material") is now sortable too, so it has a sort link. Every one of the
    # nine displayed columns (src/widgets/search_table.py) is now server-side
    # sortable, so there is no remaining non-sortable column to assert the negative on.
    page.goto(f"{two_origins.site_url}/search.html", wait_until="domcontentloaded")
    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    headers = page.locator("[data-httk-serve-optimade-table] thead th")
    assert headers.nth(0).locator("a.httk-serve-optimade-table__sort-link").count() == 1

    sort_link = headers.nth(4).locator("a.httk-serve-optimade-table__sort-link")
    sort_link.wait_for(state="attached", timeout=TIMEOUT_MS)
    href = sort_link.get_attribute("href") or ""
    # URLSearchParams.toString() percent-encodes the comma; accept either encoding tolerantly.
    assert "sort=_anyterial_max_spin_splitting" in href
    assert "%2Cid" in href or ",id" in href

    sort_link.click()
    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert page.evaluate("() => new URLSearchParams(location.search).get('sort')") == "_anyterial_max_spin_splitting,id"
    page.wait_for_function(
        "() => document.querySelectorAll('[data-httk-serve-optimade-table] thead th')[4]?.getAttribute('aria-sort') === 'ascending'",
        timeout=TIMEOUT_MS,
    )
    ascending = _column_floats(rows, 4)
    assert len(ascending) >= 2
    assert all(ascending[i] <= ascending[i + 1] for i in range(len(ascending) - 1)), ascending
    href = (
        page.locator("[data-httk-serve-optimade-table] thead th")
        .nth(4)
        .locator("a.httk-serve-optimade-table__sort-link")
        .get_attribute("href")
        or ""
    )
    assert "sort=-_anyterial_max_spin_splitting" in href

    page.locator("[data-httk-serve-optimade-table] thead th").nth(4).locator(
        "a.httk-serve-optimade-table__sort-link"
    ).click()
    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert (
        page.evaluate("() => new URLSearchParams(location.search).get('sort')") == "-_anyterial_max_spin_splitting,id"
    )
    page.wait_for_function(
        "() => document.querySelectorAll('[data-httk-serve-optimade-table] thead th')[4]?.getAttribute('aria-sort') === 'descending'",
        timeout=TIMEOUT_MS,
    )
    descending = _column_floats(rows, 4)
    assert len(descending) >= 2
    assert all(descending[i] >= descending[i + 1] for i in range(len(descending) - 1)), descending


def test_advanced_filter_disclosure_roundtrip(two_origins: Origins, page: Page) -> None:
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

    page.goto(f"{two_origins.site_url}/search.html", wait_until="domcontentloaded")
    advanced = page.locator("[data-httk-serve-optimade-advanced]")
    advanced.wait_for(state="attached", timeout=TIMEOUT_MS)
    help_link = advanced.locator("a.httk-serve-optimade-table__advanced-help")
    assert help_link.get_attribute("href") == "https://schemas.anyterial.se/defs/"

    advanced.locator("summary").click()
    filter_input = advanced.locator("[data-httk-serve-optimade-advanced-filter]")
    filter_input.fill(filter_query)
    advanced.locator("button[type=submit]").click()

    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert page.evaluate("() => new URLSearchParams(location.search).get('filter')") == filter_query
    values = _column_floats(rows, 4)
    assert len(values) >= 1
    assert all(value >= _display_floor(threshold) for value in values), values

    summary = page.locator("[data-httk-serve-optimade-summary]")
    summary.wait_for(state="visible", timeout=TIMEOUT_MS)
    pills = summary.locator(".httk-serve-optimade-table__pill")
    pill_texts = pills.evaluate_all("nodes => nodes.map(node => node.innerText)")
    assert any("Max spin splitting" in text and "≥" in text for text in pill_texts), pill_texts


def test_search_summary_reflects_filter_and_count(two_origins: Origins, page: Page) -> None:
    # The real dataset has 180 available entries; derive the total from the API so the test also
    # holds under the synthetic fallback dataset. On production data this is exactly 180, matching
    # the old site's "Showing X of 180 screened entries." line.
    body, _ = _api(two_origins, "/v1/structures", {"page_limit": 1})
    total = str(body["meta"]["data_available"])

    filtered = _api(
        two_origins,
        "/v1/structures",
        {
            "page_limit": 3,
            "response_fields": "_anyterial_max_spin_splitting",
            "sort": "-_anyterial_max_spin_splitting,id",
        },
    )[0]
    threshold = filtered["data"][2]["attributes"]["_anyterial_max_spin_splitting"]
    filter_query = f"_anyterial_max_spin_splitting >= {threshold}"
    page.goto(
        f"{two_origins.site_url}/search.html?{urlencode({'filter': filter_query, 'sort': '-_anyterial_max_spin_splitting,id'})}",
        wait_until="domcontentloaded",
    )
    summary = page.locator("[data-httk-serve-optimade-summary]")
    summary.wait_for(state="visible", timeout=TIMEOUT_MS)
    summary_text = summary.inner_text()
    assert summary_text.startswith("Showing ")
    assert f"of {total} screened entries." in summary_text
    pills = summary.locator(".httk-serve-optimade-table__pill")
    pill_texts = pills.evaluate_all("nodes => nodes.map(node => node.innerText)")
    assert any("Sorted by" in text for text in pill_texts), pill_texts
    # The filter pill must show the plain-text label and threshold, not raw LaTeX (see the
    # spin-splitting label overrides in src/widgets/search_table.py).
    assert any("Max spin splitting" in text and "≥" in text for text in pill_texts), pill_texts

    page.goto(f"{two_origins.site_url}/search.html", wait_until="domcontentloaded")
    plain_summary = page.locator("[data-httk-serve-optimade-summary]")
    plain_summary.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert f"Showing all {total} screened entries." in plain_summary.inner_text()
    assert plain_summary.locator(".httk-serve-optimade-table__pill").count() == 0


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
    # The home "total" stat is an unfiltered count; the widget reads data_returned (the filtered
    # total for the query), so derive the expected value from that same field.
    expected = str(body["meta"]["data_returned"])
    page.goto(f"{two_origins.site_url}/index.html", wait_until="domcontentloaded")
    total = page.locator('[data-site-stat="total"]')
    total.wait_for(state="visible", timeout=TIMEOUT_MS)
    page.wait_for_function(
        "expected => document.querySelector('[data-site-stat=\"total\"]')?.textContent === expected",
        expected,
        timeout=TIMEOUT_MS,
    )
    # A per-classification stat is a FILTERED count, so it distinguishes a real per-filter query from
    # one that has collapsed back to the total (where data_returned == data_available).
    collinear_body, _ = _api(
        two_origins, "/v1/structures", {"page_limit": 1, "filter": '_anyterial_classification = "collinear"'}
    )
    collinear_expected = str(collinear_body["meta"]["data_returned"])
    page.wait_for_function(
        "expected => document.querySelector('[data-site-stat=\"collinear\"]')?.textContent === expected",
        collinear_expected,
        timeout=TIMEOUT_MS,
    )


def test_page_size_dropdown_navigates_preserving_filter_and_sort(two_origins: Origins, page: Page) -> None:
    # The page-size dropdown (page_size_query="page_size" in src/widgets/search_table.py) offers
    # 50/100/500 and defaults to the authored 50. Selecting 100 must navigate preserving every other
    # URL parameter and load a first page of min(100, matching) rows. Start from a filtered+sorted URL
    # so the round-trip also proves those parameters survive the page-size navigation.
    #
    # EMPIRICAL COVERAGE REQUIREMENT: this filter MUST match every row on BOTH the real (180) and the
    # synthetic (51) datasets, else the row-count assertions become vacuous or wrong. `nsites`/
    # `nelements` are only populated on the 3 synthetic fixture rows (and 135/180 real rows), so they
    # do NOT qualify. `_anyterial_formula IS KNOWN` returns the full total on both — re-verify with a
    # data_returned == data_available probe before changing this filter.
    sort = "-_anyterial_max_spin_splitting,id"
    filter_query = "_anyterial_formula IS KNOWN"
    body, _ = _api(two_origins, "/v1/structures", {"page_limit": 1, "filter": filter_query})
    matching = int(body["meta"]["data_returned"])
    assert matching == int(body["meta"]["data_available"]), matching  # full coverage, per the note above

    page.goto(
        f"{two_origins.site_url}/search.html?{urlencode({'filter': filter_query, 'sort': sort})}",
        wait_until="domcontentloaded",
    )
    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)

    select = page.locator("[data-httk-serve-optimade-page-size]")
    select.wait_for(state="attached", timeout=TIMEOUT_MS)
    options = select.locator("option").evaluate_all("nodes => nodes.map(node => node.value)")
    assert options == ["50", "100", "500"], options
    assert select.input_value() == "50"
    # Default page size still applies on a filtered load: 50 rows (matching is >= 51 on both datasets).
    page.wait_for_function(
        "() => document.querySelectorAll("
        "'[data-httk-serve-optimade-table] tbody tr:not(.httk-serve-optimade-table__error)').length === 50",
        timeout=TIMEOUT_MS,
    )

    select.select_option("100")
    rows = _rows(page)
    rows.first.wait_for(state="visible", timeout=TIMEOUT_MS)
    assert page.evaluate("() => new URLSearchParams(location.search).get('page_size')") == "100"
    assert page.evaluate("() => new URLSearchParams(location.search).get('filter')") == filter_query
    assert page.evaluate("() => new URLSearchParams(location.search).get('sort')") == sort
    expected = min(100, matching)  # synthetic 51 (> 50, so it discriminates), real 100
    page.wait_for_function(
        "expected => document.querySelectorAll("
        "'[data-httk-serve-optimade-table] tbody tr:not(.httk-serve-optimade-table__error)').length === expected",
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
