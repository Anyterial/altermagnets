PYTHON ?= python3

.PHONY: docs docs-live docs-clean clean format format-check typecheck typecheck_pyright lint test test_fastfail audit generate_details

serve:
	python3 ./serve_dynamic.py

generate:
	python3 ./publish_static.py

generate_details:
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
	$(PYTHON) -m ruff check src examples --select F401 --fix
	$(PYTHON) -m isort src
	$(PYTHON) -m black src

format-check:
	$(PYTHON) -m isort --check-only src
	$(PYTHON) -m black --check src

lint:
	$(PYTHON) -m ruff check src

typecheck_pyright:
	$(PYTHON) -m pyright

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest

test_fastfail:
	$(PYTHON) -m pytest -q -x

ci: format-check lint typecheck test_fastfail
