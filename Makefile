PYTHON ?= python3
PY_SOURCES := src/functions tools/build_store.py serve_dynamic.py serve_optimade.py publish_static.py optimade/render_definitions.py

.PHONY: docs docs-live docs-clean clean format format-check typecheck typecheck_pyright lint test test_fastfail audit build_store generate_details sync_detail_raw_paths serve_optimade validate_optimade render_definitions

serve:
	python3 ./serve_dynamic.py

build_store:
	$(PYTHON) ./tools/build_store.py

serve_optimade:
	python3 ./serve_optimade.py --port 8081

validate_optimade:
	python3 ./serve_optimade.py --validate

render_definitions:
	python3 ./optimade/render_definitions.py

generate:
	python3 ./publish_static.py

sync_detail_raw_paths:
	python3 ./tools/sync_detail_raw_paths.py

generate_details: sync_detail_raw_paths
	python3 ./tools/generate_material_details.py

serve_static: generate
	echo "Open:"
	echo "* http://localhost:8080/index.html"
	cd public && python3 -m http.server 8080

clean:
	find . -name "*.pyc" -print0 | xargs -0 rm -f
	find . -name "*~" -print0 | xargs -0 rm -f
	find . -name "__pycache__" -print0 | xargs -0 rm -rf

format:
	$(PYTHON) -m ruff check $(PY_SOURCES) --select F401 --fix
	$(PYTHON) -m isort $(PY_SOURCES)
	$(PYTHON) -m black $(PY_SOURCES)

format-check:
	$(PYTHON) -m isort --check-only $(PY_SOURCES)
	$(PYTHON) -m black --check $(PY_SOURCES)

lint:
	$(PYTHON) -m ruff check $(PY_SOURCES)

typecheck_pyright:
	$(PYTHON) -m pyright

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

test_fastfail:
	$(PYTHON) -m pytest -q -x

ci: format-check lint typecheck test_fastfail
