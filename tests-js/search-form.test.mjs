import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import { DomDocument, element, installDom } from "./dom.mjs";

const SOURCE = new URL("../src/static/search-form.js", import.meta.url);

async function runSearch(document, window) {
  const source = await readFile(SOURCE, "utf8");
  const context = { document, window, URL, URLSearchParams, console };
  vm.runInNewContext(source, context);
  return context.altermagnetsSearch;
}

test("search fields map to exact OPTIMADE filter and sort strings", async () => {
  const document = new DomDocument();
  installDom(document);
  const api = await runSearch(document, { location: { href: "https://site.example.test/search", search: "" } });
  const result = api.buildQuery({
    q: "CrSb",
    elements: "cr, SB",
    classification: "collinear",
    electronic_type: "unknown",
    magnetic_phase: "AM",
    wave_class: "d",
    space_group: "P6_3",
    min_max_ss: "1.5",
    min_avg_ss: "2e0",
    min_fdelta_pct: "20",
    min_bandgap: "0.25",
    max_bandgap: "3",
    min_abundance_ppm: "12.5",
    sort: "abundance_desc",
  });
  assert.deepEqual(JSON.parse(JSON.stringify(result.value)), {
    q: "CrSb", elements: "cr, SB", classification: "collinear", electronic_type: "unknown",
    magnetic_phase: "AM", wave_class: "d", space_group: "P6_3", min_max_ss: "1.5", min_avg_ss: "2e0",
    min_fdelta_pct: "20", min_bandgap: "0.25", max_bandgap: "3", min_abundance_ppm: "12.5", sort: "abundance_desc",
  });
  assert.equal(result.filter, '_anyterial_search_text CONTAINS "crsb" AND _anyterial_elements HAS ALL "Cr","Sb" AND _anyterial_classification = "collinear" AND _anyterial_electronic_type = "unknown" AND _anyterial_magnetic_phases HAS "AM" AND _anyterial_wave_classes HAS "d" AND _anyterial_space_group_search CONTAINS "p6_3" AND _anyterial_max_spin_splitting >= 1.5 AND _anyterial_avg_spin_splitting >= 2 AND _anyterial_spin_splitting_fraction >= 0.2 AND _httk_dft_band_gap >= 0.25 AND _httk_dft_band_gap <= 3 AND _anyterial_min_crustal_abundance >= 12.5');
  // Sort is not mapped here anymore; the validated display alias rides through as value.sort and
  // the OPTIMADE table widget resolves it via sort_aliases.
  assert.equal(result.value.sort, "abundance_desc");
  assert.equal(api.buildQuery({ sort: "not-a-sort" }).value.sort, "screening_rank");
});

test("field criteria are reflected into the OPTIMADE filter on load for any navigation", async () => {
  const load = async (search) => {
    const replaced = [];
    const window = { location: { href: `https://site.example.test/search${search}`, search, replace: (u) => replaced.push(u), assign() {} } };
    const document = new DomDocument();
    installDom(document);
    await runSearch(document, window);
    return replaced.length ? new URL(replaced[0]).searchParams.get("filter") : null;
  };
  // A home shortcut / bookmark with only field criteria is redirected to carry the derived filter.
  assert.equal(await load("?classification=collinear"), '_anyterial_classification = "collinear"');
  assert.equal(await load("?min_bandgap=0.5"), "_httk_dft_band_gap >= 0.5");
  // A URL whose filter already matches the fields (a submitted search) is left as-is: no redirect loop.
  assert.equal(await load(`?classification=collinear&filter=${encodeURIComponent('_anyterial_classification = "collinear"')}`), null);
  // An explicit ?filter= with no field criteria (a shared filtered link) is preserved untouched.
  assert.equal(await load(`?filter=${encodeURIComponent("_httk_dft_band_gap >= 1")}`), null);
  assert.equal(await load(""), null);
});

test("numeric fields reject non-finite values and trailing garbage", async () => {
  const document = new DomDocument();
  installDom(document);
  const api = await runSearch(document, { location: { href: "https://site.example.test/search", search: "" } });
  const result = api.buildQuery({ min_max_ss: "12junk", min_avg_ss: "Infinity", min_fdelta_pct: "1e309", min_bandgap: " 2.5 ", max_bandgap: "-0.5" });
  assert.equal(result.value.min_max_ss, "");
  assert.equal(result.value.min_avg_ss, "");
  assert.equal(result.value.min_fdelta_pct, "");
  assert.equal(result.value.min_bandgap, "2.5");
  assert.equal(result.value.max_bandgap, "-0.5");
  assert.equal(result.filter, '_httk_dft_band_gap >= 2.5 AND _httk_dft_band_gap <= -0.5');
});

test("literal escapes quotes and backslashes, and the form is restored from the URL", async () => {
  const document = new DomDocument();
  const form = element(document, "form", { class: "search-form" });
  form.elements = {};
  for (const name of ["q", "elements", "classification", "electronic_type", "magnetic_phase", "wave_class", "space_group", "min_max_ss", "min_avg_ss", "min_fdelta_pct", "min_bandgap", "max_bandgap", "min_abundance_ppm", "sort"]) {
    const input = element(document, "input", { name });
    input.form = form;
    form.elements[name] = input;
    form.append(input);
  }
  document.append(form);
  // The initial URL already carries the filter matching its fields, so the on-load normalizer
  // leaves it untouched and restore() below is what is exercised. (The form submits with a plain
  // GET now; the browser carries the fields and normalizeFilterFromFields derives the filter.)
  const initialFilter = '_anyterial_search_text CONTAINS "iron" AND _anyterial_search_text CONTAINS "oxide" AND _anyterial_classification = "collinear"';
  const query = `?classification=collinear&q=iron+oxide&filter=${encodeURIComponent(initialFilter)}`;
  const window = {
    location: {
      href: `https://site.example.test/search${query}`,
      search: query,
      replace() { throw new Error("normalizer must not redirect when the filter already matches"); },
      assign() { throw new Error("the form uses a native GET; the script must not assign"); },
    },
  };
  installDom(document);
  const api = await runSearch(document, window);
  assert.equal(api.literal('a"\\b'), '"a\\"\\\\b"');
  assert.equal(form.elements.q.value, "iron oxide");
  assert.equal(form.elements.classification.value, "collinear");
});
