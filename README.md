Anyterial altermagnets (httk-serve app)
------------------------------------

This repository is a static httk-serve site for browsing and searching
altermagnetic material records. Browser-side search and material details fetch
all changing data from the OPTIMADE service.

Current functionality
---------------------

- Welcome page at `/index`
- Persistent left-side search form on all main pages
- Search result page at `/search`
- Material detail page at `/material?id=<ID>`
- Dark / twilight / light theme selector (stored in localStorage)
- No cookies are used

Quick start
-----------

```bash
python -m pip install -e .
make serve
```

The source dataset is intentionally not version-controlled. If a persistent
store is absent or unusable, startup automatically seeds an in-memory SQLite
store from the three source tables. If neither form is available, the site
still starts and shows its dataset-unavailable state. For the data-backed
application, obtain the deployment's source tables and put them under
`data/tables/` with these exact names:

- `high_throughput_screening_results_fixed.csv`
- `altermagnets_collinear.csv`
- `altermagnets_noncollinear.csv`

For the current 180-material dataset, `make serve` starts the combined local
site and OPTIMADE service. To exercise the scalable persistent path, build
the DuckDB store first:

```bash
make build_store
make serve
```

`make build_store` atomically writes `data/altermagnets.duckdb`; the OPTIMADE
service always prefers a current, matching-layout store and never modifies it.
The ASGI service passes this `SqlStore` directly to httk-serve's lazy stored
adapter: filters, sorting, counts, and pagination execute in the database, and
only the bounded response page is hydrated. No provider snapshot or duplicate
in-memory OPTIMADE dataset is built at startup.
Older or unversioned stores are treated as unavailable and fall back to the
source tables; rebuild them with `make build_store` after a store layout-version
change. Set `ALTERMAGNETS_STORE_PATH` to
use a different runtime store path; the same variable (or
`python tools/build_store.py --target PATH`) selects the builder target.
`ALTERMAGNETS_DATA_DIR` (or `--data-dir`) selects a different source-table
directory. `ALTERMAGNETS_DETAILS_DIR` (or the builder's `--details-dir`)
selects the generated detail-asset tree.

The mounted source tables under `data/tables/` are intentionally untracked; the
two curation files — the sealed id ledger `tables/amdb_ids.sqlite` and the coupling
document `tables/amdb_run_content_ids.csv` — are git-tracked under the repo's
`tables/` directory instead. `ALTERMAGNETS_TABLES_DIR` (or the builder's
`--tables-dir`) selects a different curation directory. The ledger is
authoritative for every served id: the material ids `anyt.am-1-N` come from its
`results` family, keyed by each screening row's normalized MAGNDATA cell (a
comma-separated cell is split, stripped, sorted, and rejoined), and the
structure/reference/run/record/file ids from their respective families. The build
opens the ledger first, seeds the `results` family once (in screening-row order,
each id asserted against that row's `AMDBId` column), and thereafter reads
`AMDBId` only as that transition check — never for identity.

The default (non-legacy) build ingests the finished httk v1 run tree under
`data/raw_httk_v1/` (all ten project directories) and attaches each material's
relaxed structure from its own run. The mapping is authoritative, not guessed:
`tables/amdb_run_content_ids.csv` (semicolon-delimited) records one row per
coupling with columns
`AMDBId;run_material;raw_path;structure_content_id;run_content_id;status`.
`raw_path` is the run task directory as a POSIX path relative to the runs root
(the same value recorded in each material's detail JSON); a row carrying a
`raw_path` is matched to that exact run even when the run's derived name differs
from the screening formula. `structure_content_id`/`run_content_id` are derived
content-address pins verified against the freshly collected runs, `status` is
`auto` (builder-derived), `curated` (hand-pinned), or `ambiguous` (a plausible
run that needs manual curation). The builder rewrites this file every build.
Because the content-id pins are derived data, pass `--refresh-coupling` (to
`tools/build_store.py`) to rewrite them from the current build — for example
after an ingest change alters every structure's content id — preserving each
row's `AMDBId`, `raw_path`, and `status`. Any material left without a coupled
structure falls back to its `data/details/` CONTCAR, so the default build is
always at least as complete as `make build_store_legacy`.

Plot metadata is part of the material object graph and is exposed through the
custom `_httk_custom_figures` structures property. The database stores the
root-relative locator, name, size, media type, and description; the potentially
large plot bytes remain in `data/details/` (or the configured details directory)
and are loaded on demand with containment and size checks. Generate or update
detail plots before `make build_store` so the persistent store records them.
The in-memory fallback discovers the current plot set each time the site starts.

Then open:

- http://127.0.0.1:8080/

Try queries such as:

- `Mn`
- `Fe As`
- `P4/nmm`

Static publishing emits the complete site, including browser-side OPTIMADE
search, material details, home-page counts, and curated highlights:

```bash
ALTERMAGNETS_OPTIMADE_BASE_URL=https://api.example.org/optimade/amdb make generate
# equivalent:
python publish_static.py --optimade-base-url https://api.example.org/optimade/amdb
```

Host `public/` on any static web server. Run the API separately with an exact
site origin allowed for CORS; the API host also serves figure bytes:

```bash
python serve_optimade.py --cors-origin https://www.example.org \
  --public-base-url https://api.example.org/optimade/amdb
```

Use HTTPS for both origins so browser figure requests do not downgrade to HTTP.

OPTIMADE service
----------------

The same dataset is also served over the [OPTIMADE](https://www.optimade.org/)
API by the thin `serve_optimade.py` entry point, built on the httk₂ modules
(*httk-core*, *httk-atomistic*, *httk-store*, *httk-serve*).
`MaterialRecord` and the DOI reference record are registered store-native
backings. Their durable property projections serve 180 `structures` (with
auto-derived composition fields and the `_anyterial_`/`_httk_` properties) and
deduplicated `references`. A thin bounded-page adapter preserves AMDB's public
human-readable IDs, reference relationship blocks, and deployment-specific
absolute figure URLs without copying the underlying catalogue.

The curated custom property definitions are loaded verbatim from the live schema
submodules: Anyterial-defined properties use the `_anyterial_*` prefix and HTTK-defined
properties use `_httk_*`. The deployment-specific `_httk_custom_figures` definition is
generated locally with httk₂'s lightweight property builder and receives an unpublished
`https://schemas.httk.org/ad-hoc/` identifier; it does not depend on a published schema
file. Clone with `git clone --recurse-submodules`, or initialize/update the schemas with
`make update_schemas`.

```bash
# Install the optional OPTIMADE dependencies (in the workspace they resolve via PYTHONPATH):
python -m pip install -e '.[optimade]'

# Validate every assembled record against its property definition:
make validate_optimade        # (python serve_optimade.py --validate)

# Serve the standalone OPTIMADE API (default http://127.0.0.1:8081/):
make serve_optimade           # (python serve_optimade.py --port 8081)
```

Local combined development
---------------------------

For local exploration, `make serve` runs the website at `/`, the OPTIMADE index
at `/optimade/index`, and the AMDB API at `/optimade/amdb` on the same origin:

```bash
make serve                    # site + API: http://127.0.0.1:8080/
make serve_optimade           # standalone API only: http://127.0.0.1:8081/v1/
make serve_combined           # same combined server explicitly
```

When using `serve_combined.py --public-base-url`, provide the public HTTP(S)
origin only, such as `https://site.example`; the combined app serves the
website at `/` and derives the OPTIMADE mounts below that origin.

A production (HTTPS) deployment additionally exposes a Data Space Protocol
(DSP) 2025-1 minimal catalogue at `<base>/dsp`. The catalogue advertises the
altermagnets database as one DCAT dataset, with the OPTIMADE API and the
interactive website published as two `dcat:DataService`s that serve it. DSP
mandates HTTPS, so the `/dsp` mount is only present when the public origin is
`https://` and is absent in local `http://` development.

The static output defaults to `/optimade/amdb` as its browser API base. Set
`ALTERMAGNETS_OPTIMADE_BASE_URL` or use `--optimade-base-url` when the API is
hosted elsewhere.
