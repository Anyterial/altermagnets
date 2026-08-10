PYTHON ?= python3
PY_SOURCES := src/functions src/widgets tools/build_store.py serve_combined.py serve_optimade.py optimade_service.py publish_static.py

.PHONY: docs docs-live docs-clean clean format format-check typecheck typecheck_pyright lint test test-browser test-js test_fastfail audit build_store generate_details sync_detail_raw_paths serve_combined serve_optimade validate_optimade update_schemas

serve:
	python3 ./serve_combined.py

serve_combined:
	python3 ./serve_combined.py

build_store:
	$(PYTHON) ./tools/build_store.py

serve_optimade:
	python3 ./serve_optimade.py --port 8081

validate_optimade:
	python3 ./serve_optimade.py --validate

update_schemas:
	git submodule update --init dependencies/submodules/optimade-schemas dependencies/submodules/httk-schemas dependencies/submodules/anyterial-schemas
	git -C dependencies/submodules/optimade-schemas checkout master
	git -C dependencies/submodules/optimade-schemas pull
	git -C dependencies/submodules/httk-schemas checkout main
	git -C dependencies/submodules/httk-schemas pull
	git -C dependencies/submodules/anyterial-schemas checkout main
	git -C dependencies/submodules/anyterial-schemas pull

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
	$(PYTHON) -m ruff check $(PY_SOURCES) --fix
	$(PYTHON) -m ruff format $(PY_SOURCES)

format-check:
	$(PYTHON) -m ruff format --check $(PY_SOURCES)

lint:
	$(PYTHON) -m ruff check $(PY_SOURCES)

typecheck_pyright:
	$(PYTHON) -m pyright

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

test-browser:
	$(PYTHON) -m pytest -q -m browser --override-ini addopts=

test-js:
	node --test tests-js/

test_fastfail:
	$(PYTHON) -m pytest -q -x

ci: format-check lint typecheck test-js test_fastfail
