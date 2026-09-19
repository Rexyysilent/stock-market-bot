# v3.5 Implementation Changelog

> Historical checkpoint from August 18, 2026. Later integrated work supersedes
> several deferred-status statements below. See
> [`docs/RELEASE_NOTES.md`](../../docs/RELEASE_NOTES.md) for the current mapping;
> the original record is retained so the earlier release evidence remains auditable.

## 2026-08-18 - OpenInsider acquisition hardening

- Replaced two independently timed OpenInsider scrapes with one run-scoped
  `ALL`/30-day/500-row acquisition shared by social narrative formatting and
  insider-cluster derivation.
- Reused one HTTP session, serialized source access behind one rate gate, and
  added up to three jittered exponential retries (approximately 2, 5, and 10
  seconds) only for connection failures and timeouts.
- Classified source failures as `connection_refused`, `connection_error`,
  `timeout`, `http_403`, `http_429`, other `http_<status>`, `parser_failed`,
  `untrusted_response_url`, or `unexpected_error`. Unified health now records
  attempts/retries, delay history, live/run origin, request scope, undated-row
  diagnostics, and cache provenance instead of exposing only one of two scraper
  instances.
- Added the atomic last-known-good normalized-row cache at
  `state/openinsider_last_good.json` for the daily `ALL`/30-day/500-row scope.
  Nondefault scopes use distinct scope-safe filenames and cannot overwrite the
  daily cache. A cache hit sets `run_origin=stale_cache`; it is explicitly
  stale and narrative-only, so it cannot supply insider-cluster or confluence
  evidence.
- Preserved SEC EDGAR Form 4 as the insider-cluster fallback and made SEC
  health expose `openinsider_cluster_rows_considered`,
  `openinsider_cluster_rows_eligible`, authoritative categorized eligibility
  drops, cluster source, fallback use, and `insider_cluster_fallback_reason`.
  Canonical acquisition preserves dated rows for this downstream audit; its
  reserved future/out-of-window drop counters therefore remain zero in 2.6.2.
  SEC drop categories distinguish stale provenance, undated, future, and
  out-of-window rows. Fallback reasons distinguish stale-cache rejection,
  source unavailability, temporal ineligibility, no watchlist rows, and no
  qualifying cluster.
- Tightened OpenInsider cluster eligibility: every constituent must come from
  the live run, have a parseable filing date, be no later than the run clock,
  and fall inside the trailing 30-calendar-day window. Undated, future-dated,
  out-of-window, and cached rows are rejected before clustering.

`schema_version` remains `2.6` because no public field was removed or changed
incompatibly. `pipeline_version` advances from `2.6.1` to `2.6.2`: the stricter
cluster constituent population can change confluence membership and downstream
ledger observations, so new results must start a fresh statistical era. The
schema continues to accept unmarked legacy 2.6.0/2.6.1 artifacts and marked
2.6.1 headline-contract artifacts; marked 2.6.2 artifacts use the same additive
headline sub-contract.

## 2026-08-18 - Post-review integration follow-up

A focused review of the PR 1/PR 2 implementation produced these corrective
changes. Deferred PR 3 and later roadmap work remains outside this follow-up.

- Made exchange-listing parsing contextual: valid NASDAQ, NYSE, AMEX, and
  NYSEARCA colon/dash or `-listed` metadata is stripped, while genuine exchange
  subjects remain eligible for the macro lane.
- Matched explicit title and provider symbols against the complete
  `ALL_TICKERS` set, including the display alias `VIX` -> `^VIX`.
- Preserved universe association across same-story dedupe by unioning
  `universe_tickers`, taking per-component maxima, and then recomputing the
  representative lane.
- Added lane-scoped source diversity: acquisition providers are capped at
  three overall and two per lane, publishers at one per lane, while the
  existing Google News and press-release limits remain in force.
- Defined `score_components.novelty` relative to the fetched pool: a singleton
  scores one and any syndicated/same-story copy scores zero. This is not a
  claim of longitudinal event novelty.
- Added `data_quality.headline_contract_version = "2.6-headline-lanes-1"`.
  Unmarked legacy schema-2.6 exports remain valid; marked exports require
  headline lane fields, strict pool accounting, and typed dropped rows.
- Preserved full normalized drop provenance, including links, canonical URLs,
  source and observation times, source class and IDs, provider tickers,
  summaries, and duplicate lineage.
- Supplied zero-safe pool accounting when news collection fails soft, keeping
  candidate accounting complete and marked exports schema-valid.
- Audited every secret-baseline finding as an explicit false positive, made CI
  reject unknown or confirmed-secret decisions before its tracked-file scan,
  and pinned GitHub Actions dependencies to immutable commit SHAs.

The adversarial follow-up also adds:

- Compatibility fixtures cover stored unmarked schema-2.6 shapes from pipeline
  2.6.0 and 2.6.1, including early briefs without `headline_pool` or selected
  provider fields; marked output retains the strict contract for both stored
  2.6.1 artifacts and newly emitted 2.6.2 artifacts.
- Marked selected and dropped rows carry full provenance, while semantic
  accounting reconciles selected counts, lane histograms, and candidate totals
  and rejects fetched-count contradictions.
- Contextual guards distinguish uppercase exchange/common words from actual
  ticker symbols and prevent corporate uses of macro vocabulary from creating
  false macro subjects without suppressing genuine exchange coverage.
- Same-story handling uses precomputed normalized URL/title/token features and
  shared transitive grouping semantics for novelty and dedupe, removing
  repeated pairwise normalization and redundant primary-pool processing.
- A deterministic synthetic 250-candidate stress cohort covers grouping,
  selection, provenance, and accounting stability; it records no final timing
  claim.

## 2026-08-18 — PR 1 and PR 2 completed

Implementation commit: `f788e2b` (`Add CI baseline and editorial headline lanes`)

This iteration implements only the first two work packages from
`handoff.md`: a reproducible CI baseline and the headline-relevance/editorial
lane repair. PR 3 and later roadmap items remain deliberately deferred.

## What changed

### PR 1 — reproducible CI baseline

- Added required GitHub Actions checks for Python 3.11 and 3.12.
- Added clean, hash-locked installs through `requirements.lock` and
  `requirements-ci.lock`, while retaining readable dependency inputs in
  `requirements.txt` and `requirements-ci.txt`.
- Constrained NumPy below 2.5 because NumPy 2.5 requires Python 3.12 and made
  the supported Python 3.11 matrix impossible to install.
- Added one deterministic offline runner covering 18 existing and new
  regression scripts. Live provider checks remain outside required CI.
- Added a Draft 2020-12 JSON Schema, a synthetic valid export, and negative
  regressions proving malformed exports fail validation.
- Added dependency auditing with `pip-audit` and tracked-file scanning with
  `detect-secrets` plus a reviewed false-positive baseline.
- Made the earnings-calendar fixture use an injected fixed UTC reference date.
  It previously mixed `date.today()` with the pipeline UTC run context and
  could fail at local/UTC date boundaries.
- Expanded Git ignores for brief snapshots, lock-generation environments, and
  local continuity/research artifacts.

### PR 2 — headline relevance and editorial lanes

- Added three explicit headline lanes:
  - `universe`: requires a mapped configured issuer or instrument.
  - `macro`: requires a genuine macro or market-structure subject.
  - `discovery`: requires explicit high impact plus either an approved
    vertical or official authority.
- Added separate score components instead of presenting one composite score
  as ground truth:
  - `issuer_relevance`
  - `macro_relevance`
  - `vertical_relevance`
  - `authority`
  - `novelty`
  - `impact`
- At the initial PR 2 checkpoint, `novelty` was intentionally fixed at zero;
  the post-review follow-up above replaces that placeholder with deterministic
  fetched-pool-relative novelty without claiming longitudinal event novelty.
- Exchange-listing metadata such as `(NASDAQ: DUOT)`, `NYSE: XYZ`, and
  `AMEX: XYZ` is stripped before macro scoring. The exchange name can still
  qualify when it is the actual subject of market-structure coverage.
- Preserved source-time freshness gates, provider/publisher provenance,
  syndication dedupe, publisher and press-release caps, Google caps, and
  deterministic ordering.
- Preserved the legacy `relevance` field as a compatibility measurement while
  using lanes and component scores for editorial selection.
- Added `lane`, `universe_tickers`, and `score_components` to structured
  headline rows and corresponding data-quality exclusions.
- Added `no_approved_lane` exclusions and candidate accounting so every raw
  candidate resolves to selected, relevance-excluded, stale, duplicate, or
  cap/limit-excluded output.

## Frozen August 17 acceptance fixture

`fixtures/headlines/2026-08-17.json` reproduces the reviewed editorial defect
without network calls or access to production state.

The deterministic result is five selected records:

- Universe: TSLA, NXE, ROKU
- Macro: Federal Reserve policy, genuine Nasdaq market-structure event

The following outside-universe records do not receive a universe or macro
lane merely because they contain a listing prefix or broad company language:

- DUOT
- TRV/ALL/CB insurance coverage
- EYPT
- ABNB

The stale AMAT control remains excluded by the original three-day freshness
gate even though it maps to the configured universe. Reversing upstream input
order produces the same selected records in the same order.

## PR 1/PR 2 version decision

At the PR 1/PR 2 checkpoint, no schema or pipeline-era bump was made. The later
OpenInsider eligibility change documented above starts pipeline era 2.6.2; the
following statements record why the headline work itself remained in 2.6.1.

- `schema_version` remains `2.6` because all public headline fields are
  additive. Existing fields, headline text rendering, source provenance,
  and the legacy record-identity contract remain intact.
- `pipeline_version` remained `2.6.1` at that checkpoint because headlines are
  narrative context
  and are not Tier-1 Signal Ledger inputs. This work does not change baseline
  eligibility, confluence membership, signal definitions, event lifecycles,
  or ledger identity/populations.
- Any later change to those signal populations—especially the family-specific
  confluence lifecycles planned in PR 5—must start a new pipeline era and keep
  historical statistics segmented.

## Verification evidence

The committed tree passed:

- clean hash-locked installation on Python 3.11;
- clean hash-locked installation on Python 3.12;
- compile checks on both versions;
- JSON Schema validation and malformed-document regressions;
- all 18 deterministic offline checks on both versions;
- dependency audit: no known vulnerabilities;
- staged/tracked-file secret scan: clean;
- `git diff --check`.

The tests used fixtures and temporary environments only. No production
`state/`, `archive/`, or `briefs/` migration was performed.

## Deferred work

The following v3.5 handoff items were not started in this implementation:

- dynamic editorial focus and replacement of the hardcoded deep dive;
- social partition reconciliation and human-renderer noise suppression;
- family-specific confluence evidence lifecycles;
- quote/session timestamp semantic expansion;
- direct-versus-proxy X/Twitter source identity;
- filing-grade cash-runway and earnings provenance;
- canonical entity graph;
- research/commercial source-rights modes;
- uranium vertical pod expansion;
- sellable human brief surfaces.

These should remain separate, reviewable changes rather than being folded into
the completed CI and headline-relevance baseline.
