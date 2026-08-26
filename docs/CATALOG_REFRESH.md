# Public road catalog refresh

The `Refresh public road catalogs` workflow runs every Monday and Thursday at 02:17 UTC and can also be started from **Actions → Refresh public road catalogs → Run workflow**. It pulls the official NHAI/MoRTH and NHIDCL national-highway feeds, every registered State/UT GePNIC feed, and all 37 PMGSY source feeds. It then rebuilds the three content-addressed runtime catalogs and runs offline parser and schema checks.

The workflow is deliberately review-only. It creates a unique `automation/public-road-catalogs-…` branch and opens a pull request; it never pushes to or merges `main`. A failed source, invalid schema, unexpected file change, or failed test stops the run before any branch is pushed. After rebuilding, the safety-checked pruner removes packs no longer referenced by the current manifests so obsolete catalog data does not accumulate in the repository.

Repository setup requires GitHub Actions to have read/write workflow permissions and permission to create pull requests. The workflow uses only the repository-scoped `GITHUB_TOKEN`; no API key or other secret is required.

Before merging a refresh PR, check:

- all expected feeds succeeded and none was silently skipped;
- source receipt and per-state record counts changed plausibly;
- procurement notices remain candidates, not awarded contracts;
- PMGSY records retain the source-reported `In Progress` status and do not invent a contractor, road segment, maintenance period, or DLP; and
- only catalog snapshots, packs, and manifest mirrors changed.
