import { OptimadeTransport } from "./serve-optimade-table-protocol.mjs";

const CLASSIFICATION_LABELS = {
  collinear: "Collinear",
  "noncollinear-derived": "Based on noncollinear",
  mixed: "Both",
  unclassified: "Not classified yet",
};
const ELECTRONIC_TYPE_LABELS = {
  metallic: "Metallic",
  semiconducting: "Semiconducting",
  unknown: "KS gap unavailable",
};
const FIGURE_SPECS = [
  { key: "band", title: "Band structure", summary: "", empty: "Band structure has not been generated for this material yet.", alt: "Spin-split band structure", layout: "figure-card--wide" },
  { key: "structure", title: "Crystal structure", summary: "", empty: "Crystal structure figure has not been generated for this material yet.", alt: "Crystal structure view", layout: "" },
  { key: "bz", title: "Brillouin zone and path", summary: "Reciprocal-space box with labelled special points and the reported Δmax location when available.", empty: "Brillouin-zone figure has not been generated for this material yet.", alt: "Brillouin zone and k-path", layout: "" },
];
const INFO = {
  spaceGroup: "Parent crystallographic space-group.",
  magndata: "A screened entry may correspond to one or more entries in the MAGNDATA database.",
  parentGroups: "Parent nonmagnetic space group(s) reported by the MAGNDATA-based symmetry analysis after Spglib standardisation.",
  gap: "The band gap from Kohn-Sham DFT.",
  abundance: "Minimum crustal abundance among the constituent elements, reported in ppm as a simple scarcity proxy.",
  maxSplit: "Largest spin splitting within ±3 eV of the Fermi level in the high-throughput calculation.",
  avgSplit: "Brillouin-zone average of the largest spin splitting at each k-point within the same energy window.",
  fraction: "Fraction of the sampled Brillouin zone and near-Fermi bands with appreciable spin splitting.",
  collinearity: "Whether the magnetic configuration was collinear in MAGNDATA or constructed from a noncollinear entry.",
  phase: "Some materials also have non-altermagnetic (AM) phases predicted; AM for altermagnet and FiM for ferrimagnet.",
  wave: "Symmetry wave-class shorthand derived from the parent and halving-subgroup Laue classes used in the altermagnetism analysis.",
  symprec: "Spglib symmetry tolerance symprec, shown as an exponential base-10 value.",
  bnsMcif: "BNS setting indicated in the source MCIF file.",
};

// Per-property definition descriptions, keyed by prefixed OPTIMADE field name;
// populated from the inert widget config so field labels can carry a native
// title hint that starts with the exact filterable field name.
let fieldDescriptions = {};

const node = (tag, className = "", value = null) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (value !== null && value !== undefined) result.textContent = String(value);
  return result;
};
const append = (parent, ...children) => children.forEach((child) => child && parent.append(child));
const arrayValue = (value) => (Array.isArray(value) ? value.filter((item) => item !== null && item !== undefined) : []);
const joinValue = (value) => (arrayValue(value).join(", ") || "n/a");
const safeNumber = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);
const decimal = (value, digits = 3) => {
  const number = safeNumber(value);
  return number === null ? "n/a" : number.toFixed(digits);
};
const percent = (value) => {
  const number = safeNumber(value);
  return number === null ? "n/a" : `${(number * 100).toFixed(1)}%`;
};
const abundance = (value) => {
  const number = safeNumber(value);
  if (number === null) return "n/a";
  if (number >= 1000) return `${number.toLocaleString("en-US", { maximumFractionDigits: 0 })} ppm`;
  if (number >= 1) return `${number.toLocaleString("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} ppm`;
  return `${number.toFixed(3)} ppm`;
};
const symprec = (value) => {
  const number = safeNumber(value);
  if (number === null || number <= 0) return "n/a";
  const exponent = Math.log10(number);
  const rounded = Math.round(exponent);
  const shown = Math.abs(exponent - rounded) < 1e-9 ? String(rounded) : exponent.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  return `$10^{${shown}}$`;
};
const inlineLatex = (value) => {
  const text = String(value ?? "").trim();
  if (!text) return "";
  return text.includes("$") ? text : `$${text}$`;
};
const latexJoin = (value) => {
  const result = arrayValue(value).map(inlineLatex).filter(Boolean);
  return result.join(", ") || "n/a";
};
const formulaLabel = (value) => {
  const text = String(value ?? "").trim();
  if (!text) return "n/a";
  if (text.includes("$")) return text;
  let escaped = text.replaceAll("\\", "\\\\").replaceAll("{", "\\{").replaceAll("}", "\\}");
  escaped = escaped.replaceAll("%", "\\%").replaceAll("&", "\\&").replaceAll("#", "\\#");
  escaped = escaped.replaceAll("$", "\\$").replaceAll("_", "\\_").replaceAll("·", "\\cdot ").replaceAll("⋅", "\\cdot ");
  return `$\\mathrm{${escaped.replace(/(?<=[A-Za-z)\]])(\d+(?:\.\d+)?)/g, "_{$1}")}}$`;
};
const infoDot = (message) => {
  const dot = node("span", "info-dot", "i");
  dot.tabIndex = 0;
  dot.setAttribute("role", "button");
  dot.setAttribute("aria-label", "More information");
  const bubble = node("span", "info-bubble");
  bubble.setAttribute("role", "tooltip");
  bubble.append(node("span", "info-bubble-paragraph", message));
  dot.append(bubble);
  return dot;
};
const field = (parent, label, value, message = "", className = "", property = "") => {
  const wrapper = node("div", className);
  const dt = node("dt");
  dt.append(document.createTextNode(label));
  if (property) {
    const description = fieldDescriptions[property]?.description;
    dt.title = description ? `${property} — ${description}` : property;
  }
  if (message) dt.append(infoDot(message));
  const dd = node("dd");
  if (value instanceof Node) dd.append(value);
  else dd.textContent = String(value ?? "n/a");
  append(wrapper, dt, dd);
  parent.append(wrapper);
  return wrapper;
};
const section = (title) => {
  const result = node("section", "detail-section");
  result.append(node("h3", "", title));
  return result;
};
const externalIcon = () => {
  const image = node("img", "inline-icon");
  image.src = new URL("icons/Hicon_Pack_1/external-link.svg", document.baseURI).href;
  image.alt = "";
  image.setAttribute("aria-hidden", "true");
  return image;
};
const externalLink = (url, label, className = "") => {
  const link = node("a", className);
  link.href = url;
  link.append(document.createTextNode(label), externalIcon());
  return link;
};
const linkList = (items, className) => {
  const list = node("ul", className);
  items.forEach((item) => {
    const li = node("li");
    li.append(externalLink(item.url, item.label));
    list.append(li);
  });
  return list;
};
const textList = (items) => {
  const list = node("ul", "message-list");
  arrayValue(items).forEach((item) => list.append(node("li", "", item)));
  return list;
};
const validMagndataId = (value) => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value);
const magndataUrl = (value) => (validMagndataId(value) ? `https://cryst.ehu.es/magndata/index.php?index=${encodeURIComponent(value)}` : "");
const validDoi = (value) => typeof value === "string" && /^10\.\d{4,9}\/[^\s<>"'#?&\\]+$/.test(value);
const doiLinks = (values) => arrayValue(values).filter(validDoi).map((doi) => ({ label: doi, url: `https://doi.org/${encodeURI(doi)}` }));
const figureUrl = (value, apiBase) => {
  if (typeof value !== "string" || !value) return "";
  try {
    const url = new URL(value, apiBase);
    const pageUrl = new URL(document.baseURI);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "";
    if (url.origin !== new URL(apiBase).origin || (pageUrl.protocol === "https:" && url.protocol !== "https:")) return "";
    return url.href;
  } catch {
    return "";
  }
};

function elementsFor(attributes, formula) {
  const supplied = arrayValue(attributes._anyterial_elements);
  if (supplied.length) return supplied.join(", ");
  return [...new Set((String(formula).match(/[A-Z][a-z]?/g) || []))].join(", ") || "n/a";
}

function referencesFor(resource, included) {
  const relation = resource.relationships?.references?.data;
  const ids = Array.isArray(relation) ? relation.map((item) => item?.id) : relation?.id ? [relation.id] : [];
  const byId = new Map(included.map((item) => [item.id, item]));
  return doiLinks(ids.map((id) => byId.get(id)?.attributes?.doi).filter(Boolean));
}

function variantFor(value, magndataId) {
  return { magndata_id: magndataId, source: "", formula: "", phases: [], wave_classes: [], warnings: [], notes: [], ...value };
}

function sourceLabel(source) {
  return CLASSIFICATION_LABELS[source] || "No symmetry table entry";
}

function buildFigures(attributes, apiBase) {
  const records = new Map(arrayValue(attributes._httk_custom_figures).map((item) => [item.key, item]));
  const grid = node("div", "figure-grid");
  let availableCount = 0;
  FIGURE_SPECS.forEach((spec) => {
    const record = records.get(spec.key) || {};
    const light = record.available === true ? figureUrl(record.url, apiBase) : "";
    const dark = light ? figureUrl(record.dark_url, apiBase) || light : "";
    const figure = node("figure", `figure-card ${spec.layout}${light ? "" : " is-missing"}`.trim());
    const caption = node("figcaption", "figure-caption");
    caption.append(node("h4", "", spec.title));
    if (spec.summary) caption.append(node("p", "", spec.summary));
    figure.append(caption);
    if (light) {
      availableCount += 1;
      const visual = node("div", "figure-visual");
      const image = node("img", "theme-aware-figure");
      image.src = light;
      image.setAttribute("data-src-light", light);
      image.setAttribute("data-src-dark", dark);
      image.alt = spec.alt;
      image.loading = "lazy";
      visual.append(image);
      figure.append(visual);
    } else {
      const placeholder = node("div", "figure-placeholder");
      placeholder.append(node("p", "", spec.empty));
      figure.append(placeholder);
    }
    grid.append(figure);
  });
  return { grid, availableCount };
}

function buildVariantTable(variants) {
  const wrap = node("div", "table-wrap symmetry-table-wrap");
  const table = node("table", "results-table symmetry-table");
  const head = node("thead");
  const row = node("tr");
  ["MAGNDATA ID", "Collinearity", "Phase", "Wave", "SYMPREC", "BNS (MCIF)", "G", "H"].forEach((label) => {
    const th = node("th");
    th.append(document.createTextNode(label));
    if (["Collinearity", "Phase", "Wave", "SYMPREC", "BNS (MCIF)"].includes(label)) th.append(infoDot(INFO[label === "BNS (MCIF)" ? "bnsMcif" : label.toLowerCase()]));
    row.append(th);
  });
  head.append(row);
  const body = node("tbody");
  variants.forEach((variant) => {
    const tr = node("tr");
    const idCell = node("td");
    const url = magndataUrl(variant.magndata_id);
    idCell.append(url ? externalLink(url, variant.magndata_id) : node("span", "", variant.magndata_id || "n/a"));
    [idCell, sourceLabel(variant.source), joinValue(variant.phases), joinValue(variant.wave_classes), symprec(variant.symprec), latexJoin(variant.bns_mcif_latex || variant.bns_mcif), joinValue(variant.g_laue_classes), joinValue(variant.h_laue_classes)].forEach((value) => {
      const td = node("td");
      if (value instanceof Node) td.append(value);
      else td.textContent = String(value);
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(head, body);
  wrap.append(table);
  return wrap;
}

function buildVariantCards(variants) {
  const list = node("div", "linked-entry-list");
  variants.forEach((variant) => {
    const card = node("section", "linked-entry-card");
    const header = node("div", "linked-entry-header");
    const heading = node("div");
    heading.append(node("h4", "", `MAGNDATA ${variant.magndata_id || "entry"}`), node("p", "section-note", formulaLabel(variant.formula)));
    const url = magndataUrl(variant.magndata_id);
    append(header, heading, url ? externalLink(url, "Open entry", "secondary-button compact-button") : null);
    const facts = node("div", "linked-entry-facts");
    [["Collinearity", sourceLabel(variant.source)], ["Phase", joinValue(variant.phases)], ["Wave", joinValue(variant.wave_classes)]].forEach(([label, value]) => {
      const fact = node("div", "linked-entry-fact");
      fact.append(node("span", "fact-label", label), node("strong", "", value));
      facts.append(fact);
    });
    const details = node("dl", "details-grid linked-entry-grid");
    field(details, "BNS (MCIF)", latexJoin(variant.bns_mcif_latex || variant.bns_mcif), INFO.bnsMcif);
    field(details, "BNS", latexJoin(variant.bns_latex || variant.bns));
    field(details, "Effective BNS", latexJoin(variant.effective_bns_latex || variant.effective_bns));
    field(details, "Parent SG", latexJoin(variant.parent_spacegroups_latex || variant.parent_spacegroups));
    field(details, "SYMPREC", symprec(variant.symprec), INFO.symprec);
    field(details, "Connecting element", latexJoin(variant.connecting_elements_latex || variant.connecting_elements));
    field(details, "G Laue class", joinValue(variant.g_laue_classes));
    field(details, "H Laue class", joinValue(variant.h_laue_classes));
    const angle = safeNumber(variant.spin_angle_mismatch);
    field(details, "Spin-angle mismatch", angle === null ? "n/a" : `${angle.toFixed(1)}°`);
    field(details, "Spin-length mismatch", decimal(variant.spin_length_mismatch));
    const refs = doiLinks(variant.reference_dois);
    field(details, "Reference links", refs.length ? linkList(refs, "inline-link-list") : "n/a", "", "details-wide");
    append(card, header, facts, details);
    const messages = [...arrayValue(variant.warnings), ...arrayValue(variant.notes)];
    if (messages.length) card.append(textList(messages));
    list.append(card);
  });
  return list;
}

function buildDetail(resource, included, apiBase) {
  const attributes = resource.attributes || {};
  const formula = attributes.chemical_formula_reduced || attributes._anyterial_formula || "";
  const ids = arrayValue(attributes._httk_magndata_ids).map(String);
  let variants = arrayValue(attributes._anyterial_magndata_variants).map((item) => variantFor(item, item.magndata_id));
  if (!variants.length && ids.length) variants = ids.map((id) => variantFor({}, id));
  const parentGroups = arrayValue(attributes._anyterial_parent_spacegroups);
  const phases = arrayValue(attributes._anyterial_magnetic_phases);
  const waves = arrayValue(attributes._anyterial_wave_classes);
  const classification = CLASSIFICATION_LABELS[attributes._anyterial_classification] || attributes._anyterial_classification || "n/a";
  const electronic = ELECTRONIC_TYPE_LABELS[attributes._anyterial_electronic_type] || attributes._anyterial_electronic_type || "n/a";
  const article = node("article", "material-detail");
  const header = node("header", "material-header");
  const headerTop = node("div", "material-header-top");
  headerTop.append(node("h2", "", formulaLabel(formula)));
  const idChip = node("div", "material-id-chip");
  idChip.append(node("span", "material-id-label", "ID"), node("strong", "material-id-value", resource.id));
  headerTop.append(idChip);
  header.append(headerTop, node("p", "meta-line", `${attributes._anyterial_space_group || "n/a"} | ${classification} | ${electronic}`));
  const badges = node("div", "badge-row");
  badges.append(node("span", "badge", joinValue(phases)), node("span", "badge", `wave ${joinValue(waves)}`));
  header.append(badges);
  article.append(header);

  const identity = section("Identity");
  const identityGrid = node("dl", "details-grid");
  field(identityGrid, "Elements", elementsFor(attributes, formula), "", "", "_anyterial_elements");
  field(identityGrid, "Space group", latexJoin(parentGroups) === "n/a" ? inlineLatex(attributes._anyterial_space_group) || "n/a" : latexJoin(parentGroups), INFO.spaceGroup, "", "_anyterial_space_group");
  const magndataLinks = ids.map((id) => ({ label: id, url: magndataUrl(id) })).filter((item) => item.url);
  field(identityGrid, "MAGNDATA IDs", magndataLinks.length ? linkList(magndataLinks, "comma-link-list") : "n/a", INFO.magndata, "", "_httk_magndata_ids");
  field(identityGrid, "ICSD IDs", joinValue(attributes._anyterial_icsd_ids), "", "", "_anyterial_icsd_ids");
  field(identityGrid, "Parent space group(s)", latexJoin(parentGroups), INFO.parentGroups, "details-wide", "_anyterial_parent_spacegroups");
  const references = referencesFor(resource, included);
  field(identityGrid, "References", references.length ? linkList(references, "inline-link-list") : "n/a", "", "details-wide");
  identity.append(identityGrid);
  article.append(identity);

  const properties = section("Properties");
  const propertiesGrid = node("dl", "details-grid");
  field(propertiesGrid, "KS Gap", `${decimal(attributes._httk_dft_band_gap)} eV`, INFO.gap, "", "_httk_dft_band_gap");
  field(propertiesGrid, "KS Gap Type", electronic, "", "", "_anyterial_electronic_type");
  field(propertiesGrid, "Min abundance", abundance(attributes._anyterial_min_crustal_abundance), INFO.abundance, "details-wide", "_anyterial_min_crustal_abundance");
  properties.append(propertiesGrid);
  article.append(properties);

  const metrics = section("Spin-splitting metrics");
  const metricsGrid = node("dl", "metrics-grid");
  field(metricsGrid, "$Δ E^{max}_{split}$", `${decimal(attributes._anyterial_max_spin_splitting)} eV`, INFO.maxSplit, "metric-panel", "_anyterial_max_spin_splitting");
  field(metricsGrid, "$Δ E^{avg}_{split}$", `${decimal(attributes._anyterial_avg_spin_splitting)} eV`, INFO.avgSplit, "metric-panel", "_anyterial_avg_spin_splitting");
  field(metricsGrid, "FΔ", percent(attributes._anyterial_spin_splitting_fraction), INFO.fraction, "metric-panel", "_anyterial_spin_splitting_fraction");
  metrics.append(metricsGrid);
  article.append(metrics);

  const figureSection = section("Figures");
  const figureHeading = node("div", "detail-figure-heading");
  const figureResult = buildFigures(attributes, apiBase);
  figureHeading.append(node("h3", "", "Figures"), node("p", "section-note", `${figureResult.availableCount} of ${FIGURE_SPECS.length} detail figures available from the mounted calculation archive.`));
  figureSection.replaceChildren(figureHeading, figureResult.grid);
  article.append(figureSection);

  const symmetry = section("Symmetry screening");
  symmetry.append(node("p", "section-note", "The summary table shows one row per linked MAGNDATA record and symmetry-precision variant. If a MAGNDATA ID has multiple Spglib symprec values in the source data, it appears on multiple rows. The cards below keep the same expanded per-variant detail view, including wave classification and mismatch metrics."));
  symmetry.append(variants.length ? buildVariantTable(variants) : node("p", "empty", "No linked symmetry records are available for this entry."));
  if (variants.length) symmetry.append(buildVariantCards(variants));
  article.append(symmetry);

  const warnings = variants.flatMap((variant) => arrayValue(variant.warnings));
  const notes = variants.flatMap((variant) => arrayValue(variant.notes));
  if (warnings.length || notes.length) {
    const messages = section("Notes and warnings");
    messages.append(textList([...warnings, ...notes]));
    article.append(messages);
  }
  return article;
}

function showState(shell, message) {
  shell.replaceChildren(node("p", "empty", message));
  shell.setAttribute("aria-busy", "false");
}

function showNoSelection(shell) {
  const message = node("p", "empty");
  message.append(document.createTextNode("No material selected. Open an entry from "));
  const link = node("a", "", "the search results");
  link.href = new URL("search", document.baseURI).href;
  message.append(link, document.createTextNode("."));
  shell.replaceChildren(message);
  shell.setAttribute("aria-busy", "false");
}

async function loadShell(shell, Transport = OptimadeTransport) {
  const configNode = shell.querySelector('script[type="application/json"]');
  let config;
  try {
    config = JSON.parse(configNode?.textContent || "{}");
  } catch (error) {
    console.error("Invalid material widget configuration", error);
    showState(shell, "The material detail service is temporarily unavailable. Please try again later.");
    return;
  }
  fieldDescriptions = config.field_info || {};
  const id = new URLSearchParams(window.location.search).get(config.id_query || "id")?.trim() || "";
  if (!id) {
    showNoSelection(shell);
    return;
  }
  try {
    const transport = new Transport(config, { documentBase: document.baseURI });
    const result = await transport.fetchOne(id, { include: ["references"] });
    if (result === null) {
      showState(shell, "The requested material entry could not be found.");
      return;
    }
    const discovery = await transport.discover();
    shell.replaceChildren(buildDetail(result.resource, result.included, discovery.apiBaseUrl));
    shell.setAttribute("aria-busy", "false");
    window.altermagnetsUi?.initSubtree(shell);
  } catch (error) {
    console.error("Material detail OPTIMADE request failed", error);
    showState(shell, "The material data service is temporarily unavailable. Please try again later.");
  }
}

const start = () => document.querySelectorAll("[data-site-material-detail]").forEach((shell) => loadShell(shell));
if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
else start();

export { figureUrl, loadShell };
