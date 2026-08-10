import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { DomDocument, element, installDom } from "./dom.mjs";

const SOURCE = new URL("../src/widgets/stats.mjs", import.meta.url);
const names = ["total", "collinear", "noncollinear-derived", "semiconducting"];

const tick = () => new Promise((resolve) => setImmediate(resolve));

function statsDocument() {
  const document = new DomDocument();
  const config = element(document, "script", { id: "site-stats-test-config", type: "application/json" }, JSON.stringify({ base_url: "https://api.example.test/optimade" }));
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

test("stats fills all placeholders from meta.data_available", async () => {
  const { document, targets } = statsDocument();
  let call = 0;
  await importStats(document, async () => {
    const response = new Response(JSON.stringify({ meta: { data_available: 7, data_returned: 1 } }), {
      headers: { "content-type": "application/vnd.api+json" },
    });
    call += 1;
    return response;
  }, "success");
  assert.equal(call, 4);
  names.forEach((name) => assert.equal(targets[name].textContent, "7"));
});

test("stats leaves placeholders intact when the count request fails", async () => {
  const { document, targets } = statsDocument();
  await importStats(document, async () => { throw new Error("offline"); }, "failure");
  names.forEach((name) => assert.equal(targets[name].textContent, "—"));
});

test("stats leaves placeholders intact when data_available is absent", async () => {
  const { document, targets } = statsDocument();
  await importStats(document, async () => new Response(JSON.stringify({ meta: { data_returned: 1 } }), {
    headers: { "content-type": "application/vnd.api+json" },
  }), "missing-available");
  names.forEach((name) => assert.equal(targets[name].textContent, "—"));
});
