# v3.5 Implementation Changelog

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
- `novelty` is intentionally zero until longitudinal event history exists;
  the system does not manufacture novelty from a single fetched pool.
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

## Version decision

No schema or pipeline-era bump was made.

- `schema_version` remains `2.6` because all public headline fields are
  additive. Existing fields, headline text rendering, source provenance,
  and the legacy record-identity contract remain intact.
- `pipeline_version` remains `2.6.1` because headlines are narrative context
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
