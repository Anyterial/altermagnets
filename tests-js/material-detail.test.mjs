import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { pathToFileURL } from "node:url";

import { OptimadeTransport } from "/home/rar/Documents/containers/devel/agents/httk2/httk-serve/src/httk/serve/web/assets/serve-optimade-table-protocol.mjs";
import { DomDocument, element, installDom } from "./dom.mjs";

const API = "https://api.example.test/optimade";
const MATERIAL_ID = "anyt:am-1/0001";
const protocolImport = pathToFileURL("/home/rar/Documents/containers/devel/agents/httk2/httk-serve/src/httk/serve/web/assets/serve-optimade-table-protocol.mjs").href;
installDom(new DomDocument());
globalThis.window = { location: { search: "" } };
globalThis.console = { error() {}, warn() {} };
const source = await readFile(new URL("../src/widgets/material-detail.mjs", import.meta.url), "utf8");
const material = await import(`data:text/javascript,${encodeURIComponent(source.replace('from "./serve-optimade-table-protocol.mjs";', `from ${JSON.stringify(protocolImport)};`))}`);

const fields = [
  "chemical_formula_reduced", "_anyterial_formula", "_anyterial_space_group", "_anyterial_classification",
  "_anyterial_electronic_type", "_anyterial_magnetic_phases", "_anyterial_wave_classes", "_anyterial_parent_spacegroups",
  "_anyterial_icsd_ids", "_httk_magndata_ids", "_httk_dft_band_gap", "_httk_magnetic_space_group_bns",
  "_anyterial_max_spin_splitting", "_anyterial_avg_spin_splitting", "_anyterial_spin_splitting_fraction",
  "_anyterial_min_crustal_abundance", "_anyterial_magndata_variants", "_anyterial_figures",
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
    if (url.pathname === "/optimade/versions") return textResponse("version\n1\n", url.href);
    if (url.pathname === "/optimade/v1/info") return jsonResponse({
      data: { id: "/", type: "info", attributes: {
        api_version: "1.3.0", formats: ["json"], entry_types_by_format: { json: ["structures"] },
        available_endpoints: ["info", "structures"],
      } },
    }, url.href);
    if (url.pathname === "/optimade/v1/info/structures") return jsonResponse({
      data: {
        id: "structures", type: "info", properties: Object.fromEntries(fields.map((name) => [name, {}])),
        formats: ["json"], output_fields_by_format: { json: fields },
      },
    }, url.href);
    if (url.pathname.startsWith("/optimade/v1/structures/")) {
      const complete = resource && { ...resource, attributes: { ...Object.fromEntries(fields.map((name) => [name, null])), ...resource.attributes } };
      return jsonResponse(pageResponse(complete, included), url.href);
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
    _anyterial_figures: [
      { key: "band", available: true, url: `${API}/figures/band.svg`, dark_url: `${API}/figures/dark-band.svg` },
      { key: "structure", available: true, url: "https://evil.example/structure.svg" },
      { key: "bz", available: true, url: "http://api.example.test/optimade/figures/bz.svg" },
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
  assert.equal(network.requests.at(-1).searchParams.get("include"), "references");
  assert.equal(network.requests.at(-1).pathname, `/optimade/v1/structures/${encodeURIComponent(MATERIAL_ID)}`);
  assert.equal(document.baseURI, "https://site.example.test/material");
});

test("placeholder variants retain the MAGNDATA id and figures use missing placeholders", async () => {
  const resource = { id: "placeholder", type: "structures", attributes: { chemical_formula_reduced: "MnO", _httk_magndata_ids: ["m-1"] } };
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

test("figure URL validation is origin-bound and rejects insecure mixed content", () => {
  const document = new DomDocument("https://site.example.test/material");
  installDom(document);
  assert.equal(material.figureUrl("https://api.example.test/figure.svg", API), "https://api.example.test/figure.svg");
  assert.equal(material.figureUrl("https://evil.example/figure.svg", API), "");
  assert.equal(material.figureUrl("http://api.example.test/figure.svg", API), "");
});
