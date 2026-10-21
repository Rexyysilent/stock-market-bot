# OMNI completion candidate: operator guide

2026-10-20. Working local commands, not an activation order. Every example that
imports a receipt assumes the operator already possesses the genuine reviewed
record. This handoff creates none. Use ignored `omni-local/` paths for private
policies, databases, rights, actuals and customer evidence. Keep AI/domain labels
pending human review.

## 1. Review the candidate and protect the store

The public base is `d35f667aebd3c2590cabbbe80e51076b3a1fa788`. The supplied corrected
patch applies directly to it; its local checkpoint is
`1a0ae6d2a277642e9466495323c11469f43f911a`. The final delivery provides two alternative
installation forms: ordered stage patches on that corrected candidate, or one
cumulative patch directly on the public base. Follow the bundle application
manifest. Never apply both forms, and never apply the original patch on top of
the corrected patch.

Use a separate checkout and the two hash-locked requirements files. Preserve the
operator's current configuration, archives and production ledger. These changes
belong only to the independent OMNI database.

Before any new code opens an existing OMNI store:

```sh
python -m omni --db omni-local/events.db store-backup omni-local/pre-upgrade.db
python -m omni --db omni-local/events.db store-upgrade
python -m omni --db omni-local/events.db store-upgrade
```

The first command opens the old store read-only and uses SQLite online backup.
Record its SHA256. Upgrade is additive; the second upgrade returns
`already_current`. Restore requires a new destination and the recorded hash:

```sh
python -m omni store-restore omni-local/pre-upgrade.db omni-local/restored-old.db --sha256 RECORDED_SHA256
```

To roll back, stop issuing workbench commands, keep the upgraded store, restore the
pre-upgrade copy into a new path and use it with the older matching code. Never
remove extension tables from the only evidence copy. See
[MIGRATION-AND-ROLLBACK.md](MIGRATION-AND-ROLLBACK.md) for the executed receipts.

## 2. Inspect plans before any acquisition

```sh
python -m omni plan
python -m omni watch sec-edgar --target-family clinical_result
python -m omni vertical-plan
python -m omni expansion-plan
python -m omni route-plan sec-full-index
python -m omni route-plan sec-quarterly-index --year 2026 --quarter 3
python -m omni repair-plan --start-day 2026-10-01 --end-day 2026-10-10
```

These commands do not acquire sources. `plan`, the watch dry run, `vertical-plan`
and `expansion-plan` do not create a store. Other local planning commands can open
or create the selected OMNI store. The SEC watch dry run returns the stable query
plan and `query_hash` needed for a prospective protocol. Dynamic page offsets,
watermarks and result caps are recorded separately as request context.

A declared `--target-family` is the prespecified evaluation target. It does not
filter native source rows or depend on discovered classifier events. Omit it only
when the default acquisition family (`sec_filings` or source class) is intended.
Use separate exact source/route/family strata; changing them starts a new boundary.

Source uses, route approvals and field rights are independent. A policy's
`acquire` flag alone cannot authorize text storage or review, and an expansion
proposal grants no source access. Verified new routes remain disabled until
actual exact typed route approval is supplied. The gap ledger identifies routes
that remain blocked by missing contracts or credentials.

## 3. Authorized source observations and corrections

Only after the operator's actual source-use review and route prerequisites:

```sh
python -m omni --db omni-local/events.db --sources omni-local/sources.json watch sec-edgar --execute --max-pages 1 --user-agent "Organization contact@example.org"
python -m omni --db omni-local/events.db --sources omni-local/sources.json watch sec-edgar --execute --hydrate --max-pages 1
python -m omni --db omni-local/events.db --sources omni-local/sources.json watch sec-edgar --execute --hydrate --reconcile
python -m omni --db omni-local/events.db --sources omni-local/sources.json watch sec-edgar --execute --hydrate --refresh-documents
```

These are future operator actions and were not run as real collectors in this
handoff. Hydration is opt-in and requires text-storage/local-analysis uses. An
unfinished batch resumes bounded exhibit jobs. A cap, outage, missing body,
unknown clock or deferred document remains debt. Reconciliation offsets do not
by themselves certify a contiguous interval.

Full/quarterly snapshots use explicit disabled routes. Local snapshot import is
historical audit, not a past live collector session:

```sh
python -m omni --db omni-local/events.db index-snapshot sec-quarterly-index omni-local/master.idx --year 2026 --quarter 3
python -m omni --db omni-local/events.db index-compare OLD_SNAPSHOT_REF NEW_SNAPSHOT_REF
python -m omni --db omni-local/events.db index-collect sec-full-index
```

The last command is dry until `--execute` and exact approved source/route context
exist. Full-index quarter rollover is rejected as a same-scope comparison. Missing
entries append discrepancies. Only authoritative exact correspondence can support
withdrawal; backfill does not reconstruct original public availability.

For retained supplied records:

```sh
python -m omni --db omni-local/events.db receipt-import omni-local/exact-envelope.json
python -m omni --db omni-local/events.db source-observation-import omni-local/inventory-observation.json
python -m omni --db omni-local/events.db coverage-record omni-local/coverage-request.json
python -m omni --db omni-local/events.db correspondence-import omni-local/correspondence.json
```

Generic import preserves an envelope but does not bypass downstream validators.
Imported observations are marked as imports, not continuous live source cycles.
Correspondence needs exact native revisions, relationship-specific source proof
and a supplied review; similar dates or titles do not establish a copy.

On a material correction, inspect `queue`, `leads`, `packet` and `history`; preserve
both source revisions. The changed current state stays withheld. Resolve exact
source/definition conflicts, satisfy current rights and mandatory validation,
then record a new review. Do not reset hydration status, delete old receipts or
reuse an old approval to clear quarantine.

## 4. Clinical definition catalog and review

A real reviewed catalog is an optional separate input, never bundled as approved:

```sh
python -m omni --db omni-local/events.db --sources omni-local/sources.json --clinical-catalog omni-local/clinical-catalog.json ingest omni-local/document.json --source SOURCE_ID
python -m omni --db omni-local/events.db --sources omni-local/sources.json --clinical-catalog omni-local/clinical-catalog.json packet EVENT_ID EVENT_REVISION
python -m omni --db omni-local/events.db --sources omni-local/sources.json --clinical-catalog omni-local/clinical-catalog.json review EVENT_ID EVENT_REVISION --reviewer REVIEWER_REFERENCE --decision approve
```

Every declaration has its own version, exact source spans, disclosure-time
availability and policy. Missing or conflicting mapping abstains. Definition
sources are stored separately and linked to correction quarantine; old definition
dates do not make a current result stale. Field rights for those sources are also
required. Literal results without a catalog do not acquire canonical endpoint or
treatment equivalence. See [WORKSTREAM-2.md](WORKSTREAM-2.md) for admitted grammar
and explicit refusals.

## 5. Prospective workflow with two closed inventories

Use actual future dates in a protocol specification. Freeze the code, policies,
registry, coverage snapshot, query plan, source/route/family strata, sampling rule,
window, rubric, exclusions and custody before observations. A plan first returns
the exact subject for an operator's supplied review:

```sh
python -m omni --db omni-local/events.db --sources omni-local/sources.json prospective-request omni-local/protocol-spec.json
python -m omni --db omni-local/events.db receipt-import omni-local/protocol-review-envelope.json
python -m omni --db omni-local/events.db --sources omni-local/sources.json prospective-freeze omni-local/reviewed-protocol-spec.json
```

After genuine approved observation and the elapsed frozen window:

```sh
python -m omni --db omni-local/events.db prospective-coverage PROTOCOL_ID SOURCE_COVERAGE_REF
python -m omni --db omni-local/events.db prospective-enumerate PROTOCOL_ID omni-local/coverage-refs.json
python -m omni --db omni-local/events.db prospective-closure-request PROTOCOL_ID
python -m omni --db omni-local/events.db prospective-close PROTOCOL_ID --review-receipt SOURCE_CLOSURE_REVIEW_REF
python -m omni --db omni-local/events.db prospective-publication-closure-request PROTOCOL_ID
python -m omni --db omni-local/events.db prospective-publication-close PROTOCOL_ID PUBLICATION_CLOSURE_REVIEW_REF
```

Pass the same `--sources` and optional `--clinical-catalog` snapshot to every
command; they are omitted in later examples only for readability. The source
inventory supplies recall, including unknowns and misses. The emitted-proposal
inventory supplies precision, including rejected/non-event/incorrect proposals
and exact earlier packets. Both bind the frozen source/family scope. Neither
missing labels nor missing strata can vanish from a gate.

For each exact unit, prepare its adjudication, obtain the genuine review, then
import the completed record:

```sh
python -m omni --db omni-local/events.db prospective-adjudication-request PROTOCOL_ID omni-local/source-label.json
python -m omni --db omni-local/events.db prospective-adjudicate PROTOCOL_ID omni-local/reviewed-source-label.json
python -m omni --db omni-local/events.db prospective-publication-adjudication-request PROTOCOL_ID omni-local/proposal-label.json
python -m omni --db omni-local/events.db prospective-publication-adjudicate PROTOCOL_ID omni-local/reviewed-proposal-label.json
python -m omni --db omni-local/events.db prospective-run-request PROTOCOL_ID omni-local/run.json
python -m omni --db omni-local/events.db prospective-run PROTOCOL_ID omni-local/reviewed-run.json
```

Exact scheduler and source-cycle records are required for a scheduled session;
manual CLI execution is not scheduling evidence. Use `prospective-retention-*`,
`prospective-interval-*`, `prospective-clock-request`, `prospective-clock-import`
and `prospective-timing` for their distinct reviewed inputs. A source publication
date or imported scalar latency cannot substitute for availability/clock proofs.

Untouched partitions add prespecified custodians, both sealed inventories,
`prospective-seal-request` / `prospective-seal`, access receipts,
`prospective-consumption-request` / `prospective-consume`, and
`prospective-untouched-status`. The software records custody assertions; it cannot
prove an external person never saw data. Undeclared access, changed code or reused
units invalidate eligibility. See [WORKSTREAM-3-PROSPECTIVE.md](WORKSTREAM-3-PROSPECTIVE.md)
for all input shapes, command arguments and the trust boundary.

```sh
python -m omni --db omni-local/events.db prospective-report PROTOCOL_ID
python -m omni --db omni-local/events.db prospective-record-evaluation PROTOCOL_ID
python -m omni --db omni-local/events.db prospective-verify-evaluation EVALUATION_RECEIPT_ID
python -m omni --db omni-local/events.db prospective-migrate-legacy
```

Legacy migration retains historical audit bytes and IDs. It grants no prospective
credit. Raw `evaluate` JSON is descriptive; durable verification uses the above
receipt workflow and never activates a release.

## 6. Measured expansion without membership changes

Supply an explicit candidate JSON list. Propose before the shared future protocol
window begins; candidate identities and the current coverage set are bound:

```sh
python -m omni --db omni-local/events.db expansion-propose PROTOCOL_ID omni-local/candidates.json
python -m omni --db omni-local/events.db watch sec-edgar --target-family clinical_result --measurement-proposal PROPOSAL_REF --measurement-arm baseline
```

The watch example is still a dry plan. An authorized `--execute` validates the
proposal, current identities, exact source route, frozen family/query and window
before transport. It stamps the measured arm alongside the collector's actual
request counters; it does not filter the native denominator, fan out collectors
or change any coverage membership. Synthetic measurement contexts have an explicit
offline API opt-in and are unavailable through the actual watch CLI.

```sh
python -m omni --db omni-local/events.db expansion-observe PROPOSAL_REF omni-local/observation.json --prepare
python -m omni --db omni-local/events.db expansion-observe PROPOSAL_REF omni-local/reviewed-observation.json
python -m omni --db omni-local/events.db expansion-report PROPOSAL_REF --reviews omni-local/expansion-review-refs.json --persist
```

Every request attempt/census, useful assessment, review-effort record and monetary
allocation needs its exact reference. Actual costs cannot be inferred from request
counts. Shared/correlated assertions, mismatched windows, missing costs, unknown
issuers, errors and negative outcomes remain visible. Zero useful events has no
finite unit cost. See [WORKSTREAM-5.md](WORKSTREAM-5.md).

## 7. Rights, local briefs, economics and readiness

The full procedure and exact record shapes are in
[OPERATING-TOOLS.md](OPERATING-TOOLS.md). Its synthetic demo is executable:

```sh
python scripts/omni_synthetic_demo.py --output ../omni-synthetic-preview
```

It creates fictional local source evidence, missing-rights withholding, permitted
JSON/Markdown/HTML previews, a correction and current re-review, negative synthetic
feedback and the arithmetic `86 / 2 = 43`. Actuals remain false and every gate stays
pending. Do not import those synthetic receipts as actuals.

For later actual operator evidence:

```sh
python -m omni --db omni-local/events.db rights-import omni-local/field-rights.json
python -m omni --db omni-local/events.db brief-preview --scope-hash EXACT_SCOPE --purpose local_export --persist
python -m omni --db omni-local/events.db brief-export BRIEF_RECEIPT omni-local/brief-new.html
python -m omni --db omni-local/events.db history-search --query EVENT_ID
python -m omni --db omni-local/events.db operating-import omni-local/observed-cost-or-feedback.json
python -m omni --db omni-local/events.db economics-report --scope-hash EXACT_SCOPE --window-start START --window-end END --persist
python -m omni --db omni-local/events.db readiness-context PROTOCOL_ID omni-local/candidates.json
python -m omni --db omni-local/events.db readiness-report omni-local/readiness-references.json --persist
```

Readiness context can include explicit `--operating-window-start` and
`--operating-window-end` containing the protocol window. Use that exact scope and
window in operating observations and gate reviews. A brief must exist before its
usefulness/customer assessment. Imported future receipts, stale code/policy,
truthy placeholders, incomplete feedback census or synthetic evidence keep gates
pending. Complete costs require all workbook inputs, explicit zeros and exact
currency; blanks remain unknown and signed vendor/compute/other credits remain
signed. An old report cannot silently acquire a new denominator after correction.

## Completion boundary

The candidate implements safe local tooling and executable acceptance cases.
The first real run still needs operator review of source contracts and purposes,
genuine field/counsel permissions, clinical/identity definitions, a future protocol,
external custody and clock/scheduler evidence, elapsed observations, human labels,
actual operating costs and consented feedback. Each gate names its missing inputs.
No one must infer those inputs from passing fixture tests or a public CI badge.
