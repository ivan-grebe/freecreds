.PHONY: install ingest serve test lint smoke

install:
	python -m pip install --user -e ".[dev]"

smoke:
	python -m src.assist_api

ingest:
	python -m src.ingester --university CSUF

serve:
	python -m uvicorn src.api:app --reload --port 8000

test:
	python -m pytest tests/ -v

lint:
	python -m ruff check src/ tests/
