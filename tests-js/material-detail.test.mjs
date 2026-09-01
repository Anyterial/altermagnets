import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { DomDocument, element, installDom } from "./dom.mjs";

const API = "https://api.example.test/optimade/amdb";
const MATERIAL_ID = "anyt.am-1/0001";
const siblingProtocol = new URL("../../httk-serve/src/httk/serve/web/assets/serve-optimade-table-protocol.mjs", import.meta.url);

function resolveProtocolImport() {
  const override = process.env.ALTERMAGNETS_PROTOCOL_MJS;
  if (override) {
    if (!existsSync(override)) throw new Error(`ALTERMAGNETS_PROTOCOL_MJS does not exist: ${override}`);
    return pathToFileURL(override);
  }
  if (existsSync(siblingProtocol)) return siblingProtocol;
  const installed = spawnSync("python", ["-c", "from importlib.resources import files; print(files('httk.serve.web').joinpath('assets/serve-optimade-table-protocol.mjs'))"], { encoding: "utf8" });
  const installedPath = installed.status === 0 ? installed.stdout.trim() : "";
  if (installedPath && existsSync(installedPath)) return pathToFileURL(installedPath);
  throw new Error(`Unable to resolve serve-optimade-table-protocol.mjs; checked ${siblingProtocol.pathname} and the installed httk.serve.web package`);
}

const protocolImport = resolveProtocolImport();
installDom(new DomDocument());
globalThis.window = { location: { search: "" } };
globalThis.console = { error() {}, warn() {} };
const { OptimadeTransport } = await import(protocolImport.href);
const source = await readFile(new URL("../src/widgets/material-detail.mjs", import.meta.url), "utf8");
const material = await import(`data:text/javascript,${encodeURIComponent(source.replace('from "./serve-optimade-table-protocol.mjs";', `from ${JSON.stringify(protocolImport.href)};`))}`);

// Keep this list aligned with tests/test_material_widget.py::test_material_widget_requests_every_attribute_used_by_detail_js.
const fields = [
  "chemical_formula_reduced", "_anyterial_formula", "_anyterial_elements", "_anyterial_space_group", "_anyterial_classification",
  "_anyterial_electronic_type", "_anyterial_magnetic_phases", "_anyterial_wave_classes", "_anyterial_parent_spacegroups",
  "_anyterial_icsd_ids", "_httk_magndata_ids", "_httk_dft_band_gap",
  "_anyterial_max_spin_splitting", "_anyterial_avg_spin_splitting", "_anyterial_spin_splitting_fraction",
  "_anyterial_min_crustal_abundance", "_anyterial_magndata_variants", "_httk_custom_figures",
  "lattice_vectors", "cartesian_site_positions", "species", "species_at_sites", "_httk_site_moments",
];

function jsonResponse(value, url, status = 200) {
  const response = new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/vnd.api+json; charset=utf-8" },
  });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function textResponse(value, url) {
  const response = new Response(value, { headers: { "content-type": "text/csv; charset=utf-8" } });
  Object.defineProperty(response, "url", { value: url });
  return response;
}

function pageResponse(resource = null, included = []) {
  return { meta: { api_version: "1.3.0" }, data: resource, ...(included.length ? { included } : {}) };
}

function fetchFor(resource, included = []) {
  const requests = [];
  const fetch = async (request) => {
    const url = new URL(request);
    requests.push(url);
    if (url.pathname === "/optimade/amdb/versions") return textResponse("version\n1\n", url.href);
    if (url.pathname === "/optimade/amdb/v1/info") return jsonResponse({
      data: { id: "/", type: "info", attributes: {
        api_version: "1.3.0", formats: ["json"], entry_types_by_format: { json: ["structures"] },
        available_endpoints: ["info", "structures"],
      } },
    }, url.href);
    if (url.pathname === "/optimade/amdb/v1/info/structures") return jsonResponse({
      data: {
        id: "structures", type: "info", properties: Object.fromEntries(fields.map((name) => [name, {}])),
        formats: ["json"], output_fields_by_format: { json: fields },
      },
    }, url.href);
    if (url.pathname.startsWith("/optimade/amdb/v1/structures/")) {
      return jsonResponse(pageResponse(resource, included), url.href);
    }
    throw new Error(`unexpected URL ${url}`);
  };
  return { fetch, requests };
}

function shell(config = {}, search = `?id=${encodeURIComponent(MATERIAL_ID)}`) {
  const document = new DomDocument("https://site.example.test/material");
  const result = element(document, "section", { "data-site-material-detail": "1" });
  const configNode = element(document, "script", { type: "application/json" });
  configNode.textContent = JSON.stringify({ base_url: API, entry_type: "structures", id_query: "id", response_fields: fields, ...config });
  result.append(configNode);
  installDom(document);
  globalThis.window = { location: { search }, altermagnetsUi: { initSubtree() {} } };
  return { document, result };
}

const realisticResource = {
  id: MATERIAL_ID,
  type: "structures",
  attributes: {
    chemical_formula_reduced: "Fe2O3<script>alert(1)</script>",
    _anyterial_formula: null,
    _anyterial_elements: ["Fe", "O"],
    _anyterial_space_group: "P2_1/c",
    _anyterial_classification: "collinear",
    _anyterial_electronic_type: "semiconducting",
    _anyterial_magnetic_phases: ["AM"],
    _anyterial_wave_classes: ["d"],
    _anyterial_parent_spacegroups: ["P2_1/c"],
    _anyterial_icsd_ids: ["123"],
    _httk_magndata_ids: ["165.1"],
    _httk_dft_band_gap: 1.25,
    _anyterial_max_spin_splitting: 2.4,
    _anyterial_avg_spin_splitting: 0.8,
    _anyterial_spin_splitting_fraction: 0.2,
    _anyterial_min_crustal_abundance: 56.0,
    lattice_vectors: null,
    cartesian_site_positions: null,
    species: null,
    species_at_sites: null,
    _httk_site_moments: null,
    _anyterial_magndata_variants: [{
      magndata_id: "165.1", source: "collinear", formula: "Fe2O3<script>alert(1)</script>", phases: ["AM"], wave_classes: ["d"],
      symprec: 1e-5, bns_mcif_latex: ["P2_1/c"], g_laue_classes: ["m-3m"], h_laue_classes: ["4/mmm"],
      warnings: ["<script>alert(1)</script>"], notes: ["note <img src=x>"], reference_dois: ["10.1234/über"],
    }],
    _httk_custom_figures: [
      { key: "band", available: true, url: `${API}/extensions/figures/band.svg`, dark_url: `${API}/extensions/figures/dark-band.svg` },
      { key: "structure", available: true, url: "https://evil.example/structure.svg" },
      { key: "bz", available: true, url: "http://api.example.test/optimade/amdb/extensions/figures/bz.svg" },
    ],
  },
  relationships: { references: { data: [{ type: "references", id: "ref/1" }] } },
};

test("material detail renders payload, safe figures, links, references, and aggregated messages", async () => {
  const { document, result } = shell();
  const network = fetchFor(realisticResource, [{ type: "references", id: "ref/1", attributes: { doi: "10.5678/example" } }]);
  globalThis.fetch = network.fetch;
  await material.loadShell(result, OptimadeTransport);
  assert.match(result.textContent, /Fe_\{2\}O_\{3\}<script>alert\(1\)<\/script>/);
  assert.match(result.textContent, /MAGNDATA 165\.1/);
  assert.match(result.textContent, /Band structure/);
  assert.match(result.textContent, /10\.5678\/example/);
  assert.match(result.textContent, /<script>alert\(1\)<\/script>/);
  assert.match(result.textContent, /note <img src=x>/);
  assert.equal(result.querySelectorAll("img.theme-aware-figure").length, 1);
  assert.equal(result.querySelectorAll("a").some((link) => link.getAttribute("href") === "https://cryst.ehu.es/magndata/index.php?index=165.1"), true);
  assert.equal(result.querySelectorAll("a").some((link) => link.getAttribute("href") === "https://doi.org/10.1234/%C3%BCber"), true);
  assert.equal(result.innerHTML.includes("<script>alert(1)</script>"), false);
  assert.equal(result.innerHTML.includes('<img src="x">'), false);
  const oneRequest = network.requests.find((url) => url.pathname === `/optimade/amdb/v1/structures/${encodeURIComponent(MATERIAL_ID)}`);
  assert.ok(oneRequest, "the single-entry request was issued");
  assert.equal(oneRequest.searchParams.get("include"), "references");
  assert.ok(network.requests.some((url) => url.pathname.endsWith("/_httk_alts")), "the alternatives request was issued");
  assert.equal(document.baseURI, "https://site.example.test/material");
});

test("placeholder variants retain the MAGNDATA id and figures use missing placeholders", async () => {
  const resource = {
    id: "placeholder", type: "structures",
    attributes: { ...Object.fromEntries(fields.map((name) => [name, null])), chemical_formula_reduced: "MnO", _httk_magndata_ids: ["m-1"] },
  };
  const { result } = shell({ response_fields: fields });
  const network = fetchFor(resource);
  globalThis.fetch = network.fetch;
  await material.loadShell(result, OptimadeTransport);
  assert.match(result.textContent, /MAGNDATA m-1/);
  assert.match(result.textContent, /No symmetry table entry/);
  assert.equal(result.querySelectorAll("img.theme-aware-figure").length, 0);
  assert.equal(result.querySelectorAll("figure.is-missing").length, 3);
});

test("detail widget distinguishes no id, not found, and API-down states", async () => {
  const noId = shell({}, "");
  globalThis.fetch = async () => { throw new Error("must not fetch"); };
  await material.loadShell(noId.result, OptimadeTransport);
  assert.match(noId.result.textContent, /No material selected/);

  const missing = shell({}, "?id=missing");
  globalThis.fetch = fetchFor(null).fetch;
  await material.loadShell(missing.result, OptimadeTransport);
  assert.match(missing.result.textContent, /could not be found/);

  const down = shell({}, "?id=down");
  globalThis.fetch = async () => { throw new Error("offline"); };
  await material.loadShell(down.result, OptimadeTransport);
  assert.match(down.result.textContent, /temporarily unavailable/);
  assert.notEqual(noId.result.textContent, missing.result.textContent);
  assert.notEqual(missing.result.textContent, down.result.textContent);
});

test("field labels carry a native title hint starting with the OPTIMADE field name", async () => {
  const { result } = shell({
    field_info: {
      _httk_dft_band_gap: { description: "The Kohn-Sham band gap of a material." },
      _anyterial_electronic_type: {},
    },
  });
  const network = fetchFor(realisticResource);
  globalThis.fetch = network.fetch;
  await material.loadShell(result, OptimadeTransport);
  const labels = result.querySelectorAll("dt");
  const gap = labels.find((dt) => dt.textContent.startsWith("KS Gap"));
  assert.ok(gap, "KS Gap label is rendered");
  assert.equal(gap.title, "_httk_dft_band_gap — The Kohn-Sham band gap of a material.");
  const type = labels.find((dt) => dt.textContent.startsWith("KS Gap Type"));
  assert.ok(type, "KS Gap Type label is rendered");
  assert.equal(type.title, "_anyterial_electronic_type");
});

test("figure URL validation is origin-bound and rejects insecure mixed content", () => {
  const document = new DomDocument("https://site.example.test/material");
  installDom(document);
  assert.equal(material.figureUrl("https://api.example.test/figure.svg", API), "https://api.example.test/figure.svg");
  assert.equal(material.figureUrl("https://evil.example/figure.svg", API), "");
  assert.equal(material.figureUrl("http://api.example.test/figure.svg", API), "");
});

// --- CrysViz interactive structure embed ---

const CRYSVIZ_BASE = "https://crysviz.test/index.html";

function structureAttributes(overrides = {}) {
  return {
    ...Object.fromEntries(fields.map((name) => [name, null])),
    chemical_formula_reduced: "FeO",
    // Simple cubic 2 Å cell so cartesian -> fractional is exact and checkable.
    lattice_vectors: [[2, 0, 0], [0, 2, 0], [0, 0, 2]],
    cartesian_site_positions: [[0, 0, 0], [1, 1, 1]],
    species: [
      { name: "Fe", chemical_symbols: ["Fe"] },
      { name: "O", chemical_symbols: ["O"] },
    ],
    species_at_sites: ["Fe", "O"],
    _httk_site_moments: [[0, 0, 4.2], null],
    ...overrides,
  };
}

function decodeCrysvizSrc(src) {
  const encoded = src.split("#load-file=", 2)[1];
  const [name, content] = encoded.split("|");
  const b64 = decodeURIComponent(content);
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return { name: decodeURIComponent(name), payload: JSON.parse(new TextDecoder().decode(bytes)) };
}

test("crysvizPayload maps elements, fractional positions, spins and spinsActive", () => {
  const payload = material.crysvizPayload(structureAttributes());
  assert.equal(payload.format, "crysviz");
  assert.ok(payload.version.startsWith("2"));
  assert.equal(payload.selectedFrameIndex, 0);
  const frame = payload.frames[0];
  assert.deepEqual(frame.elements, ["Fe", "O"]);
  assert.deepEqual(frame.lattice, [[2, 0, 0], [0, 2, 0], [0, 0, 2]]);
  assert.deepEqual(frame.positions, [[0, 0, 0], [0.5, 0.5, 0.5]]);
  assert.equal(frame.spins.length, 2);
  assert.deepEqual(frame.spins[0], { vector: [0, 0, 4.2], atomIndex: 0 });
  assert.deepEqual(frame.spins[1], { vector: [0, 0, 0], atomIndex: 1 });
  assert.equal(payload.display.spinsActive, true);
});

test("crysvizPayload maps species labels to chemical symbols and drops spins when moments absent", () => {
  const payload = material.crysvizPayload(structureAttributes({
    species: [{ name: "Fe1", chemical_symbols: ["Fe"] }],
    species_at_sites: ["Fe1"],
    cartesian_site_positions: [[0, 0, 0]],
    _httk_site_moments: null,
  }));
  assert.deepEqual(payload.frames[0].elements, ["Fe"]);
  assert.equal("spins" in payload.frames[0], false);
  assert.equal(payload.display.spinsActive, false);
});

test("crysvizPayload returns null for a null or malformed lattice", () => {
  assert.equal(material.crysvizPayload(structureAttributes({ lattice_vectors: null })), null);
  assert.equal(material.crysvizPayload(structureAttributes({ lattice_vectors: [[0, 0, 0], [0, 0, 0], [0, 0, 0]] })), null);
  assert.equal(material.crysvizPayload(structureAttributes({ cartesian_site_positions: [[0, 0, 0]] })), null);
});

test("structure card renders a sandboxed CrysViz iframe when structure data is present", async () => {
  const resource = { id: "s-1", type: "structures", attributes: structureAttributes() };
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  globalThis.fetch = fetchFor(resource).fetch;
  await material.loadShell(result, OptimadeTransport);
  const frames = result.querySelectorAll("iframe.crysviz-frame");
  assert.equal(frames.length, 1);
  assert.equal(result.querySelectorAll("img.theme-aware-figure").length, 0);
  const frame = frames[0];
  assert.equal(frame.getAttribute("sandbox"), "allow-scripts allow-popups allow-popups-to-escape-sandbox");
  assert.equal(frame.getAttribute("referrerpolicy"), "no-referrer");
  assert.equal(frame.getAttribute("loading"), "lazy");
  assert.equal(frame.getAttribute("title"), "Interactive crystal structure (CrysViz)");
  const src = frame.getAttribute("src");
  assert.ok(src.startsWith(`${CRYSVIZ_BASE}?widget=1&theme=light#load-file=`));
  const { name, payload } = decodeCrysvizSrc(src);
  assert.ok(name.endsWith(".crysviz"));
  assert.deepEqual(payload.frames[0].positions, [[0, 0, 0], [0.5, 0.5, 0.5]]);
});

test("structure card falls back to the static figure when lattice_vectors is null", async () => {
  const resource = { id: "s-2", type: "structures", attributes: structureAttributes({ lattice_vectors: null }) };
  const { result } = shell();
  globalThis.fetch = fetchFor(resource).fetch;
  await material.loadShell(result, OptimadeTransport);
  assert.equal(result.querySelectorAll("iframe.crysviz-frame").length, 0);
  assert.equal(result.querySelectorAll("figure.is-missing").length, 3);
});

test("crysvizIframeSrc falls back (empty) when the encoded URL exceeds the guard", () => {
  const count = 6000;
  const big = structureAttributes({
    cartesian_site_positions: Array.from({ length: count }, () => [1, 1, 1]),
    species: [{ name: "Fe", chemical_symbols: ["Fe"] }],
    species_at_sites: Array.from({ length: count }, () => "Fe"),
    _httk_site_moments: Array.from({ length: count }, () => [0, 0, 1]),
  });
  assert.equal(material.crysvizIframeSrc(big, CRYSVIZ_BASE), "");
  assert.notEqual(material.crysvizIframeSrc(structureAttributes(), CRYSVIZ_BASE), "");
});

test("crysvizPayload rejects a species label that is not a bare element symbol", () => {
  // "Fe 2+" would corrupt the whitespace-delimited POSCAR rebuild → fallback.
  assert.equal(material.crysvizPayload(structureAttributes({
    species: [{ name: "Fe2+", chemical_symbols: ["Fe 2+"] }, { name: "O", chemical_symbols: ["O"] }],
    species_at_sites: ["Fe2+", "O"],
  })), null);
  // An unmapped label that is itself not a bare symbol also fails.
  assert.equal(material.crysvizPayload(structureAttributes({
    species: null,
    species_at_sites: ["Fe1", "O"],
  })), null);
});

test("crysvizIframeSrc keeps a query-carrying base valid and the hash raw", () => {
  const src = material.crysvizIframeSrc(structureAttributes(), "https://crysviz.test/index.html?embed=1");
  assert.ok(src.startsWith("https://crysviz.test/index.html?"));
  const query = src.split("#", 1)[0];
  assert.ok(query.includes("embed=1"));
  assert.ok(query.includes("widget=1"));
  assert.equal(src.split("?").length, 2); // exactly one "?" — no "?a=b?widget=1"
  // The load-file separator survives as a raw "|" between the two encoded halves.
  const hash = src.split("#load-file=", 2)[1];
  assert.equal(hash.split("|").length, 2);
});

// --- Alternative conventional/primitive cell frames ---

function altResource(id, positions) {
  return {
    id,
    type: "structures",
    attributes: {
      lattice_vectors: [[2, 0, 0], [0, 2, 0], [0, 0, 2]],
      cartesian_site_positions: positions,
      species: [{ name: "Fe", chemical_symbols: ["Fe"] }],
      species_at_sites: positions.map(() => "Fe"),
      _httk_site_moments: null,
    },
  };
}

// Wrap fetchFor so the per-group `_httk_alts` collection returns a valid page.
function fetchForWithAlts(resource, alternatives) {
  const base = fetchFor(resource);
  const fetch = async (request) => {
    const url = new URL(request);
    if (url.pathname.endsWith("/_httk_alts")) {
      base.requests.push(url);
      return jsonResponse({ meta: { api_version: "1.3.0" }, data: alternatives }, url.href);
    }
    return base.fetch(request);
  };
  return { fetch, requests: base.requests };
}

test("altKind parses the composite id suffix and rejects a non-identifier suffix", () => {
  assert.equal(material.altKind("anyt.am-1-1~conventional"), "conventional");
  assert.equal(material.altKind("anyt.am-1-1~primitive"), "primitive");
  assert.equal(material.altKind("anyt.am-1-1~a~b_2"), "b_2"); // suffix after the LAST "~"
  assert.equal(material.altKind("anyt.am-1-1"), null); // no suffix at all
  assert.equal(material.altKind("anyt.am-1-1~Conventional"), null); // uppercase is not a bare kind
  assert.equal(material.altKind("anyt.am-1-1~2d"), null); // must start with a letter
  assert.equal(material.altKind("anyt.am-1-1~"), null); // empty suffix
});

test("crysvizPayload appends alternative frames in the given order with frameKinds", () => {
  // crysvizPayload preserves the caller's order (fetchAlternatives is what sorts).
  const payload = material.crysvizPayload(structureAttributes(), [
    { kind: "conventional", attributes: altResource("x~conventional", [[0, 0, 0]]).attributes },
    { kind: "primitive", attributes: altResource("x~primitive", [[1, 1, 1]]).attributes },
  ]);
  assert.deepEqual(payload.frameKinds, ["loaded", "conventional", "primitive"]);
  assert.equal(payload.frames.length, 3);
  assert.deepEqual(payload.frames[1].positions, [[0, 0, 0]]);
  assert.deepEqual(payload.frames[2].positions, [[0.5, 0.5, 0.5]]);
  // A malformed alternative frame is skipped, not fatal.
  const partial = material.crysvizPayload(structureAttributes(), [
    { kind: "conventional", attributes: { lattice_vectors: null } },
    { kind: "primitive", attributes: altResource("x~primitive", [[1, 1, 1]]).attributes },
  ]);
  assert.deepEqual(partial.frameKinds, ["loaded", "primitive"]);
  // A single-frame payload (no alternatives) stays byte-identical: no frameKinds.
  assert.equal("frameKinds" in material.crysvizPayload(structureAttributes()), false);
});

test("structure card embeds conventional and primitive alternative frames with frameKinds", async () => {
  const resource = { id: MATERIAL_ID, type: "structures", attributes: structureAttributes() };
  const alternatives = [
    altResource(`${MATERIAL_ID}~primitive`, [[1, 1, 1]]),
    altResource(`${MATERIAL_ID}~conventional`, [[0, 0, 0]]),
  ];
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  const network = fetchForWithAlts(resource, alternatives);
  globalThis.fetch = network.fetch;
  await material.loadShell(result, OptimadeTransport);
  const alt = network.requests.find((url) => url.pathname.endsWith("/_httk_alts"));
  assert.ok(alt, "the alternatives collection was requested");
  assert.equal(alt.searchParams.get("page_limit"), "8");
  assert.equal(alt.searchParams.get("response_fields"), "lattice_vectors,cartesian_site_positions,species,species_at_sites,_httk_site_moments");
  const frame = result.querySelectorAll("iframe.crysviz-frame")[0];
  assert.ok(frame, "the structure iframe is present");
  const { payload } = decodeCrysvizSrc(frame.getAttribute("src"));
  assert.deepEqual(payload.frameKinds, ["loaded", "conventional", "primitive"]);
  assert.equal(payload.frames.length, 3);
  assert.deepEqual(payload.frames[1].positions, [[0, 0, 0]]);
  assert.deepEqual(payload.frames[2].positions, [[0.5, 0.5, 0.5]]);
  assert.equal(payload.display.spinsActive, true);
});

test("structure card falls back to a single loaded frame when the alternatives request fails", async () => {
  const resource = { id: MATERIAL_ID, type: "structures", attributes: structureAttributes() };
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  const network = fetchFor(resource);
  globalThis.fetch = async (request) => {
    const url = new URL(request);
    if (url.pathname.endsWith("/_httk_alts")) throw new Error("alternatives offline");
    return network.fetch(request);
  };
  await material.loadShell(result, OptimadeTransport);
  const frame = result.querySelectorAll("iframe.crysviz-frame")[0];
  assert.ok(frame, "the loaded structure still renders");
  const { payload } = decodeCrysvizSrc(frame.getAttribute("src"));
  assert.equal(payload.frames.length, 1);
  assert.equal("frameKinds" in payload, false);
});


test("crysvizIframeSrc carries the current page theme and mirrors app.js dark resolution", () => {
  const document = new DomDocument("https://site.example.test/material");
  installDom(document);
  // Absent data-theme resolves to light (matches app.js figure swapping).
  const light = material.crysvizIframeSrc(structureAttributes(), CRYSVIZ_BASE);
  assert.ok(light.split("#", 1)[0].includes("theme=light"));
  // Only the "dark" site theme is dark.
  document.documentElement.setAttribute("data-theme", "dark");
  const dark = material.crysvizIframeSrc(structureAttributes(), CRYSVIZ_BASE);
  assert.ok(dark.split("#", 1)[0].includes("theme=dark"));
  // "twilight" is a non-dark site theme, so the widget stays light.
  document.documentElement.setAttribute("data-theme", "twilight");
  assert.ok(material.crysvizIframeSrc(structureAttributes(), CRYSVIZ_BASE).split("#", 1)[0].includes("theme=light"));
  // The #load-file hash is still raw: exactly one "|" between the encoded halves.
  assert.equal(dark.split("#load-file=", 2)[1].split("|").length, 2);
});

test("structure iframe uses the document theme and follows a live theme toggle", async () => {
  const resource = { id: "s-1", type: "structures", attributes: structureAttributes() };
  const { document, result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  document.documentElement.setAttribute("data-theme", "dark");
  globalThis.fetch = fetchFor(resource).fetch;
  await material.loadShell(result, OptimadeTransport);
  const frame = result.querySelectorAll("iframe.crysviz-frame")[0];
  assert.ok(frame.getAttribute("src").split("#", 1)[0].includes("theme=dark"));
  // Toggle to a light theme: the MutationObserver rebuilds the src.
  document.documentElement.setAttribute("data-theme", "light");
  assert.ok(frame.getAttribute("src").split("#", 1)[0].includes("theme=light"));
});

// --- Dynamic structure download links ---

test("structureDownloadLinks builds same-origin CIF and POSCAR links off the /v1 sibling route", () => {
  installDom(new DomDocument("https://site.example.test/material"));
  const links = material.structureDownloadLinks("anyt.am-1-1", "https://api.example.test/optimade/amdb/v1");
  assert.deepEqual(links, [
    { label: "Download CIF", url: "https://api.example.test/optimade/amdb/extensions/figures/anyt.am-1-1/structure.cif" },
    { label: "Download POSCAR", url: "https://api.example.test/optimade/amdb/extensions/figures/anyt.am-1-1/POSCAR" },
  ]);
  // A trailing slash on the base must not leak the version segment into the path.
  const trailing = material.structureDownloadLinks("anyt.am-1-1", "https://api.example.test/optimade/amdb/v1/");
  assert.equal(trailing[0].url, "https://api.example.test/optimade/amdb/extensions/figures/anyt.am-1-1/structure.cif");
  assert.deepEqual(material.structureDownloadLinks("", "https://api.example.test/optimade/amdb/v1"), []);
});

test("crysvizPayload attaches structure download menuLinks only when a structure is present", () => {
  const links = [{ label: "Download CIF", url: "https://h/x/structure.cif" }];
  assert.deepEqual(material.crysvizPayload(structureAttributes(), [], links).menuLinks, links);
  // No structure → null payload, so no menuLinks anywhere.
  assert.equal(material.crysvizPayload(structureAttributes({ lattice_vectors: null }), [], links), null);
  // No links supplied → the key stays absent.
  assert.equal("menuLinks" in material.crysvizPayload(structureAttributes()), false);
});

test("structure iframe payload carries CIF and POSCAR download links from the discovered API base", async () => {
  const resource = { id: MATERIAL_ID, type: "structures", attributes: structureAttributes() };
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  globalThis.fetch = fetchFor(resource).fetch;
  await material.loadShell(result, OptimadeTransport);
  const frame = result.querySelectorAll("iframe.crysviz-frame")[0];
  const { payload } = decodeCrysvizSrc(frame.getAttribute("src"));
  const enc = encodeURIComponent(MATERIAL_ID);
  assert.deepEqual(payload.menuLinks, [
    { label: "Download CIF", url: `https://api.example.test/optimade/amdb/extensions/figures/${enc}/structure.cif` },
    { label: "Download POSCAR", url: `https://api.example.test/optimade/amdb/extensions/figures/${enc}/POSCAR` },
  ]);
});
