# Public Release Checklist

The repository must stay private until every blocking item below is complete.

## Legal and data access

- [ ] Obtain and retain written confirmation that each production data source
      may be accessed and used as implemented, or remove the affected adapter.
- [ ] Confirm whether attribution, branding, display, or redistribution terms
      apply.
- [ ] Review the release with qualified counsel if uncertainty remains.

## Security and privacy

- [ ] Rotate any credential that was ever exposed and enable secret scanning.
- [ ] Confirm production IDs and configuration are appropriate to publish.
- [ ] Configure API rate limiting and abuse monitoring.
- [ ] Verify analytics, logging, cache, and retention disclosures.
- [ ] Run a full history and dependency secret scan.

## Release quality

- [ ] Confirm CI passes from a clean checkout.
- [ ] Review all fixtures and documentation for copied third-party content.
- [ ] Verify license, contribution, security, and privacy documents.
- [ ] Test both FastAPI and Cloudflare runtimes.
- [ ] Reconfirm repository visibility only after the preceding checks pass.
