# FreeCreds

FreeCreds helps California students find community-college courses that
articulate to a selected university course, with optional term and online
offering filters.

> **Private project status:** this repository is intentionally private.
> Do not change its visibility until every item in
> [PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md) is complete,
> especially the third-party data and access-permission review.

FreeCreds is an independent planning aid, not an official advising service.
Results can be delayed, incomplete, or interpreted differently by an
institution. Students should confirm decisions with the relevant colleges and
an academic counselor.

## Features

- Reverse articulation lookup for CSU, UC, and participating private schools.
- Correct handling of standalone equivalents and multi-course AND bundles.
- Current and upcoming term filters for CVC-listed online offerings.
- Fast Cloudflare Pages Functions backed by D1, plus a local FastAPI runtime.
- Scheduled private ingestion through GitHub Actions and a Cloudflare Worker.

## Architecture

```text
ASSIST and CVC sources
        |
        v
Python ingestion (src/) --> local SQLite --> chunked D1 imports
                                              |
                                              v
Cloudflare Pages Functions (functions/) --> frontend/
```

No captured production responses or database snapshots are committed. Test
fixtures are synthetic. The private refresh workflow exports only the base
tables it needs from the existing D1 database before updating CVC offerings.

## Local development

Requirements: Python 3.8+, Node.js 22+, npm, and SQLite.

```bash
python -m pip install -e ".[dev]"
npm ci
npm run check
```

To run the local FastAPI version:

```bash
python -m src.ingester --university CSUFULL
python -m uvicorn src.api:app --reload --port 8000
```

To run the Cloudflare Pages version:

```bash
npm run cf:dev
```

## Database and deployment

Create a D1 database and put its ID in `wrangler.toml` and
`wrangler.refresh.toml`, then apply every migration:

```bash
npm run cf:db:create
npm run cf:db:schema
```

Build a local database, export it in D1-sized chunks, and deploy:

```bash
python -m src.ingester --all
python -m src.cvc_fetcher
npm run cf:db:dump
npm run cf:db:import
npm run cf:deploy
npm run cf:deploy:refresh
```

The refresh workflow requires these GitHub Actions secrets:

- `CLOUDFLARE_API_TOKEN` with D1 read/write and export access.
- `CLOUDFLARE_ACCOUNT_ID` for the account that owns the database.

The scheduled Worker requires `GITHUB_TOKEN`, `GITHUB_OWNER`,
`GITHUB_REPO`, and `MANUAL_TRIGGER_TOKEN` as encrypted Worker secrets.
Never commit their values.

For production, configure Cloudflare rate limiting or WAF rules for `/api/*`
and review logs, caching, analytics, and retention settings.

## API

- `GET /universities.json`
- `GET /api/terms`
- `GET /api/courses?university=CSUFULL`
- `GET /api/reverse?university=CSUFULL&prefix=MATH&number=170A`

The reverse endpoint also supports `term`, `async_only`, and
`standalone_only`.

## Repository layout

```text
frontend/     Static application
functions/    Cloudflare Pages API
workers/      Scheduled refresh dispatcher
src/          Python clients, parsers, ingestion, SQLite, and FastAPI
migrations/   Ordered D1 migrations
scripts/      Export, import, and validation utilities
tests/        Python tests and synthetic fixtures
tests-ts/     Production TypeScript tests
```

## Governance and data

- [THIRD_PARTY_DATA.md](THIRD_PARTY_DATA.md) explains the source-data boundary.
- [PRIVACY.md](PRIVACY.md) describes current analytics and request handling.
- [SECURITY.md](SECURITY.md) covers private vulnerability reporting.
- [CONTRIBUTING.md](CONTRIBUTING.md) explains the development workflow.

The source code is licensed under the [MIT License](LICENSE). That license does
not grant rights to third-party data, names, marks, APIs, or website content.
