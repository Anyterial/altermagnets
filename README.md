Anyterial altermagnets (httk-web app)
------------------------------------

This repository is a dynamic httk-web app prototype for browsing and searching
mock altermagnetic material records.

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

For the current 180-material dataset, `make serve` is enough: startup discovers
the tables and seeds memory. To exercise the scalable persistent path, build
the DuckDB store first:

```bash
make build_store
make serve
```

`make build_store` atomically writes `data/altermagnets.duckdb`; runtime always
prefers that store and never modifies it. Set `ALTERMAGNETS_STORE_PATH` to use
a different runtime store path; the same variable (or
`python tools/build_store.py --target PATH`) selects the builder target.
`ALTERMAGNETS_DATA_DIR` (or `--data-dir`) selects a different source-table
directory. `ALTERMAGNETS_DETAILS_DIR` (or the builder's `--details-dir`)
selects the generated detail-asset tree.

Plot metadata is part of the material object graph as OPTIMADE `File` entries.
The database stores the root-relative locator, name, size, media type, and
description; the potentially large plot bytes remain in `data/details/` (or
the configured details directory) and are loaded on demand with containment
and size checks. Generate or update detail plots before `make build_store` so
the persistent store records them. The in-memory fallback discovers the
current plot set each time the site starts.

Then open:

- http://127.0.0.1:8080/

Try queries such as:

- `Mn`
- `Fe As`
- `P4/nmm`

Static publish mode is available for layout preview (`make generate`), but core
search/detail behavior relies on dynamic httk-web functions in `src/functions/`.

OPTIMADE service
----------------

The same dataset is also served over the [OPTIMADE](https://www.optimade.org/)
API by the thin `serve_optimade.py` entry point, built on the httk₂ modules
(*httk-core*, *httk-io*, *httk-atomistic*, *httk-data*, *httk-optimade*). It
reads the three CSV tables under `data/tables/`, parses each material's
`CONTCAR.bz2` into an exact crystal structure, and serves 180 `structures`
(with auto-derived composition fields and ten custom `_anyt_` properties) plus
the deduplicated `references`, linked via OPTIMADE relationships.

The custom property definitions live under `optimade/property_definitions/` as
self-contained YAML authored with the optimade-property-yaml skill; run
`python optimade/render_definitions.py` to (re)render the checked-in JSON under
`optimade/property_definitions/json/`. The custom properties use the `_anyt_`
prefix with `$id`s under `https://anyterial.se/optimade/defs/properties/`.

```bash
# Install the optional OPTIMADE dependencies (in the workspace they resolve via PYTHONPATH):
python -m pip install -e '.[optimade]'

# Validate every assembled record against its property definition:
make validate_optimade        # (python serve_optimade.py --validate)

# Serve the OPTIMADE API (default http://127.0.0.1:8081/):
make serve_optimade           # (python serve_optimade.py --port 8081)
```

Combined dynamic site and OPTIMADE pilot
----------------------------------------

The existing dynamic website and standalone OPTIMADE server remain independent.
For local exploration, the opt-in combined server mounts the API at
`/optimade` beside the dynamic website:

```bash
make serve                    # dynamic website: http://127.0.0.1:8080/
make serve_optimade           # OPTIMADE only: http://127.0.0.1:8081/v1/
make serve_combined           # both: http://127.0.0.1:8080/ and /optimade/v1/
```

The combined server also provides `/optimade-search`, a browser table that
uses the mounted API and accepts an OPTIMADE `filter` query parameter.
