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
Older or unversioned stores are treated as unavailable and fall back to the
source tables; rebuild them with `make build_store` after a store layout-version
change. Set `ALTERMAGNETS_STORE_PATH` to
use a different runtime store path; the same variable (or
`python tools/build_store.py --target PATH`) selects the builder target.
`ALTERMAGNETS_DATA_DIR` (or `--data-dir`) selects a different source-table
directory. `ALTERMAGNETS_DETAILS_DIR` (or the builder's `--details-dir`)
selects the generated detail-asset tree.

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
ALTERMAGNETS_OPTIMADE_BASE_URL=https://api.example.org/optimade make generate
# equivalent:
python publish_static.py --optimade-base-url https://api.example.org/optimade
```

Host `public/` on any static web server. Run the API separately with an exact
site origin allowed for CORS; the API host also serves figure bytes:

```bash
python serve_optimade.py --cors-origin https://www.example.org \
  --public-base-url https://api.example.org/optimade
```

Use HTTPS for both origins so browser figure requests do not downgrade to HTTP.

OPTIMADE service
----------------

The same dataset is also served over the [OPTIMADE](https://www.optimade.org/)
API by the thin `serve_optimade.py` entry point, built on the httk₂ modules
(*httk-core*, *httk-io*, *httk-atomistic*, *httk-store*, *httk-serve*). It
reads the three CSV tables under `data/tables/`, parses each material's
`CONTCAR.bz2` into an exact crystal structure, and serves 180 `structures`
(with auto-derived composition fields and 18 `_anyterial_` plus four `_httk_`
custom properties, including the `_httk_custom_figures` plot metadata) plus
the deduplicated `references`, linked via OPTIMADE relationships.

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

# Serve the OPTIMADE API (default http://127.0.0.1:8081/):
make serve_optimade           # (python serve_optimade.py --port 8081)
```

Local combined development
---------------------------

For local exploration, `make serve` runs the static site and mounts the API at
`/optimade` on the same origin:

```bash
make serve                    # site + API: http://127.0.0.1:8080/
make serve_optimade           # OPTIMADE only: http://127.0.0.1:8081/v1/
make serve_combined           # same combined server explicitly
```

The static output defaults to `/optimade` as its browser API base. Set
`ALTERMAGNETS_OPTIMADE_BASE_URL` or use `--optimade-base-url` when the API is
hosted elsewhere.
