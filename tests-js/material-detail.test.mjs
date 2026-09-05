import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { DomDocument, element, installDom } from "./dom.mjs";

const API = "https://api.example.test/optimade/amdb";
const MATERIAL_ID = "anyt.am-1/0001";
// The AMDB main entity's served (wire) entry type — the detail page's primary endpoint.
const RESULT_TYPE = "_anyterial_altermagnet_screening_result";
// The slim structure record referenced by the result (include=structures target).
const STRUCTURE_ID = "anyt.am.structure-1-1";
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

// The science/figure/energy fields the detail page requests off the RESULT resource.
// Keep aligned with src/widgets/material_detail.py RESPONSE_FIELDS and the JS accesses.
const resultFields = [
  "_anyterial_formula", "_anyterial_elements", "_anyterial_space_group", "_anyterial_classification",
  "_anyterial_electronic_type", "_anyterial_magnetic_phases", "_anyterial_wave_classes",
  "_anyterial_parent_spacegroups", "_anyterial_icsd_ids", "_httk_magndata_ids", "_httk_dft_band_gap",
  "_anyterial_max_spin_splitting", "_anyterial_avg_spin_splitting", "_anyterial_spin_splitting_fraction",
  "_anyterial_min_crustal_abundance", "_anyterial_magndata_variants", "_httk_custom_figures",
];
// The five CrysViz structural fields the INCLUDED structure carries (+ the standard formula).
const structureFields = ["lattice_vectors", "cartesian_site_positions", "species", "species_at_sites", "_httk_site_moments", "chemical_formula_reduced"];

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

function infoRoot(types) {
  return { data: { id: "/", type: "info", attributes: {
    api_version: "1.3.0", formats: ["json"],
    entry_types_by_format: { json: types }, available_endpoints: ["info", ...types],
  } } };
}
function infoEntry(type, fieldList) {
  return { data: { id: type, type: "info", properties: Object.fromEntries(fieldList.map((n) => [n, {}])),
    formats: ["json"], output_fields_by_format: { json: fieldList } } };
}

// A RESULT resource carrying every requested science field (null defaults, overridable),
// plus the injected structures/references relationship blocks the detail page reads.
function resultResource(attrs = {}, { id = MATERIAL_ID, structureId = STRUCTURE_ID, references = [], relationships = {} } = {}) {
  const rel = { ...relationships };
  if (structureId) rel.structures = { data: [{ type: "structures", id: structureId }] };
  rel.references = { data: references };
  return {
    id, type: RESULT_TYPE,
    attributes: { ...Object.fromEntries(resultFields.map((n) => [n, null])), ...attrs },
    relationships: rel,
  };
}

// The INCLUDED slim structure resource carrying the CrysViz structural fields.
function structureResource(attrs = {}, id = STRUCTURE_ID) {
  return { id, type: "structures", attributes: attrs };
}

// Serves discovery for both the result and structures endpoints, the single-entry
// request (result + included), an empty alternatives page, and (when needed) the run.
function fetchFor(result, included = []) {
  const requests = [];
  const fetch = async (request) => {
    const url = new URL(request);
    requests.push(url);
    if (url.pathname === "/optimade/amdb/versions") return textResponse("version\n1\n", url.href);
    if (url.pathname === "/optimade/amdb/v1/info") return jsonResponse(infoRoot([RESULT_TYPE, "structures"]), url.href);
    if (url.pathname === `/optimade/amdb/v1/info/${RESULT_TYPE}`) return jsonResponse(infoEntry(RESULT_TYPE, resultFields), url.href);
    if (url.pathname === "/optimade/amdb/v1/info/structures") return jsonResponse(infoEntry("structures", structureFields), url.href);
    if (url.pathname.endsWith("/_httk_alts")) return jsonResponse(pageResponse([]), url.href);
    if (url.pathname.startsWith(`/optimade/amdb/v1/${RESULT_TYPE}/`)) return jsonResponse(pageResponse(result, included), url.href);
    throw new Error(`unexpected URL ${url}`);
  };
  return { fetch, requests };
}

function shell(config = {}, search = `?id=${encodeURIComponent(MATERIAL_ID)}`) {
  const document = new DomDocument("https://site.example.test/material");
  const result = element(document, "section", { "data-site-material-detail": "1" });
  const configNode = element(document, "script", { type: "application/json" });
  configNode.textContent = JSON.stringify({
    base_url: API, entry_type: RESULT_TYPE, id_query: "id", response_fields: resultFields, include: ["structures", "references"], ...config,
  });
  result.append(configNode);
  installDom(document);
  globalThis.window = { location: { search }, altermagnetsUi: { initSubtree() {} } };
  return { document, result };
}

const realisticResultAttributes = {
  _anyterial_formula: "Fe2O3<script>alert(1)</script>",
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
  _anyterial_magndata_variants: [{
    magndata_id: "165.1", source: "collinear", formula: "Fe2O3<script>alert(1)</script>", phases: ["AM"], wave_classes: ["d"],
    symprec: 1e-5, bns_mcif_latex: ["P2_1/c"], g_laue_classes: ["m-3m"], h_laue_classes: ["4/mmm"],
    warnings: ["<script>alert(1)</script>"], notes: ["note <img src=x>"], reference_dois: ["10.1234/über"],
  }],
  _httk_custom_figures: [
    { key: "band", available: true, url: `${API}/extensions/files/band.svg`, dark_url: `${API}/extensions/files/dark-band.svg` },
    { key: "structure", available: true, url: "https://evil.example/structure.svg" },
    { key: "bz", available: true, url: "http://api.example.test/optimade/amdb/extensions/files/bz.svg" },
  ],
};

test("material detail renders payload, safe figures, links, references, and aggregated messages", async () => {
  const { result } = shell();
  // The included structure has a null lattice, so the structure card degrades to the
  // static figure while the alternatives request (keyed on the structure id) still fires.
  const resource = resultResource(realisticResultAttributes, { references: [{ type: "references", id: "ref/1" }] });
  const included = [
    structureResource(structureAttributes({ lattice_vectors: null })),
    { type: "references", id: "ref/1", attributes: { doi: "10.5678/example" } },
  ];
  const network = fetchFor(resource, included);
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
  // The single-entry request goes to the RESULT endpoint and inlines both relationships.
  const oneRequest = network.requests.find((url) => url.pathname === `/optimade/amdb/v1/${RESULT_TYPE}/${encodeURIComponent(MATERIAL_ID)}`);
  assert.ok(oneRequest, "the single-entry request was issued against the result endpoint");
  assert.equal(oneRequest.searchParams.get("include"), "structures,references");
  // The alternatives request re-keys to the INCLUDED structure id, not the result id.
  const alt = network.requests.find((url) => url.pathname.endsWith("/_httk_alts"));
  assert.ok(alt, "the alternatives request was issued");
  assert.ok(alt.pathname.includes(encodeURIComponent(STRUCTURE_ID)), "alternatives key on the structure id");
});

test("placeholder variants retain the MAGNDATA id and figures use missing placeholders", async () => {
  // No structure included and no figures → all three figure cards render the placeholder.
  const resource = resultResource({ _httk_magndata_ids: ["m-1"] }, { structureId: null });
  const { result } = shell();
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
  const network = fetchFor(resultResource(realisticResultAttributes));
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

// --- include=structures: the CrysViz payload rides in via the included structure ---

test("includedStructure resolves the structures relationship against the included section", () => {
  const resource = resultResource({});
  const structure = structureResource(structureAttributes());
  assert.equal(material.includedStructure(resource, [structure]).id, STRUCTURE_ID);
  // No structures block → null (degrades to the static figure).
  assert.equal(material.includedStructure(resultResource({}, { structureId: null }), [structure]), null);
  // Block present but the resource is absent from included → null.
  assert.equal(material.includedStructure(resource, []), null);
});

// --- CrysViz interactive structure embed ---

const CRYSVIZ_BASE = "https://crysviz.test/index.html";

function structureAttributes(overrides = {}) {
  return {
    ...Object.fromEntries(structureFields.map((name) => [name, null])),
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

test("structure card renders a sandboxed CrysViz iframe when the included structure has data", async () => {
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  globalThis.fetch = fetchFor(resultResource({}), [structureResource(structureAttributes())]).fetch;
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

test("structure card falls back to the static figure when the included structure lattice is null", async () => {
  const { result } = shell();
  globalThis.fetch = fetchFor(resultResource({}), [structureResource(structureAttributes({ lattice_vectors: null }))]).fetch;
  await material.loadShell(result, OptimadeTransport);
  assert.equal(result.querySelectorAll("iframe.crysviz-frame").length, 0);
  assert.equal(result.querySelectorAll("figure.is-missing").length, 3);
});

test("structure card falls back to the static figure when no structure is included", async () => {
  const { result } = shell();
  globalThis.fetch = fetchFor(resultResource({}, { structureId: null })).fetch;
  await material.loadShell(result, OptimadeTransport);
  assert.equal(result.querySelectorAll("iframe.crysviz-frame").length, 0);
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
function fetchForWithAlts(result, included, alternatives) {
  const base = fetchFor(result, included);
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

test("structure card embeds conventional and primitive alternative frames re-keyed to the structure id", async () => {
  const alternatives = [
    altResource(`${STRUCTURE_ID}~primitive`, [[1, 1, 1]]),
    altResource(`${STRUCTURE_ID}~conventional`, [[0, 0, 0]]),
  ];
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  const network = fetchForWithAlts(resultResource({}), [structureResource(structureAttributes())], alternatives);
  globalThis.fetch = network.fetch;
  await material.loadShell(result, OptimadeTransport);
  const alt = network.requests.find((url) => url.pathname.endsWith("/_httk_alts"));
  assert.ok(alt, "the alternatives collection was requested");
  // Alternatives re-key to the INCLUDED structure id, not the result/material id.
  assert.ok(alt.pathname.includes(encodeURIComponent(STRUCTURE_ID)));
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
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  const network = fetchFor(resultResource({}), [structureResource(structureAttributes())]);
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
  const { document, result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  document.documentElement.setAttribute("data-theme", "dark");
  globalThis.fetch = fetchFor(resultResource({}), [structureResource(structureAttributes())]).fetch;
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
  const links = material.structureDownloadLinks("anyt.am.structure-1-1", "https://api.example.test/optimade/amdb/v1");
  assert.deepEqual(links, [
    { label: "Download CIF", url: "https://api.example.test/optimade/amdb/extensions/files/anyt.am.structure-1-1/structure.cif" },
    { label: "Download POSCAR", url: "https://api.example.test/optimade/amdb/extensions/files/anyt.am.structure-1-1/POSCAR" },
  ]);
  // A trailing slash on the base must not leak the version segment into the path.
  const trailing = material.structureDownloadLinks("anyt.am.structure-1-1", "https://api.example.test/optimade/amdb/v1/");
  assert.equal(trailing[0].url, "https://api.example.test/optimade/amdb/extensions/files/anyt.am.structure-1-1/structure.cif");
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

test("structure iframe payload carries CIF and POSCAR download links re-keyed to the structure id", async () => {
  const { result } = shell({ crysviz_base_url: CRYSVIZ_BASE });
  globalThis.fetch = fetchFor(resultResource({}), [structureResource(structureAttributes())]).fetch;
  await material.loadShell(result, OptimadeTransport);
  const frame = result.querySelectorAll("iframe.crysviz-frame")[0];
  const { payload } = decodeCrysvizSrc(frame.getAttribute("src"));
  const enc = encodeURIComponent(STRUCTURE_ID);
  assert.deepEqual(payload.menuLinks, [
    { label: "Download CIF", url: `https://api.example.test/optimade/amdb/extensions/files/${enc}/structure.cif` },
    { label: "Download POSCAR", url: `https://api.example.test/optimade/amdb/extensions/files/${enc}/POSCAR` },
  ]);
});

// --- Provenance section (records-based relationships off the RESULT resource) ---

const CALC_RECORD_ID = "anyt.am.records-1-7";
const SCREENING_RECORD_ID = "anyt.am.records-1-9";
const RUN_ID = "anyt.am.runs-1-1";
const RECORD_FIELDS = ["_httk_total_energy"];
const RUN_FIELDS = ["_httk_workflow_declaration_uri", "_httk_source_id"];
const RESULT_WITH_RUN_ID = "anyt.am-1-7";
const STRUCTURE_EDGE_ID = "anyt.am.structure-1-7";
const FILE_EDGE_ID = "file-hash-000";
const WORKFLOW_URI = "https://schemas.httk.org/defs/v0.1/workflows/x";

// The result's `_httk_records` relationship: a screening_result_record entry is served
// for every material; the calculation_output_record entry only for run-coupled ones.
function recordsRelationship(calcRecordId = null) {
  const data = [{ type: "_httk_records", id: SCREENING_RECORD_ID, meta: { role: "screening_result_record" } }];
  if (calcRecordId) data.push({ type: "_httk_records", id: calcRecordId, meta: { role: "calculation_output_record" } });
  return { data };
}

// The coupled RESULT resource: its `_httk_records` block names the calculation record.
function resultWithRun(extra = {}) {
  return {
    id: RESULT_WITH_RUN_ID, type: RESULT_TYPE,
    attributes: { ...extra },
    relationships: {
      structures: { data: [{ type: "structures", id: STRUCTURE_EDGE_ID }] },
      references: { data: [] },
      _httk_records: recordsRelationship(CALC_RECORD_ID),
    },
  };
}

// The calculation record: total energy plus the reverse block naming the producing run.
function recordResource(relationships = {}, attrs = {}) {
  return { id: CALC_RECORD_ID, type: "_httk_records", attributes: { _httk_total_energy: -1.0, ...attrs }, relationships };
}
const RECORD_WITH_RUN = recordResource({
  _httk_is_output: { data: [{ type: "_httk_runs", id: RUN_ID, meta: { role: "output", _httk_label: "total_energy" } }] },
  _httk_is_artifact: { data: [{ type: "_httk_runs", id: RUN_ID, meta: { role: "artifact", _httk_label: "total_energy" } }] },
});

// The run's forward edges: relaxed structure, the calculation record itself, and an
// output file. `_httk_has_output` and `_httk_has_artifact` name IDENTICAL target sets
// on the wire — there is no result-typed edge any more.
function forwardEdges(role) {
  return [
    { type: "structures", id: STRUCTURE_EDGE_ID, meta: { role, _httk_label: "relaxed_structure" } },
    { type: "_httk_records", id: CALC_RECORD_ID, meta: { role, _httk_label: "total_energy" } },
    { type: "files", id: FILE_EDGE_ID, meta: { role, _httk_label: "vasprun" } },
  ];
}
const RUN_RESOURCE = {
  id: RUN_ID, type: "_httk_runs",
  attributes: { _httk_source_id: "httk-v1:abc", _httk_workflow_declaration_uri: WORKFLOW_URI },
  relationships: {
    _httk_has_output: { data: forwardEdges("output") },
    _httk_has_artifact: { data: forwardEdges("artifact") },
  },
};

// The produced model built from RUN_RESOURCE (`_httk_has_output` is read; `_httk_has_artifact`
// is never consulted when the output block is present).
const EXPECTED_PRODUCED = [
  { type: "structures", id: STRUCTURE_EDGE_ID, label: "relaxed_structure" },
  { type: "_httk_records", id: CALC_RECORD_ID, label: "total_energy" },
  { type: "files", id: FILE_EDGE_ID, label: "vasprun" },
];

// A fake transport answering the direct `_httk_records/<id>` and `_httk_runs/<id>` GETs
// (fetchOne) without the network; the batched files lookup still uses fetchPage. No
// route ever accepts a `filter`-based lookup for records or runs.
class FakeRecordRunTransport {
  constructor(config) { this.config = config; FakeRecordRunTransport.configs.push(config); }
  async fetchOne(id) {
    FakeRecordRunTransport.fetched.push({ entryType: this.config.entry_type, id });
    if (this.config.entry_type === "_httk_records") return { resource: FakeRecordRunTransport.record };
    if (this.config.entry_type === "_httk_runs") return { resource: FakeRecordRunTransport.run };
    throw new Error(`unexpected fetchOne entry_type ${this.config.entry_type}`);
  }
  async fetchPage({ filter }) {
    FakeRecordRunTransport.filters.push(filter);
    if (this.config.entry_type === "files") return { resources: [] };
    throw new Error(`unexpected fetchPage entry_type ${this.config.entry_type}`);
  }
}
function resetFakeTransport(record, run) {
  FakeRecordRunTransport.configs = [];
  FakeRecordRunTransport.filters = [];
  FakeRecordRunTransport.fetched = [];
  FakeRecordRunTransport.record = record;
  FakeRecordRunTransport.run = run;
}

test("fetchProvenance follows the calculation_output_record relationship to the record, then the record's reverse block to the run", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  resetFakeTransport(RECORD_WITH_RUN, RUN_RESOURCE);
  const obj = await material.fetchProvenance(FakeRecordRunTransport, { base_url: API }, resultWithRun());
  // Direct id fetches only — no filter route for records or runs, ever.
  assert.deepEqual(FakeRecordRunTransport.fetched, [
    { entryType: "_httk_records", id: CALC_RECORD_ID },
    { entryType: "_httk_runs", id: RUN_ID },
  ]);
  assert.deepEqual(FakeRecordRunTransport.configs[0], { base_url: API, entry_type: "_httk_records", response_fields: RECORD_FIELDS, page_size: 1 });
  assert.deepEqual(FakeRecordRunTransport.configs[1], { base_url: API, entry_type: "_httk_runs", response_fields: RUN_FIELDS, page_size: 1 });
  // The only fetchPage call is the batched files lookup.
  assert.deepEqual(FakeRecordRunTransport.filters, [`id="${FILE_EDGE_ID}"`]);
  assert.equal(obj.calcRecordId, CALC_RECORD_ID);
  assert.equal(obj.sourceId, "httk-v1:abc");
  assert.equal(obj.workflowUri, WORKFLOW_URI);
  assert.equal(obj.totalEnergy, -1.0);
  assert.deepEqual(obj.produced, EXPECTED_PRODUCED);
});

test("fetchProvenance is null with no calculation_output_record entry, and issues no request at all", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  class ThrowsIfConstructed {
    constructor() { throw new Error("must not be instantiated"); }
  }
  const noRecords = { id: "x", attributes: {}, relationships: {} };
  assert.equal(await material.fetchProvenance(ThrowsIfConstructed, { base_url: API }, noRecords), null);
  // A `_httk_records` block with only the screening_result_record role is the same as none.
  const screeningOnly = { id: "x", attributes: {}, relationships: { _httk_records: recordsRelationship(null) } };
  assert.equal(await material.fetchProvenance(ThrowsIfConstructed, { base_url: API }, screeningOnly), null);
});

test("fetchProvenance degrades to the energy line alone when the record has no run block, issuing no run request", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  resetFakeTransport(recordResource({}, { _httk_total_energy: -2.5 }), null);
  const obj = await material.fetchProvenance(FakeRecordRunTransport, { base_url: API }, resultWithRun());
  assert.deepEqual(FakeRecordRunTransport.fetched, [{ entryType: "_httk_records", id: CALC_RECORD_ID }]);
  assert.equal(obj.totalEnergy, -2.5);
  assert.equal(obj.workflowUri, null);
  assert.equal(obj.sourceId, null);
  assert.deepEqual(obj.produced, []);
});

test("fetchProvenance degrades to no energy line when the record fetch fails, issuing no run request", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  class RecordFailsTransport {
    constructor(config) { this.config = config; }
    async fetchOne() {
      RecordFailsTransport.fetchedTypes.push(this.config.entry_type);
      throw new Error("_httk_records endpoint offline");
    }
    async fetchPage() { throw new Error("must not be called"); }
  }
  RecordFailsTransport.fetchedTypes = [];
  const obj = await material.fetchProvenance(RecordFailsTransport, { base_url: API }, resultWithRun());
  assert.deepEqual(RecordFailsTransport.fetchedTypes, ["_httk_records"]);
  assert.equal(obj.totalEnergy, null);
  assert.equal(obj.workflowUri, null);
  assert.equal(obj.sourceId, null);
  assert.deepEqual(obj.produced, []);
});

test("fetchProvenance degrades to the energy line alone when the run fetch fails", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  class RunFailsTransport {
    constructor(config) { this.config = config; }
    async fetchOne() {
      if (this.config.entry_type === "_httk_records") return { resource: RECORD_WITH_RUN };
      throw new Error("_httk_runs endpoint offline");
    }
    async fetchPage() { throw new Error("must not be called"); }
  }
  const obj = await material.fetchProvenance(RunFailsTransport, { base_url: API }, resultWithRun());
  assert.equal(obj.totalEnergy, -1.0);
  assert.equal(obj.workflowUri, null);
  assert.equal(obj.sourceId, null);
  assert.deepEqual(obj.produced, []);
});

test("buildProvenance renders the calculation record as the (this material) non-link entry, and no produced entry is ever a link", () => {
  installDom(new DomDocument("https://site.example.test/material"));
  const obj = { calcRecordId: CALC_RECORD_ID, workflowUri: WORKFLOW_URI, sourceId: "httk-v1:abc", totalEnergy: -12.5, produced: EXPECTED_PRODUCED };
  const section = material.buildProvenance(obj);
  assert.ok(section);
  assert.match(section.textContent, /Provenance/);
  assert.match(section.textContent, /httk-v1:abc/);
  assert.ok(section.querySelectorAll("a").some((a) => a.getAttribute("href") === WORKFLOW_URI));
  assert.match(section.textContent, /-12\.500000/);
  // No produced entry is ever a real material-page link any more.
  assert.equal(section.querySelectorAll("a.provenance-produced-link").length, 0);
  const entries = section.querySelectorAll("span.provenance-produced-entry");
  assert.deepEqual(entries.map((s) => s.textContent.replace(/\s*\(this material\)$/, "")), ["structure", "record", "file"]);
  assert.deepEqual(entries.map((s) => s.title), [STRUCTURE_EDGE_ID, CALC_RECORD_ID, FILE_EDGE_ID]);
  // Only the calculation-record entry carries the current-material annotation.
  const current = entries.find((s) => s.title === CALC_RECORD_ID);
  assert.ok(current.className.includes("is-current"));
  assert.match(current.textContent, /\(this material\)/);
  entries.filter((s) => s.title !== CALC_RECORD_ID).forEach((s) => {
    assert.equal(s.className.includes("is-current"), false);
    assert.doesNotMatch(s.textContent, /\(this material\)/);
  });
  // Edge labels render as muted annotations.
  assert.match(section.textContent, /relaxed_structure/);
  assert.match(section.textContent, /total_energy/);
  // Energy-only object (no produced entries) still renders the scalar and no produced list.
  const energyOnly = material.buildProvenance({ calcRecordId: "x", workflowUri: null, sourceId: null, totalEnergy: -1.0, produced: [] });
  assert.match(energyOnly.textContent, /-1\.000000/);
  assert.equal(energyOnly.querySelectorAll("ul.provenance-produced").length, 0);
  // Null object → no section.
  assert.equal(material.buildProvenance(null), null);
});

// --- Produced files as real download links (batched files endpoint) ---

const FILE_URL = "https://api.example.test/optimade/amdb/extensions/files/entry/file-hash-000";

test("fetchProvenance turns produced files into download links via ONE batched files request", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  class FileAwareTransport {
    constructor(config) { this.config = config; FileAwareTransport.configs.push(config); }
    async fetchOne() {
      if (this.config.entry_type === "_httk_records") return { resource: RECORD_WITH_RUN };
      return { resource: RUN_RESOURCE };
    }
    async fetchPage({ filter }) {
      FileAwareTransport.filters.push(filter);
      return { resources: [{ id: FILE_EDGE_ID, type: "files", attributes: { name: "vasprun.xml", url: FILE_URL, size: 20480 } }] };
    }
  }
  FileAwareTransport.configs = [];
  FileAwareTransport.filters = [];
  const obj = await material.fetchProvenance(FileAwareTransport, { base_url: API }, resultWithRun());
  assert.deepEqual(FileAwareTransport.filters, [`id="${FILE_EDGE_ID}"`]);
  const filesConfig = FileAwareTransport.configs.find((c) => c.entry_type === "files");
  assert.deepEqual(filesConfig, { base_url: API, entry_type: "files", response_fields: ["name", "url", "size"], page_size: 1 });
  const file = obj.produced.find((item) => item.type === "files");
  assert.deepEqual(file.file, { name: "vasprun.xml", url: FILE_URL, size: 20480 });
  assert.equal("file" in obj.produced.find((item) => item.type === "_httk_records"), false);
  const section = material.buildProvenance(obj);
  const links = section.querySelectorAll("a.provenance-produced-file");
  assert.equal(links.length, 1);
  assert.equal(links[0].getAttribute("href"), FILE_URL);
  assert.match(links[0].textContent, /vasprun\.xml/);
  assert.match(links[0].textContent, /20\.0 kB/);
  assert.equal(links[0].title, FILE_EDGE_ID);
  // The record and structure still render as non-link entries; the file no longer does.
  const entries = section.querySelectorAll("span.provenance-produced-entry");
  assert.deepEqual(entries.map((s) => s.title), [STRUCTURE_EDGE_ID, CALC_RECORD_ID]);
});

test("fetchProvenance leaves produced files as non-link entries when the batched request fails", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  class FilesFailTransport {
    constructor(config) { this.config = config; }
    async fetchOne() {
      if (this.config.entry_type === "_httk_records") return { resource: RECORD_WITH_RUN };
      return { resource: RUN_RESOURCE };
    }
    async fetchPage() { throw new Error("files endpoint offline"); }
  }
  const obj = await material.fetchProvenance(FilesFailTransport, { base_url: API }, resultWithRun());
  const file = obj.produced.find((item) => item.type === "files");
  assert.equal("file" in file, false);
  const section = material.buildProvenance(obj);
  assert.equal(section.querySelectorAll("a.provenance-produced-file").length, 0);
  const fileSpan = section.querySelectorAll("span.provenance-produced-entry").find((s) => s.title === FILE_EDGE_ID);
  assert.ok(fileSpan);
  assert.equal(fileSpan.textContent, "file");
});

test("fetchProvenance renders a mixed link/non-link list when the files batch is partial", async () => {
  installDom(new DomDocument("https://site.example.test/material"));
  const twoFileRun = {
    id: RUN_ID, type: "_httk_runs",
    attributes: { _httk_source_id: "httk-v1:abc", _httk_workflow_declaration_uri: WORKFLOW_URI },
    relationships: {
      _httk_has_output: { data: [
        { type: "files", id: "file-a", meta: { _httk_label: "a" } },
        { type: "files", id: "file-b", meta: { _httk_label: "b" } },
      ] },
    },
  };
  const fileAUrl = "https://api.example.test/optimade/amdb/extensions/files/entry/file-a";
  class PartialTransport {
    constructor(config) { this.config = config; }
    async fetchOne() {
      if (this.config.entry_type === "_httk_records") return { resource: RECORD_WITH_RUN };
      return { resource: twoFileRun };
    }
    async fetchPage({ filter }) {
      PartialTransport.filesFilter = filter;
      return { resources: [{ id: "file-a", type: "files", attributes: { name: "a.txt", url: fileAUrl, size: 10 } }] };
    }
  }
  const obj = await material.fetchProvenance(PartialTransport, { base_url: API }, resultWithRun());
  assert.equal(PartialTransport.filesFilter, `id="file-a" OR id="file-b"`);
  const section = material.buildProvenance(obj);
  const links = section.querySelectorAll("a.provenance-produced-file");
  assert.equal(links.length, 1);
  assert.equal(links[0].getAttribute("href"), fileAUrl);
  assert.equal(links[0].title, "file-a");
  const spans = section.querySelectorAll("span.provenance-produced-entry");
  assert.deepEqual(spans.map((s) => s.title), ["file-b"]);
});

// Serves discovery, the result single-entry request, and the direct `_httk_records/<id>` /
// `_httk_runs/<id>` GETs (fetchOne) — no filter route is ever mounted for either.
function runAwareFetch(result, included = []) {
  return async (request) => {
    const url = new URL(request);
    if (url.pathname === "/optimade/amdb/versions") return textResponse("version\n1\n", url.href);
    if (url.pathname === "/optimade/amdb/v1/info") return jsonResponse(infoRoot([RESULT_TYPE, "structures", "_httk_records", "_httk_runs"]), url.href);
    if (url.pathname === `/optimade/amdb/v1/info/${RESULT_TYPE}`) return jsonResponse(infoEntry(RESULT_TYPE, resultFields), url.href);
    if (url.pathname === "/optimade/amdb/v1/info/structures") return jsonResponse(infoEntry("structures", structureFields), url.href);
    if (url.pathname === "/optimade/amdb/v1/info/_httk_records") return jsonResponse(infoEntry("_httk_records", RECORD_FIELDS), url.href);
    if (url.pathname === "/optimade/amdb/v1/info/_httk_runs") return jsonResponse(infoEntry("_httk_runs", RUN_FIELDS), url.href);
    if (url.pathname === `/optimade/amdb/v1/_httk_records/${encodeURIComponent(CALC_RECORD_ID)}`) return jsonResponse(pageResponse(RECORD_WITH_RUN), url.href);
    if (url.pathname === `/optimade/amdb/v1/_httk_runs/${encodeURIComponent(RUN_ID)}`) return jsonResponse(pageResponse(RUN_RESOURCE), url.href);
    if (url.pathname.endsWith("/_httk_alts")) return jsonResponse(pageResponse([]), url.href);
    if (url.pathname.startsWith(`/optimade/amdb/v1/${RESULT_TYPE}/`)) return jsonResponse(pageResponse(result, included), url.href);
    throw new Error(`unexpected URL ${url}`);
  };
}

test("detail page appends a live Provenance section via direct record/run GETs, and omits it when absent", async () => {
  const withRun = resultWithRun({ ...Object.fromEntries(resultFields.map((n) => [n, null])), _anyterial_formula: "CrSb" });
  const shown = shell({}, `?id=${encodeURIComponent(RESULT_WITH_RUN_ID)}`);
  const requests = [];
  const network = runAwareFetch(withRun, [structureResource(structureAttributes({ lattice_vectors: null }), STRUCTURE_EDGE_ID)]);
  globalThis.fetch = async (request) => { requests.push(new URL(request)); return network(request); };
  await material.loadShell(shown.result, OptimadeTransport);
  assert.match(shown.result.textContent, /Provenance/);
  assert.match(shown.result.textContent, /httk-v1:abc/);
  assert.match(shown.result.textContent, /relaxed_structure/);
  assert.match(shown.result.textContent, /-1\.000000/);
  // The record and run were located by direct id GETs.
  assert.ok(requests.some((u) => u.pathname === `/optimade/amdb/v1/_httk_records/${encodeURIComponent(CALC_RECORD_ID)}`), "the calculation record was fetched directly by id");
  assert.ok(requests.some((u) => u.pathname === `/optimade/amdb/v1/_httk_runs/${encodeURIComponent(RUN_ID)}`), "the run was fetched directly by id");
  // No `_httk_runs` or `_httk_records` request ever carries a `filter` query parameter.
  assert.equal(requests.some((u) => (u.pathname === "/optimade/amdb/v1/_httk_runs" || u.pathname === "/optimade/amdb/v1/_httk_records") && u.searchParams.has("filter")), false);
  // No produced entry is ever a real material-page link.
  assert.equal(shown.result.querySelectorAll("a.provenance-produced-link").length, 0);

  const noProv = resultResource({ _anyterial_formula: "CrSb" }, { id: MATERIAL_ID });
  const hidden = shell();
  globalThis.fetch = fetchFor(noProv).fetch;
  await material.loadShell(hidden.result, OptimadeTransport);
  assert.doesNotMatch(hidden.result.textContent, /Provenance/);
});
