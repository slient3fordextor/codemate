HOST ?= 127.0.0.1
PORT ?= 8000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)

.PHONY: dev sync test lint format typecheck check

dev:
	uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

sync:
	$(PYTHON) -m pip install -c requirements.lock -e ".[dev]"

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy app

check: lint typecheck test
