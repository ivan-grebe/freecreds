.PHONY: install ingest serve test lint smoke

install:
	python -m pip install --user -e ".[dev]"

smoke:
	python -m freecreds.assist_api

ingest:
	python -m freecreds.ingester --university CSUFULL

serve:
	python -m uvicorn freecreds.api:app --reload --port 8000

test:
	python -m pytest tests/ -v

lint:
	python -m ruff check src tests
