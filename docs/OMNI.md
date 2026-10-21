# OMNI local evidence and review workbench

OMNI remains off by default. The completion candidate implements all six engineering
workstreams from the 2026-10-20 handoff, with explicit refusals where source or
clinical contracts are missing. Source activation, human adjudication, real pilot
observations, membership changes and distribution remain pending operator work.
AI and domain labels remain pending human review. Synthetic tests give no real
pilot, customer, rights or operating-cost credit.

The canonical daily brief remains schema 2.8 / pipeline 2.6.5. This workbench uses
its own versioned contracts and SQLite store. It installs no scheduler, sends no
messages, invokes no model and does not promote OMNI events to production signals.

## Implemented workstreams

| Workstream | Implemented behavior | Remaining boundary |
| --- | --- | --- |
| 1 — source lifecycle | Immutable change observations, durable quarantine, current-state re-review, reviewed copy correspondence, observed feed intervals, index sets and correction surveillance; disabled verified route adapters | A copied assertion is not independent corroboration; missing source records are discrepancies, not automatic withdrawals; backfill cannot recover unobserved availability |
| 2 — clinical scope and amounts | Optional versioned endpoint/arm/cohort catalog; exact sentence and bounded pipe-table parsing; scoped HR/RR/OR/risk-difference/CI/p/count fields; scoped currency/amount atoms and semantic display projections | Real definition approvals are absent; unsupported tables, safety/disposition grammar and amount inference abstain |
| 3 — prospective evaluation | Future frozen protocol, independent source inventory, complete scoped publication census, timing/retention proofs, supplied adjudications, untouched custody and version-bound reports | Genuine prospective observations, scheduler clocks, human decisions and external custody cannot be fabricated |
| 4 — disabled verticals | NRC APS, EIA API, EIA-860 2025 and delayed USAspending parsers; explicit collectors; narrow read-only GET/POST contracts; all 20 proposed fixtures executable | Credentials, actual route metadata, reviewed identities, rights and live coverage remain missing where documented |
| 5 — measured expansion | Operator candidate set difference, frozen baseline/expanded scopes, source-first yield/recall/error reports, request/retry census, review effort and aligned incremental economics | Produces a proposal only; no built-in Wave B list, fanout or membership mutation |
| 6 — operating tools | Exact field/purpose rights, local neutral brief preview/export, correction history, typed operating records, workbook-aligned economics and OMNI-10/11/12 checks | Record consistency is not human authentication or legal approval; actual costs, customers, rights and value evidence remain absent |

Use the [operator guide](research/omni-20261020/OPERATOR-GUIDE.md) for runnable
commands, the [requirement matrix](research/omni-20261020/REQUIREMENTS-MATRIX.md)
for implementation/test mapping, and the
[source gap ledger](research/omni-20261020/SOURCE-GAPS.md) for unresolved contracts.
The [final report](research/omni-20261020/FINAL-REPORT.md) separates engineering
verification from real operating readiness.

## Contracts and preservation

| Area | Current contract |
| --- | --- |
| Identity / legacy base store | `omni-identity-1` / `omni-store-1` |
| Additive store extension / immutable receipts | `omni-extension-schema-1` / `omni-receipt-1` |
| Evidence / classifier / validator / review | `omni-evidence-2` / `omni-classifier-5` / `omni-validator-5` / `omni-review-2` |
| Clinical numeric grammar | `omni-clinical-numerics-1` plus explicit `omni-clinical-numerics-2` forms |
| Definition catalog / binding | `omni-clinical-scope-catalog-1` / `omni-clinical-scope-binding-1` |
| Scoped amounts | `omni-scoped-amount-1` |
| Source coverage / prospective workflow | `omni-source-coverage-1` / `omni-prospective-1` |
| Legacy audit / evaluator | `omni-pilot-3` / `omni-evaluation-3` |
| Rights / reviewed brief / operating observations | `omni-field-rights-1` / `omni-reviewed-brief-1` / `omni-operating-observation-1` |

Legacy evidence v1 remains readable. Old receipts and archived event revisions are
not rewritten into new approvals or prospective observations. Existing approvals
without current lifecycle/version binding require a new exact-state review.
[Migration and rollback](research/omni-20261020/MIGRATION-AND-ROLLBACK.md) preserve
both legacy and upgraded stores through checked backups and new restore paths.

A material source change, stale policy, changed catalog definition, conflicting
current outcome or incomplete exhibit set withholds the event until its current
state passes validation and review. Hydration status, cached source text, an old
approval or process restart cannot clear quarantine. Enrichment without changed
meaning is recorded separately. Historical conflict handling is conservative:
different primary outcomes under one trial scope can remain contested until their
separate analysis/endpoint scope is evidenced; the workbench does not invent a
conflict-resolution decision.

Source/native/revision correspondence needs source-supported proof and a supplied
exact review. `same_release`, `republishes`, `independently_reports`, `corrects`
and `retracts` remain distinct. Transitive copies count once. An origin count is
an assertion count; the workbench leaves `independence_assessed=false`.

## Source and extraction boundaries

`registry/event-sources.json` retains its default policies. New routes are disabled
and source-purpose grants are absent. Public availability supplies research
material, not blanket permission for storage, review, model use or distribution.
Operator policy snapshots and receipts belong in ignored local paths.

The GET source client retains route/DNS/TLS checks, redirect refusal, bounded
responses, shared local SEC pacing, finite attempt budgets and Retry-After
backoff. Body hydration remains opt-in and requires the appropriate source uses.
The new POST transport permits only verified read-only search contracts with exact
method/path/query/body allowlists and typed current route approval. It neither
obtains credentials nor broadens the existing GET client.

Feed windows retain source publication, local acquisition and prior observed poll
clocks separately. A first fetch is only an initial window. A later interval needs
captured matching route/query/family/policy observations and actual traversal to
the previous watermark. Source retention and original availability are separate
proofs. Daily/full/quarterly indexes never imply continuous real-time coverage.
The SEC full index is scoped to the observed New York calendar quarter so a quarter
rollover cannot appear to retract the entire previous quarter.

Clinical values need literal revision-bound scope. Optional catalog definitions
must have their own exact evidence, policy and display rights; their historical
dates are separate from result recency. The bounded table grammar requires complete
pipe tables with repeated explicit scope. General HTML/PDF reconstruction,
footnotes, inherited cells, censoring/unevaluable populations, severity/seriousness
and unimplemented effects/multiplicity methods remain explicit refusals.

Amounts retain decimal strings, currency or unknown currency, operator, role,
period, contract/financing identity, contingency and additivity evidence. Ceilings,
options, obligations, authority and gross/net proceeds remain separate. Bounds,
ranges and approximations never become exact scalars. The original exact-USD
control remains. No aggregate, conversion, annualization, received cash, revenue
or runway is inferred.

The nine [Work Package A source cases](research/omni-20261019/source-cases.json)
and original A/B/C/D research remain intact. They are research cases with AI draft
labels, not human-approved pilot observations. Both supplied numeric-parser fixes
and their nine negative examples remain covered.

## Review, rights and evaluation

`queue` exposes pending identifiers. `leads` and ingest diagnostics expose bounded
reason codes and identifiers, withholding nested source spans. `packet`, `history`,
reviewed brief preview and local export enforce field/purpose rights. Raw internal
storage and parser APIs are trusted local engineering interfaces, not a public
service or a replacement for the rights-filtered views.

Required attribution/scope/review/coverage fields with missing rights withhold a
card. Optional fields have independent grants. Numeric and amount projections
remove proof text; raw source text requires its separate right. Export revalidates
current source state, exact review and rights, and writes only a new local JSON,
Markdown or HTML file. A correction cannot revoke bytes already copied elsewhere;
no distribution or recall service is implemented.

Prospective recall comes from native source inventories independent of classifier
discoveries. Proposed-publication precision comes from a separate complete census
of emitted proposals in the frozen source/family strata, including withheld,
rejected, incorrect and non-event proposals. False matches across event families
are rejected. Missing labels stay pending. Generic receipt imports cannot remove
hard examples, invent source cycles or assert detections without correspondence.

The original release floors remain: 10 eligible distinct scheduled sessions and
100 meaningful new-issuer cases for manual review, then 300 proposed publications,
per-family/source evidence, at least 98% precision and a 95% Wilson lower bound of
at least 95%, plus prespecified recall/timing and untouched evidence. These are
necessary evidence requirements, not activation instructions. Typed dependent
gates can become ready for human review only; automatic activation stays false.

## Start locally

```sh
python -m omni plan
python -m omni watch sec-edgar --target-family clinical_result
python -m omni vertical-plan
python -m omni expansion-plan
python -m omni --db omni-local/events.db status
python -m omni --db omni-local/events.db operations
python -m omni --db omni-local/events.db queue
python -m omni --db omni-local/events.db leads --limit 100
python -m omni --db omni-local/events.db brief --mode off
```

Plans and watch commands without `--execute` perform no acquisition. The synthetic
operating demonstration writes to a new directory outside the checkout:

```sh
python scripts/omni_synthetic_demo.py --output ../omni-synthetic-preview
```

The guide explains backup-before-upgrade and future authorized collection. Do not
replace missing real approvals with the synthetic demonstration's receipts.

## Verification

Run both hash-locked Python 3.11 and 3.12 environments, then:

```sh
python scripts/validate_export_schema.py
python scripts/run_offline_checks.py --report review-results/offline.json
python -m compileall -q omni scripts
node --check dashboard/app.js
python scripts/run_secret_scan.py
git diff --check
```

The offline suite includes all OMNI extensions and existing dashboard/security
regressions. It allows loopback tests and rejects external provider access.
Final combined receipts are under
[verification](research/omni-20261020/verification/final-combined.json). Earlier
focused stage checks and public CI for the old revision do not establish that a
new candidate passes. No live pilot, Windows run of this candidate, deployment,
model execution or scheduler validation is claimed by Linux fixture tests.
