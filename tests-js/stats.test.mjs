import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { DomDocument, element, installDom } from "./dom.mjs";

const SOURCE = new URL("../src/widgets/stats.mjs", import.meta.url);
const names = ["total", "collinear", "noncollinear-derived", "semiconducting"];

const tick = () => new Promise((resolve) => setImmediate(resolve));

function statsDocument(baseUrl = "https://api.example.test/optimade/amdb") {
  const document = new DomDocument();
  const config = element(document, "script", { id: "site-stats-test-config", type: "application/json" }, JSON.stringify({ base_url: baseUrl }));
  document.append(config);
  const targets = Object.fromEntries(names.map((name) => {
    const target = element(document, "span", { "data-site-stat": name }, "—");
    document.append(target);
    return [name, target];
  }));
  return { document, targets };
}

async function importStats(document, fetch, suffix) {
  installDom(document);
  globalThis.fetch = fetch;
  await import(`${SOURCE.href}?${suffix}`);
  await tick();
}

test("stats fills all placeholders from meta.data_returned", async () => {
  const { document, targets } = statsDocument();
  let call = 0;
  const requests = [];
  await importStats(document, async (request) => {
    requests.push(new URL(request));
    // data_available is the unfiltered endpoint total (ignored); data_returned is the filtered count.
    const response = new Response(JSON.stringify({ meta: { data_available: 180, data_returned: 7 } }), {
      headers: { "content-type": "application/vnd.api+json" },
    });
    call += 1;
    return response;
  }, "success");
  assert.equal(call, 4);
  assert.equal(requests.every((request) => request.pathname === "/optimade/amdb/v1/structures"), true);
  assert.equal(requests.every((request) => request.searchParams.get("page_limit") === "1"), true);
  assert.deepEqual(requests.map((request) => request.searchParams.get("filter") || ""), [
    "",
    '_anyterial_classification = "collinear"',
    '_anyterial_classification = "noncollinear-derived"',
    '_anyterial_electronic_type = "semiconducting"',
  ]);
  names.forEach((name) => assert.equal(targets[name].textContent, "7"));
});

test("stats resolves a root-relative base_url against the page origin", async () => {
  // The combined server serves base_url "/optimade/amdb"; it must not be used as a bare URL base.
  const { document, targets } = statsDocument("/optimade/amdb");
  const requests = [];
  await importStats(document, async (request) => {
    requests.push(new URL(request));
    return new Response(JSON.stringify({ meta: { data_available: 180, data_returned: 42 } }), {
      headers: { "content-type": "application/vnd.api+json" },
    });
  }, "relative-base");
  assert.equal(requests.length, 4);
  assert.equal(requests.every((request) => request.origin === "https://site.example.test"), true);
  assert.equal(requests.every((request) => request.pathname === "/optimade/amdb/v1/structures"), true);
  names.forEach((name) => assert.equal(targets[name].textContent, "42"));
});

test("stats leaves placeholders intact when the count request fails", async () => {
  const { document, targets } = statsDocument();
  await importStats(document, async () => { throw new Error("offline"); }, "failure");
  names.forEach((name) => assert.equal(targets[name].textContent, "—"));
});

test("stats leaves placeholders intact when data_returned is absent", async () => {
  const { document, targets } = statsDocument();
  await importStats(document, async () => new Response(JSON.stringify({ meta: { data_available: 180 } }), {
    headers: { "content-type": "application/vnd.api+json" },
  }), "missing-returned");
  names.forEach((name) => assert.equal(targets[name].textContent, "—"));
});
