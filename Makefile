.PHONY: dev test lint format typecheck

dev:
	uvicorn app.main:app --reload

test:
	pytest

lint:
	ruff check .

format:
	ruff format .

typecheck:
	mypy app tests
