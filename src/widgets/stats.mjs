const MAX_BODY_BYTES = 64 * 1024;
const FILTERS = {
  total: null,
  collinear: '_anyterial_classification = "collinear"',
  "noncollinear-derived": '_anyterial_classification = "noncollinear-derived"',
  semiconducting: '_anyterial_electronic_type = "semiconducting"',
};

const validCount = (value) => Number.isSafeInteger(value) && value >= 0;

async function count(baseUrl, filter) {
  const endpoint = new URL("v1/structures", `${baseUrl.replace(/\/$/, "")}/`);
  endpoint.searchParams.set("page_limit", "1");
  if (filter) endpoint.searchParams.set("filter", filter);
  const response = await fetch(endpoint, { headers: { Accept: "application/vnd.api+json, application/json" } });
  if (!response.ok) throw new Error(`OPTIMADE count request failed: ${response.status}`);
  const body = await response.text();
  if (new TextEncoder().encode(body).byteLength > MAX_BODY_BYTES) throw new Error("OPTIMADE count response too large");
  const document = JSON.parse(body);
  const available = document?.meta?.data_available;
  const returned = document?.meta?.data_returned;
  if (!validCount(available) || !validCount(returned) || returned > 1) throw new Error("Invalid OPTIMADE count response");
  return available;
}

async function load() {
  const config = JSON.parse(document.querySelector('[id^="site-stats-"][type="application/json"]')?.textContent || "{}");
  if (typeof config.base_url !== "string" || !config.base_url || /[\s\\]/.test(config.base_url)) return;
  const counts = await Promise.all(Object.entries(FILTERS).map(async ([name, filter]) => [name, await count(config.base_url, filter)]));
  counts.forEach(([name, value]) => {
    const target = document.querySelector(`[data-site-stat="${CSS.escape(name)}"]`);
    if (target) target.textContent = String(value);
  });
}

load().catch((error) => console.warn("OPTIMADE site counts unavailable", error));
