# Measurement-integrity release — 2.6.5

This release repairs measurement integrity found by an external methodology
review of 2.6.4. It changes measurement semantics, so the pipeline and ledger era
is 2.6.5. Daily export schemas keep their identifiers: 2.8 by default, 2.9 for
explicit profiles. Both now accept era 2.6.5. Historical 2.6.4 and older archives
keep their original bytes and era, and remain readable.

Each correction below has a regression test registered in
`scripts/run_offline_checks.py`. The review's acceptance IDs are given in brackets.

## Prices and outcomes

- **One acquisition per window [M04, M05].** A ledger return window resolves both
  endpoints from one immutable price acquisition. The acquisition records provider,
  interval, requested range, adjustment convention, currency when known, acquisition
  time and content hash. Overlapping refills and vendor revisions cannot replace only
  one endpoint. Split and dividend conventions get distinct identities.
- **Legacy rows.** Legacy `(ticker, session)` rows are neither rewritten nor assumed
  compatible. A new strict calculation refetches the complete window, or stays
  unavailable.
- **Frozen cohorts [M06].** The benchmark cohort is the one frozen in the archived
  brief, not today's configuration. A cohort that cannot be verified refuses the
  benchmark and keeps the raw return.
- **Partial benchmarks [M07, M08].** A partial benchmark keeps `univ_ret` null and
  records expected, covered and missing members and the weights. It is never
  renormalised into a full-basket value. Raw maturity and benchmark maturity are
  separate, and every published state is an append-only row in `outcome_revisions`.
  A later recovery appends a revision and keeps the earlier one.
- **Arrival order [M16].** Signal sightings are retained. The earliest and latest
  observation do not depend on the order archives are ingested.

## Statistics

- **Matched energy [M09].** The ratio uses the same complete-benchmark pairs in its
  numerator and denominator, and the sample gate applies to those matched pairs.
  `n_benchmark_pairs` is reported next to the raw `n`. A basket move within 1e-12 of
  zero leaves the ratio undefined.
- **No intervals without a design [M10].** Many tickers on one session are not
  independent observations. The IID bootstrap is removed, so every cell reports
  `mean_ci95: null` and `inference_status: "ineligible"`, together with the count of
  distinct entry sessions. Stats schema 2.5 becomes 2.6.
- **Prospective holdout [M11, first part].** Holdout `holdout-1` covers entry sessions
  on or after 2026-10-01 and was declared on 2026-09-22, before those sessions existed.
  Windows that cross the boundary are purged. Cells report their development, purged
  and holdout counts. The calendar-block bootstrap is not built, so intervals stay
  withheld.

## Observations and timing

- **Cash flow [M17].** Missing or non-finite operating cash flow no longer becomes
  zero burn, GREEN or a 999-quarter sentinel. Missing debt and market capitalisation
  stay null. Free cash flow and non-current liabilities are not relabelled as
  operating cash flow or total debt.
- **Social attention [M12–M14].**
  - A ticker ranked below the fetched leaderboard depth is `censored`, not zero.
    Failed or partial collections do not advance baselines or absence history, and a
    genuine source zero stays zero.
  - `ATTENTION_BIRTH` is renamed `TOP200_ENTRANCE` and requires complete, comparable
    snapshots.
  - Social state moves to a new, versioned file; the old file is left untouched.
- **Strictly earlier baselines [M01–M03].** Baselines use strictly earlier sessions in
  chronological order. Strict mode applies a known-by cutoff and refuses rows that
  have no knowledge time. A market-cap cache with a negative age is rejected, and
  historical callers never fetch a current value.
- **Publisher breadth [M15].** The focus component keyed `independent_corroboration`
  is documented and rendered as publisher breadth. Independence of reporting origins
  is not assessed. The wire key is kept for schemas 2.6–2.9.

## New tools

- **Lean brief.** `lean_brief.json` is a labelled projection of each brief, about 5% of
  its size, for an operator who chooses to share a brief with a language model. It is
  not the canonical record and is not for redistribution. Dated copies are kept in
  `lean_briefs/` for 7 days.
- **Legacy narrative route disabled.** The optional Discord/Ollama narrative route
  returns a typed refusal. It has no frozen evidence, source-purpose approval or claim
  validation.
- **Clock guard.** The scheduled launcher checks the host clock against network time
  before export. It refuses the run on a skew over 5 minutes, or when the clock cannot
  be verified, because every written timestamp comes from the local clock.
- **Evidence history.** `event_history/` is an append-only sidecar that indexes archived
  briefs by exact bytes. It is described in the README ("Evidence history").
  - Covers E01–E08 and the full M15/M16 cases: stable anchors, scoped amendments,
    retained contradictions, abstaining unknown-timezone dates, append-only links with
    cycle refusal, and as-operated versus retrospective views.

## Compatibility

- **Existing data:** readers of 2.8/2.9 exports work unchanged, apart from typed nulls
  where a value was previously invented, and the renamed social tag. Existing ledgers
  migrate additively on the next `ledger update`.
- **Legacy outcomes:** outcomes filled before the revision contract keep their
  published values, but are excluded from matched energy until
  `python -m ledger rebuild` regenerates them.
- **Unchanged:** empty provider responses remain retryable, and valid raw, SPY and
  excess return fields keep their meaning.

## Remaining limits

- **Intervals and linking:** there are no calibrated or inferential intervals, and no
  exact claim-origin linking inside exports. The latter exists only in the evidence
  history, from explicit decisions.
- **Confluence timing:** confluence has no knowledge-time rule of its own, and there is
  no strict replay entry point.
- **Adjustment labels** record the requested provider convention. They do not certify
  every corporate action.
- **Instrument keys and currency:** the ticker remains the ledger instrument key, and
  currency may be unknown.
- **EDGAR times:** SEC `acceptanceDateTime` values are copied as EDGAR returns them.
  For some accessions, EDGAR's value differed by several hours between an early and a
  later capture, so filing time is not yet reliable for ordering against a session.
- **Not this release:** issuer-change packets, a dated listing registry and
  coverage-expansion budgets. These are later roadmap work.
