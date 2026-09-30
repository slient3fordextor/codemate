HOST ?= 127.0.0.1
PORT ?= 8000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python)

.PHONY: start deploy desktop dev sync test test-safety test-frontend lint format format-check typecheck docs-check wheel-smoke check

start:
	./scripts/start.sh

deploy: start

desktop:
	./scripts/desktop.sh

dev:
	$(PYTHON) -m uvicorn app.main:app --reload --host $(HOST) --port $(PORT)

sync:
	$(PYTHON) -m pip install -c requirements.lock -e ".[dev]"

test:
	$(PYTHON) -m pytest

test-safety:
	CODEMATE_REQUIRE_SANDBOX_TESTS=1 $(PYTHON) -m pytest -q tests/test_agent_safety.py tests/test_cli_safety.py tests/test_sandbox_execution.py tests/test_computer_websocket.py tests/test_process_locking.py tests/test_worktree_delivery_boundaries.py tests/test_desktop_process_locking.py

test-frontend:
	node --test tests/frontend/*.test.cjs

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy app tests scripts

docs-check:
	$(PYTHON) scripts/check_docs.py

wheel-smoke:
	$(PYTHON) scripts/wheel_smoke.py

check: lint format-check typecheck test test-frontend docs-check wheel-smoke
