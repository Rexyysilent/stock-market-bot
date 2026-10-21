# Workstream 1 — durable source lifecycle and coverage

Engineering candidate, 2026-10-20. AI/domain labels remain pending human review.
No production collection, source approval, scheduler, deployment or distribution
was performed. Tests use synthetic source documents and disposable local stores.

## Implemented contracts

- `omni.receipts`: content-bound append-only envelopes, exact references, finite
  bounded JSON, transactional/idempotent import. SQLite rejects receipt updates
  and deletes. Imported labels are assertions, not authenticated human identity.
- `omni.lifecycle`: immutable enrichment/discrepancy/content correction/amendment/
  withdrawal observations; current evidence and candidate heads; exact state
  review; durable links and queue reopening; late conflicts and resumed packets.
  Cached hydration cannot erase a source-change receipt. Policy/implementation
  drift changes review binding. Metadata without a supplied policy fingerprint
  remains explicitly unbound; collectors bind their policy through observations.
- `omni.evidence` version 2 binds material metadata as well as text. Legacy version
  1 interpretation stays available. Arbitrary origin strings cannot merge claims.
  A canonical filing accession may group its own verified exhibit paths. External
  copies require exact reviewed source/native/revision correspondence and explicit
  source proof. Copy relations are transitive; independent reports/corrections/
  withdrawals are distinct. Same-native revisions remain one assertion lineage.
- `omni.review`: immutable event packet versions retain exact evidence bindings,
  including same-semantic-claim metadata updates. Current review binds lifecycle,
  registry and policies. Copy cards are deduplicated only after reviewed origin
  correspondence; different hosts never prove independent corroboration.
- `omni.coverage`: source-first inventory observations and separate feed interval,
  acquired index set, correction surveillance receipts. Every inventory must bind
  to its source cycle. Captured page hashes/native inventories and a head-to-prior
  watermark traversal are required for cursor progress. Caller booleans alone
  cannot close an interval. Imports preserve acquisition time separately from
  receipt import time. Bounded repair emits at most 52 seven-day batches.
- `omni.index_snapshots`: disabled full/quarterly SEC index collection, bounded
  parsing/chunked immutable metadata snapshots, exact-scope comparisons and atomic
  discrepancy propagation. Missing records do not become retractions. Empty/capped
  or incomplete inventories preserve coverage debt; no historical availability or
  continuous monitoring is reconstructed.
- `omni.route_contracts` and `omni.feeds`: canonical FDA/Beam routes, strict IDs,
  timezone/date semantics, duplicate/cap/empty/redirect boundaries. New routes need
  typed, exact route/source-purpose approval; placeholders, synthetic production
  grants, policy drift, expiry and revocation do not unlock transport.

The six B-O01..B-O06 design fixtures are executable in `test_omni_lifecycle.py`.
Additional tests cover crash rollback, replay, cross-source conflict, metadata
changes, receipts, migration, bounded repair, coverage provenance and route grants.
Old tests that treated caller origin strings as reviewed equivalence were changed
into refusal controls; a canonical filing fixture and reviewed correspondence
controls preserve positive coverage.

## Migration and recovery

Storage remains `omni-store-1`; additive extensions are
`omni-extension-schema-1`. Upgrade does not rewrite legacy evidence or approvals.
Old approvals lack the current-state binding and require re-review. Backup and
restore require new destination paths and verify hashes/integrity. The backup
command opens a legacy store read-only before migration. Restore is a copy to a
new path, not destructive in-place downgrade. `test_omni_lifecycle.MigrationTests`
exercises legacy/new backups, repeated upgrade, both restored formats, and refusal
to overwrite the original.

Commands: `python -m omni --db PATH store-backup DEST`, `store-upgrade`,
`store-restore BACKUP DEST --sha256 HASH`, `route-plan ROUTE`,
`repair-plan --start-day DATE --end-day DATE`, `feed-contract-parse ROUTE FILE`,
`index-snapshot ROUTE FILE`, `index-compare OLD_REF NEW_REF`,
`index-collect ROUTE` (dry by default), `receipt-import FILE`,
`source-observation-import FILE`, `coverage-record FILE`,
`correspondence-import FILE`. Source observation imports never pretend to be past
live polling sessions. No new acquisition is enabled by these examples.

## Source verification and unresolved boundaries

Primary sources checked 2026-10-20:

- SEC: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
  and https://www.sec.gov/Archives/edgar/full-index/ . Official guidance describes
  later removals, nightly indexes and weekly full/quarterly rebuilds. Index filing
  dates cannot establish first availability, and old daily indexes may differ.
- Beam: https://investors.beamtx.com/stockholder-tools/rss-feeds links the canonical
  https://investors.beamtx.com/rss/news-releases.xml . One bounded research request
  returned 10 items with GUID/date; stability and retention remain unverified.
- FDA: https://www.fda.gov/safety/medwatch-fda-safety-information-and-adverse-event-reporting-program/medwatch-rss-feed
  links a legacy path that redirected to the observed canonical lower-case route.
  One request returned 20 GUID/date items. Midnight feed dates are not proved
  first-public-availability clocks. Normal collectors reject redirects.
- FDA oncology notifications remain their explicitly limited family; no all-FDA
  approval, device, recall or label-change coverage is claimed.

`source-route-observations.json` retains retrieval clocks, response status/type,
byte hashes and structural counts, without source bodies or invented rights.
Unknown issuer feeds have no guessed route fallback. Retention, longitudinal ID
stability, actual source-purpose grants and real continuous coverage remain pending.
