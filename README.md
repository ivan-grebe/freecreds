<h1 align="center">FreeCreds</h1>

<p align="center">
  <em>Find the community-college course that transfers for the class you need.</em>
</p>

<p align="center">
  <a href="https://github.com/ivan-grebe/freecreds/actions/workflows/ci.yml"><img src="https://github.com/ivan-grebe/freecreds/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/ivan-grebe/freecreds/actions/workflows/deploy.yml"><img src="https://github.com/ivan-grebe/freecreds/actions/workflows/deploy.yml/badge.svg" alt="Deploy"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="MIT License"></a>
</p>

FreeCreds helps California students find community-college courses that articulate to a selected university course, with optional term and online-offering filters.

> [!IMPORTANT]
> FreeCreds is an independent planning aid, not an official advising service. Results can be delayed, incomplete, or interpreted differently by an institution. Confirm decisions with the relevant colleges and an academic counselor.

## Features

- Look up reverse articulation for CSU, UC, and participating private schools.
- Handle standalone equivalents and multi-course AND bundles correctly.
- Filter by current and upcoming terms for CVC-listed online offerings.
- Serve fast Cloudflare Pages Functions backed by D1.
- Refresh data on a schedule through GitHub Actions and a Cloudflare Worker.

## Run locally

Requirements: Python 3.10+, Node.js 22.13+, npm, and SQLite.

```bash
git clone https://github.com/ivan-grebe/freecreds.git
cd freecreds
python -m pip install -c config/requirements-dev.lock -e ".[dev]"
npm ci
```

Run the site and API locally through Cloudflare Pages:

```bash
npm run cf:dev
```

## Testing

```bash
npm run check
```

That runs Python and JavaScript lint, TypeScript typechecking, and both test suites. Database tests apply the real migrations; API query tests execute against SQLite. GitHub Actions runs the same checks on every pull request and each push to `main`.

## Deployment

Pushing to `main` deploys automatically: the [deploy workflow](.github/workflows/deploy.yml) runs the full check suite, applies database migrations, then publishes the Pages site and the refresh Worker. You can also trigger it manually from the Actions tab.

To deploy by hand instead:

```bash
npm run cf:db:schema
npm run cf:deploy
npm run cf:deploy:refresh
```

### First-time database setup

Create a D1 database, put its ID in `wrangler.toml` and `config/wrangler.refresh.toml`, then apply every migration:

```bash
npm run cf:db:create
npm run cf:db:schema
```

Build a local database and export it in D1-sized chunks:

```bash
python -m freecreds.ingester --all
python -m freecreds.cvc_fetcher
npm run cf:db:dump
npm run cf:db:import
```

### Secrets

Repository secrets used by the deploy and refresh workflows:

| Secret | Purpose |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | Pages and Workers deploys, plus D1 read/write and export |
| `CLOUDFLARE_ACCOUNT_ID` | Account that owns the database |

The scheduled Worker additionally needs `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`, and `MANUAL_TRIGGER_TOKEN` as encrypted Worker secrets. Never commit their values.

For production, configure Cloudflare rate limiting or WAF rules for `/api/*`, and review logging, caching, analytics, and retention settings.

## API

| Endpoint | Description |
| --- | --- |
| `GET /universities.json` | Supported universities |
| `GET /api/terms` | Current and upcoming terms |
| `GET /api/courses?university=CSUFULL` | Courses at a university |
| `GET /api/reverse?university=CSUFULL&prefix=MATH&number=170A` | Articulating community-college courses |

The reverse endpoint also supports `term`, `async_only`, and `standalone_only`.

## License

Released under the [MIT License](LICENSE). That license does not grant rights to third-party data, names, marks, APIs, or website content.
