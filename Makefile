HOST ?= 127.0.0.1
PORT ?= 8000
PYTHON ?= python

.PHONY: dev test lint format typecheck

dev:
	uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

typecheck:
	$(PYTHON) -m mypy app tests
