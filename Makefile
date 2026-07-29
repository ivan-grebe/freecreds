.PHONY: install ingest serve test lint check smoke

install:
	python -m pip install --user -c requirements-dev.lock -e ".[dev]"

smoke:
	python -m freecreds.assist_api

ingest:
	python -m freecreds.ingester --university CSUFULL

serve:
	npm run cf:dev

test:
	npm run test:py
	npm run test:ts

lint:
	npm run lint:py
	npm run lint:js
	npm run cf:typecheck

check:
	npm run check
