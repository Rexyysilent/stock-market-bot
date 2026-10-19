# OMNI local event workbench

OMNI is an optional local evidence and review path. The default mode is `off`.
Nothing installs a scheduler, sends messages, calls a model, expands acquisition
cohorts, or promotes event rows into production signals. The canonical daily
brief remains schema 2.8 / pipeline 2.6.5. This workbench has its own versioned
contracts and SQLite database, `history/omni-events.db`.

## What is implemented

| Package | Local implementation | Release boundary |
|---|---|---|
| 03 | Sourced issuer/security/asset/trial graph, scoped aliases, separate relationship roles, precision-preserving times | Unknown identities remain review items; the graph is a seed, not universal coverage |
| 04 | Deterministic clinical, regulatory, financing and contract claims; exact evidence spans; mandatory validator | Conservative grammar only; numeric clinical results and broad natural-language interpretation remain unsupported |
| 05 | Bounded EDGAR feed and daily-index reconciliation, durable per-exhibit jobs, optional issuer feeds, atomic cursor/event/outbox commit, shared local SEC rate gate | Source permissions and live coverage must be verified; incomplete intervals stay partial |
| 06 | Version-pinned ClinicalTrials.gov seeds and changes with atomic progress; FDA MedWatch and oncology notification metadata | Results posting does not establish outcome or approval; FDA notification indexes have explicitly limited scope |
| 07 | Explicit approve/reject audit and evidence-backed companion focus; six or more intake candidates survive the five-card display limit | Human approval is bound to the exact inputs and revalidated; no automatic publication |
| 08 | Separate observation-quality, sample/readiness and information-sequence validators, alongside existing ledger checks | No new production family or performance claim; runtime measurements remain in their captured era |
| 09 | Frozen pilot corpus, durable case/run/gold/coverage receipts, actual precision/recall denominators, Wilson intervals and version checks | Prospective evidence pending; synthetic tests cannot mature a gate |
| 10–12 | Explicit dependency and missing-evidence reports; operator-supplied expansion candidates exclude existing names | No bundled expansion list or automatic membership/distribution change |

Contracts include `omni-identity-1`, `omni-evidence-1`, `omni-store-1`,
`omni-validator-3`, `omni-review-1`, `omni-clinical-1` and `omni-evaluation-1`.
Events, evidence revisions and review receipts are separate objects. A changed
source timestamp without changed claims does not create another event revision.
An amendment with changed claims needs a new review. Source copies count as one
origin only when an explicit verified origin reference establishes equivalence.

## Commands

Run from the repository's Python environment. Global options precede the command.

```powershell
python -m omni plan
python -m omni watch sec-edgar
python -m omni --db history/omni-events.db watch sec-edgar --execute --max-pages 1
python -m omni --db history/omni-events.db status
python -m omni --db history/omni-events.db queue
python -m omni --db history/omni-events.db operations
python -m omni --db history/omni-events.db leads --limit 100
python -m omni --db history/omni-events.db history EVENT_ID
python -m omni --db history/omni-events.db packet EVENT_ID REVISION_ID
python -m omni --db history/omni-events.db review EVENT_ID REVISION_ID --reviewer NAME --decision approve
python -m omni --db history/omni-events.db brief --mode reviewed --legacy-brief daily_brief.json
python -m omni evaluate LOCAL_EVALUATION_INPUT.json
python -m omni reconcile-index --start-day 2026-10-16 --end-day 2026-10-16
python -m omni regulatory
python -m omni pilot-plan
python -m omni pilot-freeze REVIEWED_CORPUS.json
python -m omni pilot-record PILOT_ID case HUMAN_CASE_RECEIPT.json
python -m omni pilot-record PILOT_ID run SCHEDULED_RUN_RECEIPT.json
python -m omni pilot-record PILOT_ID gold GOLD_OBSERVATION_RECEIPT.json
python -m omni pilot-record PILOT_ID coverage SOURCE_COVERAGE_RECEIPT.json
python -m omni pilot-report PILOT_ID
```

`plan`, `pilot-plan` and watch/clinical/regulatory/reconcile-index commands without `--execute` make no network requests
and create no database. `brief` prints a companion JSON document; it does not
rewrite `daily_brief.json`. `--mode off` restores the supplied legacy focus.
Run `python -m omni COMMAND --help` for command options. `--db` must name an OMNI
database; an existing production ledger is rejected before journal changes.

The source client requires a contact User-Agent (default `SEC_USER_AGENT`).
It enforces route allowlists, public DNS destinations, TLS verification, no
redirects, bounded responses, request budgets, retry limits and persistent
Retry-After backoff. Every HTTP attempt consumes the attempt budget. Initial
limits are 12 metadata requests, 8 document requests and 30 attempts per cycle;
300 metadata requests, 80 document requests and 750 attempts per UTC day in one
workbench database. All SEC users in this checkout share a local rate gate at
three reservations per second. Separate machines/databases are not coordinated.

If an EDGAR scan reaches its cap before the old watermark, its cursor remains
unchanged and its next offset is queued. Use `watch sec-edgar --execute
--reconcile` to consume bounded backfill with an overlapping page. Mutable feed
offsets cannot certify continuity: a contiguous scan to the previous watermark
or independently verified index reconciliation is still required. Initial
imports establish an observation boundary and explicitly report
`initial_window_only`. They do not manufacture historical coverage.

Body hydration is opt-in (`--hydrate`) and also requires approved `store_text`
and `local_analysis` purposes. Empty/blocked bodies and missing/deferred exhibits
remain in the acquisition queue. A filing may have more than the three-document
cycle cap; successful exhibits are cached and subsequent hydrated cycles resume
the remaining jobs. `--refresh-documents` explicitly rechecks the listing and
bodies. Claims from current exhibits of one filing are combined before validation;
conflicting outcomes remain contested, and incomplete exhibit sets cannot be
approved. Negated or withdrawn stage assertions remain unestablished.

Contract claims can retain one exact, explicitly denominated USD amount as a
ceiling, option or obligation. The original sentence remains the evidence, and
the card carries currency and a stated-amount/not-revenue qualifier. Bare dollar
signs, multiple amounts or stages, approximations, unsupported units and competing
numeric scopes remain unparsed. No revenue, cash receipt or accounting equivalence
is inferred. Clinical numerical results remain unsupported rather than borrowed
from another trial or analysis.

`reconcile-index --execute` retrieves at most seven calendar days of SEC daily
indexes. It waits until 06:00 New York time on the following day, preserves filing
dates as dates, records content hashes and co-registrants, and merges metadata
without moving the current-feed watermark. Its completion means the listed
records in the observed index were parsed; it does not certify later corrections,
real-time dissemination or comprehensive source coverage. Missing indexes remain
gaps. Source budgets also apply to this command.

Clinical collection checks the registry version before and after paging. A version
change restarts an old continuation or discards a mixed-version batch. Trial
snapshots, changes and cursor updates commit in one transaction. Initial snapshots
remain seeds. FDA oncology intake records index metadata only and reports unknown
dates, route restrictions and result caps; no approval claim is inferred from a row.

`operations` reports exact queue counts, source cycles, budgets and job states.
`leads` preserves unclassified/unknown-identity items for investigation. `history`
shows event revisions and reviews. These reports omit source bodies. Monetary and
review-time costs remain unknown until genuine operating receipts exist.

## Source-use and review policy

`registry/event-sources.json` permits only the described local discovery
metadata. Body storage, review display, model processing and commercial display
are unapproved by default. A source URL or public availability does not itself
grant those purposes. There are no configured issuer feeds. FDA metadata is an
explicit command, with no automatic poller enabled.

Keep local policies, corpora, pilot receipts and generated reports under the
Git-ignored `omni-local/` directory. Never commit operator data, source bodies,
private configuration or actual evaluation records. The bundled identity graph
contains public identity facts and provenance references for the existing example
coverage; it is not universal coverage or a portfolio. Test event statements are
synthetic, including those using real entity names.

Use an operator-reviewed `--sources omni-local/sources.json` snapshot when actual
source-use decisions exist. Policy fields include `policy_id`, `version`,
`evidence`, exact host/path routes, `source_class`, `reporting_parties`, and
separate boolean `uses`. Source documents cannot change those permissions.
Policy fingerprints bind evidence to the approved snapshot; policy changes
require revalidation. Any downstream distribution requires its own source-purpose
and deployment review; this repository does not activate a distribution service.

Local `ingest` accepts the keyword fields of `omni.evidence.document` (excluding
`policy`, which comes from `--sources`): URL, native identity, text, title,
publication and observation times, source class, reporting party and completeness.
Its default evidence kind is `unverified_local_input`; it does not become a
prospective observation merely because it was imported today.

## Release evidence

`pilot-plan` creates only a draft. `pilot-freeze` requires a nonempty prespecified
corpus with actual human adjudication references; synthetic records are rejected.
The frozen manifest binds implementation, source policy, registry and coverage.
New receipts are rejected after those versions change. A report can still show an
older pilot but marks it ineligible for the current implementation. Only distinct
event IDs count as cases. Scheduled runs need actual complete source cycles and a
scheduler receipt; a manual command cannot become a scheduled session. Gold misses
remain in the recall denominator. No pilot is frozen by installation.

An evaluation input contains `manifest`, `cases`, `runs`, `gold` and optional
`operating_evidence`. Optional `expansion_candidates` are supplied explicitly by
the operator and default to an empty list. The manifest must freeze `implementation_hash`,
`corpus_hash`, `source_scope_hash` and `frozen_at`. Case rows require matching
versions, distinct event IDs, prospective observation times, meaningful exercise,
an adjudicator and an adjudication receipt. Gold rows require the frozen corpus
and scope, prespecification and adjudication receipts. Receipt references must
be audited by the operator; this evaluator does not authenticate human identity
or inspect arbitrary receipt files.

The manual-pilot gate needs 10 eligible distinct scheduled sessions and 100
meaningful new-issuer cases, no critical leakage, reviewed outside-cohort
publications and source-coverage evidence. Automatic activation is always false.
Eligibility for a later activation review additionally needs 300 proposed
publications, per-family/source evidence, precision at least 98%, a 95% Wilson
lower bound at least 95%, prespecified recall and timing evidence, and an
untouched test receipt. Correlated cases still require sampling review.

Missing pilot, source-use or operating records leave the corresponding gates
pending. Passing offline fixtures establishes only those fixture behaviors;
prospective accuracy, recall and usefulness require actual reviewed observations.

## Verification and rollback

The ten `test_omni_*.py` scripts are part of the existing offline suite. They
cover identities, exact claims, source failure/caps, crash rollback, replay,
posted clinical changes, reviews, quality denominators, release gates and actual
CLI subprocesses. The acquisition suite exercises feed → body → evidence →
event → local review queue using synthetic requests. Live canary receipts must
be reported separately with their actual source failures and limitations.

To stop the workbench, stop issuing its explicit commands and select `off`.
Keep its database as evidence; do not rewrite archives or production history.
Rollback the workbench together with the SEC adapter changes that import its
shared request gate. Preserve local databases and permitted evidence separately;
removing the new modules alone would leave those adapter imports broken.
