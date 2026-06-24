# Third-Party Data and Services

FreeCreds code is MIT-licensed. That license does not apply to data, API
responses, HTML, names, logos, or other material supplied by third parties.

The application is designed to work with information associated with ASSIST
and the California Virtual Campus Exchange. It is not affiliated with or
endorsed by either service or by any listed institution.

## Repository rules

- Do not commit production responses, database exports, signed URLs, session
  tokens, CSRF tokens, API keys, or copied catalogs.
- Use small, invented fixtures for tests.
- Store deployment credentials only in GitHub Actions or Cloudflare secrets.
- Verify the applicable terms and obtain any required permission before
  collecting, using, redistributing, or publicly demonstrating source data.
- Remove or replace an ingestion adapter if its permitted use cannot be
  established before a public release.

The repository must remain private until the release checklist is complete.
Users of the software are responsible for confirming that their access and use
of every upstream service is authorized.
