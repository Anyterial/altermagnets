import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import { DomDocument, DomNodeClass } from "./dom.mjs";

const SOURCE = new URL("../src/static/app.js", import.meta.url);

// DomElement is not exported; reach it through a created node and give the tree a dataset shim.
function makeDocument() {
  const base = new DomDocument();
  const created = [];
  const withDataset = (node) => { if (node && !node.dataset) node.dataset = {}; return node; };
  const documentElement = withDataset(base.createElement("html"));
  const body = withDataset(base.createElement("body"));
  const listeners = new Map();
  return {
    baseURI: base.baseURI,
    readyState: "complete",
    documentElement,
    body,
    createElement: (tag) => withDataset(base.createElement(tag)),
    createTextNode: (value) => base.createTextNode(value),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (type, fn) => listeners.set(type, fn),
    dispatchEvent: (event) => { listeners.get(event.type)?.(event); return true; },
    _make: (tag) => withDataset(base.createElement(tag)),
  };
}

function makeWindow() {
  return {
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    addEventListener() {},
    innerWidth: 1024, innerHeight: 768, scrollY: 0, pageYOffset: 0,
    renderMathInElement: undefined, // KaTeX absent -> renderMath is a guarded no-op
    getSelection: () => ({ isCollapsed: true }),
    location: { assigned: [], assign(href) { this.assigned.push(href); } },
  };
}

function clickRow(row, target, overrides = {}) {
  const event = {
    button: 0, target, currentTarget: row, defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; }, ...overrides,
  };
  row.listeners.get("click")(event);
}

async function loadApp() {
  const document = makeDocument();
  const window = makeWindow();
  const source = await readFile(SOURCE, "utf8");
  const context = { document, window, URL, URLSearchParams, console, Node: DomNodeClass, Element: DomNodeClass };
  vm.runInNewContext(source, context);
  return { document, window };
}

const HREF = "/material?id=anyt%3Aam-1-0001";

// Build the row shape the httk-serve widget emits: nine cells, the first a detail anchor.
function buildTable(document) {
  const table = document._make("table");
  const tbody = document._make("tbody");
  const row = document._make("tr");
  const anchor = document._make("a");
  anchor.setAttribute("href", HREF);
  anchor.textContent = "Ca(Al2Fe)4";
  const first = document._make("td");
  first.append(anchor);
  row.append(first);
  for (let i = 1; i < 9; i += 1) {
    const cell = document._make("td");
    cell.textContent = i === 2 ? "noncollinear-derived" : i === 8 ? "41500" : String(i);
    row.append(cell);
  }
  tbody.append(row);
  table.append(tbody);
  return { table, row, anchor };
}

test("the material-cell detail link survives the optimade-table formula/KaTeX pass", async () => {
  const { document } = await loadApp();
  const { table, row } = buildTable(document);

  document.dispatchEvent({ type: "httk-serve:optimade-table-updated", target: table });

  const survivingLink = row.querySelectorAll("td")[0].querySelector("a");
  assert.ok(survivingLink, "the material cell must still contain the navigation <a> after decoration");
  assert.equal(survivingLink.getAttribute("href"), HREF);
  // The formula text is beautified into the anchor (LaTeX for the later KaTeX pass), not dropped.
  assert.match(survivingLink.textContent, /\\mathrm\{/);
});

test("a plain click anywhere in the row follows the material link; modifiers/anchor/selection do not", async () => {
  const { document, window } = await loadApp();
  const { table, row, anchor } = buildTable(document);
  document.dispatchEvent({ type: "httk-serve:optimade-table-updated", target: table });

  assert.match(row.getAttribute("class") || "", /optimade-row--clickable/);
  const otherCell = row.querySelectorAll("td")[3];

  // Plain primary click on a non-link cell navigates to the material.
  clickRow(row, otherCell);
  assert.deepEqual(window.location.assigned, [HREF]);

  // Clicking the anchor itself is left to the browser (native navigation), not double-driven.
  clickRow(row, anchor);
  // A modifier click (open in new tab) is left to the browser.
  clickRow(row, otherCell, { metaKey: true });
  assert.deepEqual(window.location.assigned, [HREF], "anchor and modifier clicks must not be hijacked");

  // An active text selection suppresses navigation.
  window.getSelection = () => ({ isCollapsed: false });
  clickRow(row, otherCell);
  assert.deepEqual(window.location.assigned, [HREF], "a text selection must not trigger navigation");
});
