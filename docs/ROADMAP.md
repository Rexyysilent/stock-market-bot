# Public roadmap: reliable evidence before broader coverage

This project remains local-first public data tooling. There is no roadmap for
brokerage connectivity, order placement, security recommendations, personalized
allocation, paid signal tiers, target prices, or a hosted advisory feed.

The order below is deliberate: correctness, coverage, usability, evaluation, then
distribution. Phase gates are proposed acceptance criteria, not completed results
or delivery-date promises. See [the review](HARDENING_REVIEW.md) for specific known
limitations and [the research register](RESEARCH_REGISTER.md) for primary sources.

## Foundation to retain

The existing shared UTC/NYSE run context, source-time distinctions, completed-
session eligibility, headline lanes, source health/fallbacks, immutable archives,
state rollback, process locking, version-separated ledger, and locked CI are
valuable. Keep these contracts. Do not introduce an agent framework, cloud service,
vector database, or distributed queue without a measured need.

## In the 2.6.3 review change set

Optional strict JSON profiles and isolated workspaces; exact fixed-basket and pair
comparison windows; endpoint-aware ledger cache refill; directional sample gates;
health-first local dashboard; missing RSI instead of a neutral substitute;
local-only JSON import; no-key synthetic demo; read-only inspect/diff; loopback and
same-origin server defaults; additional deterministic regressions and CI reports.
These implement several priorities already present in the earlier roadmap rather
than replacing its direction.

## Phase A: prove the local release path

**A1. Release evidence and rollback.** Require the actual Python 3.11/3.12 CI results,
schema and JavaScript checks, locked dependency audit, secret scan, and a reviewer
walk-through of synthetic and degraded fixtures. Document restoring a prior
workspace backup. Do not overwrite historical archives when moving to 2.6.3.
Acceptance: a clean clone can display the demo without keys; a second operator can
follow the workspace guide without editing source code; all applicable checks
pass on the final commit, not just its parent.

**A2. Windows scheduling.** Extend the existing ValidateOnly path to an explicit
profile/workspace invocation; validate paths, quoting, timezone gates, exit codes,
logs, and the outer operation lock without registering a task in CI. Acceptance:
Windows dry-run tests plus an owner-run test covering no-run-before-settle, failure
logging, a duplicate launch, and restart. Keep the old scheduler untouched until
this path has evidence.

**A3. Crash and restore drills.** Exercise interruption around archive creation,
state replacement, mirror verification, and ledger rebuild. Current exception
rollback is not a multi-file crash transaction. Acceptance: a recovery journal or
manifest makes committed/uncommitted generations distinguishable; interrupted
runs cannot be mistaken for a complete current brief; a restore reproduces the
same archived bytes. Add SBOM generation without replacing the reviewed locks.

## Phase B: one explicit source contract

Introduce a small common result envelope around existing adapters, not a rewrite
of all acquisition code at once. It should carry provider, publisher, instrument
identity, acquisition ID, source/event/observed times with precision, expected and
received coverage, parser version, latency, retry/quota state, and permitted
retention metadata. Keep `fresh`, `cached`, `stale`, `empty`, `unsupported`,
`rate_limited`, `transport_error`, and `parse_error` distinguishable.

Start with daily prices and SEC records. A valid response is not necessarily a
current response, and a fallback is not necessarily equivalent coverage. Acceptance:
fixtures for normal, missing symbol, partial page, stale date, 429, 5xx, changed
schema, malformed body, and exhausted retry budget. Every displayed figure must
have a path back to a source observation or a documented calculation.

Add a shared per-host request budget, bounded retry/backoff, and a circuit breaker.
Cache run-scoped requests by provider, symbol/identifier, adjustment basis, and
window so several consumers do not reacquire the same history. Preserve raw
checksums where terms permit; use sanitized shape fixtures elsewhere. Measure
request counts, cache hits, provider throttles, p50/p95 runtime, and eligible
coverage before and after. No throughput improvement claim without those measures.

## Phase C: expand the universe without diluting evidence

Separate a **candidate registry** from a **bounded active acquisition universe**.
A directory can contain thousands of candidates without making every expensive
adapter scan all of them on every run.

Use a dated official directory snapshot and explicit identifiers. Exclude test
issues, keep share classes and instrument types separate, and retain unmatched or
ambiguous mappings for review. Maintain symbol history, venue, currency, native
calendar, listing interval, and corporate-action lineage. Today's membership is
not a survivorship-free historical universe.

For each candidate, measure price/history completeness, freshness, source support,
applicable event sources, and estimated incremental request cost. A missing options
chain may mean not applicable or not entitled, not a broken equity price source.
Enable only the source families that apply. Do not treat index volume as stock
volume or use futures roll artifacts as company events.

Run an opt-in profile as a shadow workspace. Increase active membership only when
existing members retain acceptable coverage and the same host budgets. The
current 45-symbol example and 64-member cap are a starting interface, not a proved
optimal universe. Acceptance: a coverage matrix per provider/instrument type,
zero silently mixed profile histories, explained exclusions, no unexplained quota
regression, and an explicit scope statement for every pack.

A later India or multi-market pack requires native exchange calendars, timezone
and currency handling, instrument mapping, corporate-action tests, source terms,
and appropriate local filings. Appending `.NS` to symbols is not an implementation
of those requirements. Keep this separate from the U.S.-session pilot.

## Phase D: improve signal legibility and research validity

**D1. Availability-aware replay.** Extract a pure replay path that uses only
observations available before the decision time and strictly earlier baseline
sessions. Adding future records must not change a historical result. Keep planned
events, filing dates, publication instants, and date-precision evidence distinct.
Do not mutate production state during replay. Acceptance: deterministic invariance
tests for future rows, repeated sessions, revised macro data, and source delays.

**D2. Provenance-root grouping.** Link multiple headlines and derived features back
to the same filing, release, registry change, or price event. Show a source tree
or evidence drawer, not an inflated confidence number. Preserve cross-family
confluence as a descriptive count while distinguishing shared-origin evidence.
Acceptance: curated same-source/different-provider examples do not gain apparent
independence; genuinely distinct events remain separate.

**D3. Censoring and outcomes.** Model absence from top-N social lists as censored or
unknown, not measured zero. Make price-empty outcomes retryable unless a documented
terminal condition applies. Persist universe benchmark expected/used membership;
compare like with like. Acceptance: no artificial attention births from missing
ranked rows; no permanent outcome loss after a transient empty response; every
partial benchmark reports its denominator.

**D4. Evaluation.** Freeze candidate definitions and a prospective holdout. Record
all tested variants, including failures. Report issuer-link accuracy, evidence
support, deduplication errors, source freshness, alert repetition, and reviewer
triage time separately from future-price outcomes. Estimate recall only against a
bounded, independently defined event set, not against whatever the system found.

For the descriptive ledger, disclose horizon overlap and same-session clustering;
add a justified date-block or other dependence-aware method before stronger
uncertainty claims. Keep sample-size gates for each reported subset. More tickers
on one day are not the same as more independent time periods. Never describe a
historical event ledger as an executable, cost-adjusted strategy backtest.

## Phase E: utilities users can actually feel

**A diagnostic command** should explain missing dependencies, required versus
optional credentials, workspace identity, writable paths, source status, and the
next safe action without printing secrets. **A brief-change view** should separate
new source evidence, changed observations, corrections, stale/recovered coverage,
and configuration changes. The current diff is only a read-only first step.

**A provenance drawer** should expose the calculation, units, source timestamp,
links, and exclusions behind a number. **A profile editor** should generate valid
JSON, preview coverage and request cost, and show why a setting creates a new era.
**A saved research view** can filter a watchlist or event type without altering
baseline state. These are more directly useful than another unvalidated indicator.

For an optional LLM narrative layer, supply a frozen structured brief and an
allowlisted evidence set. Produce source-linked claims; compute numbers in code;
refuse unsupported comparisons; make model/provider choice optional. Evaluate
numerical fidelity and unsupported claims against held-out examples, and log cost
and latency. External source text is untrusted data. No source text may authorize
tool use, credentials, a publication, or a trade.

Keep full-text data local unless terms and user choice allow sharing. Start with
read models over existing JSON/SQLite. Add a heavier analytical store only when
archive query measurements justify it. Do not add a multi-user backend merely to
make a local research viewer appear more enterprise-like.

## Phase F: earn adoption, then present evidence honestly

After release gates pass, produce one reproducible tutorial: launch the synthetic
demo, inspect a degraded brief, create a profile, and explain a missing result.
Use screenshots from synthetic data only. Add accurate repository topics and a
short description, a clear license/provider distinction, tagged release notes,
and a contributor map. Metadata changes and releases require maintainer action;
they are not performed by this roadmap.

Recruit a small number of willing testers with different workflows: a data
engineer, an individual researcher, and a source/event-focused reviewer. Ask each
to complete a concrete task, record friction with consent, and fix the recurring
problems before broader promotion. Proposed initial goal: five non-maintainer
successful installations and at least three returning users, not an asserted
result or program threshold.

Turn bounded work into well-scoped issues with fixtures and acceptance tests:
provider quota handling, Windows dry-run coverage, profile validation edge cases,
and source-time precision. Welcome contributions that reduce maintenance burden,
not superficial changes made for contribution counts. Publish a technical article
about missing-versus-neutral evidence and a reproducible comparison-window test.
Share where that specific problem is relevant; do not mass-message people, buy
stars, or request reciprocal stars.

Track successful onboarding, repeat voluntary use, externally reproduced bugs,
reviewed contributions, and documented time saved. Keep telemetry off by default;
issue reports and opt-in feedback can establish early evidence. Stars and forks
are secondary discovery signals, not proof of utility.

For a Codex OSS application, describe real maintenance work, actual users or
reproducible downstream use, the project's distinct role, and specific use of
support for parser regressions, review, releases, and security maintenance. Include
only measured evidence. Do not imply that a test count, polished README, or invented
star target guarantees acceptance. Recheck the current program criteria at the
time of application; no application is submitted by this change set.

## Bounded research-agent handoffs for later work

These are task specifications, not a claim that agents have executed them.

| Workstream | Allowed input and output | Acceptance gate |
|---|---|---|
| Provider contracts | Official docs and sanitized public fixtures; one adapter/result contract per PR | All failure states above have deterministic tests; no live keys or invented coverage |
| Instrument identity | Official directory snapshots and public mapping docs; candidate registry proposal | Ambiguity retained; test issues excluded; dates/types/currencies preserved |
| Temporal evaluation | Synthetic event calendars and cleared archives; pure replay tests | Future-row invariance; no production-state writes; no pooled measurement eras |
| Narrative fidelity | Frozen synthetic/source-cleared briefs; claim-to-evidence test set | Numbers match deterministic calculations; unsupported statements fail visibly |
| Adoption research | Public project documentation and consented feedback; one targeted tutorial/tester brief | No outreach sent, no fake users/stars, no private data copied |

Keep approval, consequential writes, security decisions, and final verification
with the maintainer. A cheaper research worker can gather public references and
fixture candidates; it should not silently promote a source, change thresholds,
claim independent validation, or publish on the maintainer's behalf.
