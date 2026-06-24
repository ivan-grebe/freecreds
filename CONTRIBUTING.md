# Contributing

This repository is private. Do not copy source data, credentials, production
database exports, or captured third-party responses into issues, commits, test
fixtures, or logs.

## Development

1. Create a branch from `main`.
2. Install dependencies with `python -m pip install -e ".[dev]"` and `npm ci`.
3. Make focused changes and add synthetic tests.
4. Run `npm run check`.
5. Open a pull request describing behavior, risks, and verification.

Keep migrations append-only after deployment. If a schema change is needed,
add a numbered migration and ensure `python scripts/check_migrations.py`
succeeds from an empty database.

By contributing code, you agree that it may be distributed under the MIT
License if the repository is released publicly later.
