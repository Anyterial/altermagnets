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
  assert.equal(result.sort, "-_anyterial_min_crustal_abundance,-_anyterial_max_spin_splitting,id");
  assert.equal(api.buildQuery({ sort: "screening_rank" }).sort, "id");
  assert.equal(api.buildQuery({ sort: "max_ss_desc" }).sort, "-_anyterial_max_spin_splitting,id");
  assert.equal(api.buildQuery({ sort: "avg_ss_desc" }).sort, "-_anyterial_avg_spin_splitting,id");
  assert.equal(api.buildQuery({ sort: "bandgap_desc" }).sort, "-_httk_dft_band_gap,id");
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

test("literal escapes quotes and backslashes, and URL state round-trips", async () => {
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
  const assigned = [];
  const window = {
    location: {
      href: "https://site.example.test/search?classification=collinear&q=iron+oxide",
      search: "?classification=collinear&q=iron+oxide",
      assign(value) { assigned.push(value); },
    },
  };
  installDom(document);
  const api = await runSearch(document, window);
  assert.equal(api.literal('a"\\b'), '"a\\"\\\\b"');
  assert.equal(form.elements.q.value, "iron oxide");
  form.elements.q.value = 'A"\\B';
  form.dispatchEvent({ type: "submit", preventDefault() {} });
  const submitted = new URL(assigned[0]);
  assert.equal(submitted.searchParams.get("q"), "AB");
  assert.equal(submitted.searchParams.get("classification"), "collinear");
  // The human-facing alias stays in `sort` (for form pre-fill); the resolved OPTIMADE sort — the
  // value the table forwards — is under `osort`, so `sort=screening_rank` never reaches OPTIMADE.
  assert.equal(submitted.searchParams.get("sort"), "screening_rank");
  assert.equal(submitted.searchParams.get("osort"), "id");
});
