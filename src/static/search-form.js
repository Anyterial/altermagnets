(function () {
  const fields = [
    "q", "elements", "classification", "electronic_type", "magnetic_phase", "wave_class", "space_group",
    "min_max_ss", "min_avg_ss", "min_fdelta_pct", "min_bandgap", "max_bandgap", "min_abundance_ppm", "sort",
  ];
  const enums = {
    classification: new Set(["", "collinear", "noncollinear-derived", "mixed", "unclassified"]),
    electronic_type: new Set(["", "metallic", "semiconducting", "unknown"]),
    magnetic_phase: new Set(["", "AM", "Luttinger ferrimagnet", "weakly-canted AFM", "FiM", "non-AM"]),
    wave_class: new Set(["", "a", "b", "c", "d", "e", "f", "g", "d/g", "s"]),
    sort: new Set(["screening_rank", "max_ss_desc", "avg_ss_desc", "bandgap_desc", "abundance_desc"]),
  };
  const numericPattern = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
  const maxLengths = {
    q: 256, elements: 128, classification: 40, electronic_type: 40, magnetic_phase: 80, wave_class: 8,
    space_group: 80, sort: 32, min_max_ss: 32, min_avg_ss: 32, min_fdelta_pct: 32, min_bandgap: 32,
    max_bandgap: 32, min_abundance_ppm: 32,
  };
  const textToken = (value, max, extra = "") => Array.from(value || "")
    .filter((char) => char.charCodeAt(0) >= 0x20 && char.charCodeAt(0) !== 0x7f && !"`\\<>".includes(char) && !extra.includes(char))
    .join("").trim().slice(0, max);
  const boundedTokens = (value, maxTokens, maxLength) => textToken(value).replace(/,/g, " ").split(/\s+/)
    .filter(Boolean).slice(0, maxTokens).map((token) => token.slice(0, maxLength));
  const literal = (value) => `"${String(value).replaceAll("\\", "\\\\").replaceAll('"', '\\"')}"`;
  const finiteNumber = (value) => {
    const token = (value || "").trim();
    if (!token || !Number.isFinite(Number(token))) return null;
    return numericPattern.test(token) ? token : null;
  };
  const readFields = (form) => Object.fromEntries(fields.map((name) => [name, form.elements[name]?.value || ""]));
  const sanitize = (raw) => {
    const value = {};
    fields.forEach((name) => {
      const cleaned = textToken(raw[name], maxLengths[name], name === "q" || name === "space_group" || name === "elements" ? "'\"" : "");
      value[name] = enums[name] ? (enums[name].has(cleaned) ? cleaned : "") : cleaned;
    });
    for (const name of ["min_max_ss", "min_avg_ss", "min_fdelta_pct", "min_bandgap", "max_bandgap", "min_abundance_ppm"]) {
      value[name] = finiteNumber(value[name]) || "";
    }
    if (!enums.sort.has(value.sort)) value.sort = "screening_rank";
    return value;
  };
  const buildQuery = (raw) => {
    const value = sanitize(raw);
    const predicates = [];
    const add = (predicate) => { if (predicates.length < 40) predicates.push(predicate); };
    boundedTokens(value.q.toLowerCase(), 12, 64).forEach((token) => add(`_anyterial_search_text CONTAINS ${literal(token)}`));
    const elements = boundedTokens(value.elements, 16, 8).map((token) => token.slice(0, 1).toUpperCase() + token.slice(1).toLowerCase());
    if (elements.length && predicates.length < 40) add(`_anyterial_elements HAS ALL ${elements.map(literal).join(",")}`);
    if (value.classification) add(`_anyterial_classification = ${literal(value.classification)}`);
    if (value.electronic_type) add(`_anyterial_electronic_type = ${literal(value.electronic_type)}`);
    if (value.magnetic_phase) add(`_anyterial_magnetic_phases HAS ${literal(value.magnetic_phase)}`);
    if (value.wave_class) add(`_anyterial_wave_classes HAS ${literal(value.wave_class)}`);
    if (value.space_group) add(`_anyterial_space_group_search CONTAINS ${literal(value.space_group.toLowerCase())}`);
    for (const [name, property, operator, scale] of [
      ["min_max_ss", "_anyterial_max_spin_splitting", ">=", 1],
      ["min_avg_ss", "_anyterial_avg_spin_splitting", ">=", 1],
      ["min_fdelta_pct", "_anyterial_spin_splitting_fraction", ">=", 100],
      ["min_bandgap", "_httk_dft_band_gap", ">=", 1],
      ["max_bandgap", "_httk_dft_band_gap", "<=", 1],
      ["min_abundance_ppm", "_anyterial_min_crustal_abundance", ">=", 1],
    ]) {
      if (!value[name] || predicates.length >= 40) continue;
      add(`${property} ${operator} ${Number(value[name]) / scale}`);
    }
    // `value.sort` carries the display alias; the OPTIMADE table widget resolves it to a real
    // OPTIMADE sort via its sort_aliases config, so the site does not map it here.
    return { value, filter: predicates.join(" AND ") };
  };
  globalThis.altermagnetsSearch = { buildQuery, literal, sanitize };
  // Reflect the form-field criteria into the OPTIMADE `filter` param that the table reads, so the
  // table filters on ANY navigation (home shortcuts, bookmarks, submits), not only after a submit.
  // Returns true if it redirected (via replace, so the back button is not trapped).
  const normalizeFilterFromFields = () => {
    const params = new URLSearchParams(window.location.search);
    const active = fields.some((name) => name !== "sort" && (params.get(name) || "").trim());
    if (!active) return false; // no field criteria: leave any explicit ?filter= URL untouched
    const desired = buildQuery(Object.fromEntries(fields.map((name) => [name, params.get(name) || ""]))).filter;
    if ((params.get("filter") || "") === desired) return false;
    if (desired) params.set("filter", desired);
    else params.delete("filter");
    const url = new URL(window.location.href);
    url.search = params.toString();
    window.location.replace(url.href);
    return true;
  };
  if (normalizeFilterFromFields()) return;
  const form = document.querySelector("form.search-form")?.querySelector('[name="q"]')?.form;
  if (!form) return;
  const restore = () => {
    const params = new URLSearchParams(window.location.search);
    fields.forEach((name) => {
      const element = form.elements[name];
      if (element && params.has(name)) element.value = params.get(name) || "";
    });
  };
  restore();
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const query = buildQuery(readFields(form));
    const url = new URL(window.location.href);
    const params = url.searchParams;
    fields.forEach((name) => params.set(name, query.value[name]));
    params.set("filter", query.filter);
    url.search = params.toString();
    window.location.assign(url.href);
  });
})();
