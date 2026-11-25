# Archived-cohort signal verification

The verifier measures what the archived pipeline noticed before subsequent
market moves. It starts with every member of the archived instrumented cohort,
including snapshots with no classifications. This supplies the missing
denominator that an emitted-signals-only ledger cannot provide.

The implemented output is a historical coverage and directional-agreement
diagnostic. It does not reconstruct a private model's decisions, prove that an
opportunity was executable, or calculate portfolio P&L. Supplied hypothetical
exposures and actual decision-completion times belong in
[the portfolio measurement lab](PORTFOLIO_LAB.md).

## What is implemented

| Question | Implemented answer |
| --- | --- |
| Did the configured cohort contain a later large mover? | One observation for every selected archive member and configured horizon, even with zero signals |
| Did a classification appear in the chosen snapshot? | Exact `signal_sightings` join to the selected archive; first emissions and repeated sightings remain distinguishable |
| Did the classification predict a direction? | Directionless capture, a unique direction, and conflicting directions are separate |
| What happens when prices are missing? | The member remains in the matured denominator with an unavailable outcome; it does not become a zero return or a non-event |
| Can rerunning the collector improve the apparent sample count? | Only the latest eligible completed archive per era, frozen cohort, and entry session is selected |
| Can a different watchlist rewrite history? | Membership and identity come from immutable `run_cohorts`, with digest verification |
| Can a failed or demonstration run become a false negative? | Failed, declared synthetic, unknown-quality, and unestablished-time archives are excluded with reasons |
| Can a later download pretend it existed at an earlier cutoff? | Explicit historical cutoffs reject later price acquisitions; acquisition timestamps follow provider completion |
| Are indexes and continuous futures stock opportunities? | They remain visible as unsupported observations and enter neither stock-relative capture nor direction metrics |
| Are the resulting rates validated predictive accuracy? | No. They are descriptive rates within each era/cohort/policy-timing partition, with explicit counts and missingness |

## Freeze a study and retain successive reviews

The monitoring commands are a local bridge to repeated evaluation:

```sh
python -m ledger monitor-init --workspace workspaces/signal-review --archives archive/briefs --policy-id coverage-study-v1 --move-threshold 0.05 --round-trip-cost-bps 10 --entry-latency-seconds 60 --min-observation-days 28
python -m ledger monitor-run --workspace workspaces/signal-review --fetch
python -m ledger monitor-status --workspace workspaces/signal-review
```

The values above are illustrative measurement assumptions, not empirically
estimated thresholds or a recommended trading policy. The initializer freezes
the actual local creation time and assumptions. Repeated reviews retain report
history; they do not silently change the collector or a trading strategy. Each
invocation performs one local review. It does not itself install a scheduler.

For an existing ledger and a separately retained policy, a cache-only report is:

```sh
python -m ledger verify --db ledger/ledger.db --policy workspaces/private-policy.json --out workspaces/verification-001.json
```

Use a new output path for a new review. `--fetch` explicitly permits bounded
read-only market-data acquisition into the existing price cache. Without it,
verification opens the ledger read-only and makes no provider request. An
already-cached larger coherent acquisition can supply a smaller window without
writing a new binding.

An explicit `--as-of` is a strict availability cutoff for both archives and
price acquisitions. A historical bar downloaded today is not evidence that
this system held that price vintage last month. For a current review that
acquires missing outcomes, omit `--as-of`; the report records the market cutoff
fixed at evaluation start and the later price-knowledge cutoff separately.

## Policy contract

`VerificationPolicy.from_dict()` accepts the following complete frozen form:

```json
{
  "schema_version": "signal-verification-policy-1",
  "policy_id": "coverage-study-v1",
  "declared_at": "2026-11-25T12:00:00Z",
  "move_threshold": 0.05,
  "round_trip_cost_bps": 10.0,
  "horizons": [1, 5, 20],
  "entry_latency_seconds": 60,
  "benchmark": "SPY"
}
```

This timestamp is an example, not an attestation. Prefer the monitor's actual
creation receipt to manually backdating a policy file. Unknown fields,
nonfinite values, Boolean numbers, duplicate horizons, and naive timestamps
are refused. Threshold and friction assumptions are required. `.to_dict()`
includes all defaults and `.sha256` identifies the exact canonical policy.

Policy declaration before an archive is labeled `predeclared_policy`; a later
declaration is labeled `retrospective_policy`. These partitions remain separate.
Neither label proves that the model, threshold selection, archived content, or
local clock had independent custody. Retrospective exploration remains useful
for finding operational defects and designing future tests.

## Exact time and return definitions

The candidate is the latest eligible completed archive whose time maps to the
same first eligible entry session. Entry is the first NYSE open **strictly
later** than archive completion plus the declared latency. A snapshot at
exactly the open maps to a later session. A snapshot after the open cannot
replace that day's pre-open evidence. Ties use archive ID deterministically,
without consulting future prices or outcomes.

The legacy session-offset convention is retained: horizon `H` exits at the close
of session `entry_index + H`. Consequently `+1` runs from the entry open to the
following session's close; it is not a same-session open-to-close holding
period. The verifier's strict entry rule is its own explicitly versioned
contract; it does not rewrite the older ledger's at-or-after-open calculations.
Only completed exit sessions mature. Exchange holidays, daylight-saving time,
and early closes come from the schedule, rather than elapsed calendar days.
NYSE publishes its official holiday and early-close schedule. [S1]

For one supported member and one horizon, let

\[
R_i=P^{\mathrm{adj}}_{i,\mathrm{exit\ close}}/
P^{\mathrm{adj}}_{i,\mathrm{entry\ open}}-1,
\qquad E_i=R_i-R_{\mathrm{benchmark}},
\qquad c=\mathrm{round\_trip\_cost\_bps}/10{,}000.
\]

The measured large-move event is `abs(E_i) - c >= move_threshold`. The unique
directional diagnostic is `d_i * E_i - c > 0`, with `d_i=1` for the archived
long label and `d_i=-1` for its short label. Directionless classifications and
conflicting directions do not receive directional hits.

This is an exact definition of a **linear relative-return hurdle**. It is not
an exact cost-adjusted long/short transaction: spread, financing, borrow,
settlement, fillability, tax and hedged capital are not reconstructed. The
portfolio lab has separate path accounting and explicit transaction-cost
scenarios. Costs change implementability and capacity, so a profitable-looking
gross event statistic should not substitute for that measurement. [S2]

Both endpoints must come from one compatible immutable price acquisition.
Provider, daily interval, adjustment basis, acquisition time and content hash
are retained. Known currency mismatches are refused. If both existing currency
fields are missing, their absence remains visible; it is not verification of
currency or exchange identity. Each stock/benchmark comparison requires both
complete endpoint pairs. An incomplete cohort is never rescaled into a
complete fixed-basket benchmark.

Adjusted endpoints are useful for economic-return comparisons, but they do not
prove corporate-action completeness. yfinance's maintainer documents adjustment
and repair behavior, including reconstructed observations and possible repair
errors. A later data correction should create a review with a distinct retained
price basis, not overwrite an earlier result without evidence. [S3]

## Denominators and missed-move classes

`expected_observations` counts selected cohort members across configured
horizons. `matured_expected = observed + unavailable` includes supported members
whose exit session has closed. Pending and unsupported instruments have their
own counts. The top-level summary pools **counts**, not directional accuracy
rates; the rates appear only within era, cohort and policy-timing segments.

Within each horizon segment:

\[
\text{outcome coverage}=\frac{N_{\mathrm{observed}}}{N_{\mathrm{matured}}},
\qquad
\text{observed event capture}=
\frac{N_{\mathrm{observed\ large\ moves\ with\ a\ signal}}}
     {N_{\mathrm{observed\ large\ moves}}}.
\]

The second quantity is conditional on observed complete pairs. It must be read
beside the first. A high capture rate with many unavailable outcomes does not
establish high recall over the original population. A zero denominator yields
an unavailable rate, not zero percent or perfect accuracy.

| Recorded class | Interpretation |
| --- | --- |
| `no_recorded_signal` | No temporally admissible ledger classification exists in the selected snapshot |
| `directionless_only` | The pipeline noticed an event, but its classification made no directional statement |
| `direction_matched` | The unique archived direction agrees with the subsequent relative move |
| `opposite_direction_only` | The unique archived direction disagrees with the subsequent relative move |
| `conflicting_directions` | The snapshot contains both directions; no unique prediction is credited |

`no_recorded_signal` is an observation, not a causal diagnosis. It can reflect a
source gap, missing feature, discarded candidate, intentionally strict filter,
stale source, parser failure, or absence of genuinely informative public
evidence. The current ledger does not retain every upstream decision needed to
distinguish those possibilities. Review the original archive, source health,
headline drop reasons and evidence-history provenance before assigning a cause.

Likewise, “large mover inside this archived cohort” differs from “best trade in
the entire market.” Historical whole-market recall needs a separately archived
eligible listing universe. Nasdaq's directory identifies fields such as ETF and
test-issue status and includes a creation timestamp; its current file is not
by itself a history of past membership. [S4]

## Signal-family diagnostics

`family_diagnostics` partitions observations by era, frozen cohort,
declaration timing and horizon. Each group includes complementary populations
with and without recorded classifications, plus an overlapping slice for each
recorded family. Counts refer to instrument/entry-session observations, not the
number of repeated records. Every slice retains missing and pending outcomes,
distinct entry-session counts and its observed-outcome denominator.

`large_move_yield_observed` is large moves divided by observed comparisons within
that slice. `event_capture_rate_observed` uses all observed large moves in the
group as its denominator. The no-classification slice has zero captured events;
its `large_move_share_observed` instead describes the missed-event share.
Empty denominators remain null. Mean absolute excess is descriptive and does
not apply another friction deduction.

Families can share observations. Their counts and capture rates cannot be
added as independent contributions, and the combined row-level direction is
not assigned to each individual family. Different selection and price coverage
can explain different observed yields; these slices are not causal ablations,
calibrated probabilities or strategy returns.

## Archive-quality evidence and migration

`ledger.ingest` now adds `run_evidence`, bound to the same immutable archive hash
as `runs`. It retains the source schema, explicit timestamp provenance,
top-level health, error/stage-failure counts and declared synthetic markers.
It leaves legacy signal extraction and outcome definitions unchanged.

A schema migration creates an empty evidence table. It never populates old
rows with invented `OK` values. Replay the original archive bytes to supply
the missing evidence. Existing archive or metadata contradictions are refused.
The verifier excludes an old run whose quality has not been re-established,
while showing the reason in `excluded_runs`.

Healthy `WARN` archives with no hard errors remain eligible because expected
provider degradation is not equivalent to a failed run. Their warning status
stays in each selected-run receipt. `ERROR`, nonempty hard errors, failed
pipeline stages, unrecognized health and reconstructed/naive archive times
do not enter the verified population. Declared demo/synthetic/fixture markers
are checked in supported metadata containers as well as top level. An absent
marker is recorded as `not_declared`, not proof that the content is real.

## What a useful multiweek review should do

The first review should establish capture reliability: did scheduled runs
arrive, did the cohort remain stable, were prices acquired for all matured
members, did source health degrade, and did any identity/timestamp mismatch
occur? These are operational measurements that several weeks can meaningfully
improve. They do not require pretending that the same period proves an edge.

Keep the baseline policy frozen while candidate changes run separately. Review
each horizon only after its own label has matured. A 20-session outcome requires
20 later sessions after entry; many signals emitted near a study's last day
will remain pending at that day's review. Retain the later maturation reviews
instead of dropping the final cohorts.

Weekly inspection may guide operational repairs. Changes to feature selection,
thresholds, labels, source composition or ranking rules create a new experiment
identity. They cannot retrospectively inherit an untouched holdout. Retain all
tested versions, including failures: financial backtests are vulnerable to
selection and repeated-testing bias, and combining many selected signals can
amplify it. [S5, S6]

The current event rates have same-date market dependence and overlapping
horizons. Counting hundreds of ticker/session rows does not produce hundreds
of independent observations. Before adding inferential intervals, define the
comparison and resample complete date blocks while retaining the cross-section
together, or evaluate a genuine daily portfolio path. Block-bootstrap methods
have assumptions about dependence and stationarity; they are not an automatic
certificate under structural market changes. [S7]

## Research extensions in priority order

### 1. Preserve the full candidate decision path

Add append-only rows keyed by archive/run, stable instrument/listing ID,
source/event identity and stage. At minimum retain `known_at`, source-time
precision, observed time, feature-vector identity, rule/model identity,
eligibility, decision, rejection reasons, and the exact selected or withheld
source IDs. Preserve explicit abstentions and provider failures.

This allows a missed-move review to distinguish “never in the universe,” “in the
universe but unavailable,” “seen and rejected,” and “noticed without direction.”
Only the last two can directly diagnose selection rules. A cause inferred from
today's data should remain a retrospective hypothesis until a controlled replay
or prospective comparison confirms it.

### 2. Build historical listing and corporate-action evidence forward

The existing dated instrument registry is the right place for issuer,
instrument, listing and provider-binding identities. Retain directory snapshots
and dated positive termination evidence. A ticker's absence from a provider is
not proof of delisting. A symbol reused by a new company must not join the old
company's price path. Different share classes remain different instruments.

Do not remove a delisted or hard-to-price security from the historical
denominator simply because a retail price endpoint no longer supplies it.
CRSP's documented delisting-return treatment illustrates why a terminal return
and a missing terminal valuation are distinct cases. Until terminal economics
are known, publish the unresolved exposure and its effect on completeness. [S8]

### 3. Evaluate new information through controlled ablations

For each added source or feature family, preserve the same eligible universe,
decision times, candidate count/budget, target and baseline. Compare the existing
pipeline, the new pipeline and an ablation without the new source on identical
future cohorts. Track factual fidelity and relevant event capture as well as
directional return diagnostics; also retain source cost, latency and outage
burden. More collected text is not itself additional independent evidence.

When using financial statements or event releases, source availability must be
separate from the period the fact describes. The SEC distinguishes acceptance,
filing and report-period timestamps; treating fiscal-period end or a date-only
filing label as an exact availability instant leaks information. [S9]

### 4. Add calibrated probabilities only after defining the event

A family count, ordinal score or confluence value is not an estimated chance of
success. A future probability model must name its target, horizon, availability
cutoff and population, then emit immutable probabilities before outcomes exist.
Assess Brier score, log loss and reliability-bin counts against a training-only
base-rate forecast. Brier loss mixes calibration and discrimination, so pair it
with calibration curves rather than calling every reduction better
calibration. Fit calibrators on temporally separate data. [S10]

### 5. Use purged chronological model comparisons

Train, tune and test in chronological order. For any training example with label
interval `[entry, exit]`, remove it if that interval overlaps the future
validation block. Ensure every training label had matured before fitting.
Group all securities from a decision session together; a row-count gap is not
necessarily a session gap in a panel. Fit imputation, scaling, feature selection,
calibration and threshold selection inside the training process.

scikit-learn's time-series splitter provides ordered folds and an optional gap,
but the documented gap is a number of samples. A variable-horizon financial
panel needs explicit label-interval logic on top. Keep a final future period
unavailable for feature selection and record every trial, rather than choosing
the best displayed result. [S11, S5]

### 6. Define attainable counterfactuals before calling something a missed trade

An eventual maximum price is an oracle diagnostic, not an attainable exit.
Compare supplied strategies under one frozen entry/exit policy, capital budget,
position limits, eligible instruments and cost assumptions. Keep overlapping
positions, cash and exposure constraints in the portfolio path. A list of all
winning securities cannot be added up as if the same capital bought each one.

If daily bars cross both a stop and target, their order is unknown without
finer observations. Record ambiguity or conservative/optimistic bounds; do not
quietly choose the favorable path. Auction gaps, halts, OTC quote quality,
delistings and short borrow require their own execution evidence. MCTS or other
planning research should consume this well-defined decision environment after
the measurement layer is stable.

## Python API and verification

```python
from ledger.verification import VerificationPolicy, build_verification, render_markdown

policy = VerificationPolicy.from_dict(saved_policy)
report = build_verification("ledger/ledger.db", policy=policy, fetch=False)
markdown = render_markdown(report)
```

Tests in `test_signal_verification.py` use fabricated data and cover zero-signal
winners, complete population accounting, unavailable benchmark/member data,
strict entry timing, rerun deduplication, conflicting directions, repeated
sightings, price-acquisition cutoffs, future source records, unsupported asset
classes, currency mismatches, policy immutability, quality migration and
holiday/early-close timing. `test_ledger_subwindows.py` additionally verifies
read-only subwindow reuse and acquisition-completion timestamp integrity.

## Primary references

- **[S1]** NYSE, [Holidays & Trading Hours](https://www.nyse.com/trade/hours-calendars), checked November 25, 2026.
- **[S2]** Frazzini, Israel and Moskowitz, [Trading Costs of Asset Pricing Anomalies](https://pages.stern.nyu.edu/~afrazzin/pdf/Trading%20Cost%20of%20Asset%20Pricing%20Anomalies%20-%20Frazzini%2C%20Israel%20and%20Moskowitz.pdf), author-hosted research, 2012 draft. It motivates measuring actual costs; its institutional estimates are not this bot's tariff.
- **[S3]** yfinance maintainer, [PriceHistory API](https://ranaroussi.github.io/yfinance/reference/yfinance.price_history.html) and [Price Repair](https://github.com/ranaroussi/yfinance/blob/main/doc/source/advanced/price_repair.rst), checked November 25, 2026.
- **[S4]** Nasdaq Trader, [Symbol Directory Definitions](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs), checked November 25, 2026.
- **[S5]** Bailey, Borwein, Lopez de Prado and Zhu, [The Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb), *Journal of Computational Finance*, 2017, DOI 10.21314/JCF.2016.322.
- **[S6]** Novy-Marx, [Backtesting Strategies Based on Multiple Signals](https://www.nber.org/papers/w21329), NBER Working Paper 21329, 2015.
- **[S7]** Politis and Romano, [The Stationary Bootstrap](https://doi.org/10.1080/01621459.1994.10476870), *JASA* 89(428), 1303–1313, 1994; [original Stanford technical-report record](https://statistics.stanford.edu/technical-reports/stationary-bootstrap).
- **[S8]** CRSP, [US Stock & Indexes Database data definitions](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-data-descriptions-guide-crspaccess/), especially returns and delisting returns.
- **[S9]** SEC, [Webmaster Frequently Asked Questions](https://www.sec.gov/about/webmaster-frequently-asked-questions) and [EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), checked November 25, 2026.
- **[S10]** scikit-learn, [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html), checked November 25, 2026.
- **[S11]** scikit-learn, [TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit), checked November 25, 2026.
