# Pipeline quality and listing audit

Audit date: **25 November 2026**. Recovered source baseline:
`dc6e4cde3cfe0a521c9eeff9ec9b355086964367`. This note describes implementation,
public listing evidence and research priorities. It does not establish live
forecast accuracy. Private archives and raw provider captures remain outside
the repository.

## Assessment

The useful next step is to improve the evidence denominator before increasing
model complexity. The project already preserves immutable briefs, source and
observation clocks, version-separated classifications, exact session windows,
and a separate hypothetical portfolio lab. These are good foundations for a
prospective study. They do not by themselves show that a classification
predicts a profitable outcome.

Three quantities must remain distinct:

1. **Collection quality:** eligible listings, available sources, fresh records,
   valid identity and session mappings, and unresolved failures.
2. **Information quality:** correct extraction and event attribution, novel
   evidence, directional meaning when explicitly supplied, and incremental
   prediction beyond price and market controls.
3. **Economic quality:** delayed hypothetical execution with costs, turnover,
   drawdown, exposure and realistic liquidity constraints.

The new whole-cohort verifier measures a subset of the first two. The existing
portfolio lab measures the third under explicit assumptions. The MCTS prototype
isolates conditional planning behavior. None should borrow another component's
success label.

## Repairs implemented

| Observed defect or gap | Change and reason |
|---|---|
| Shared freshness admitted future timestamps | Bound source time at the run clock and retain the rejection reason; start era 2.6.6 because classification eligibility changes. |
| A stale social response could suppress a usable fallback | Apply freshness before deciding whether fallback is needed and before duplicate selection. |
| Packet grouping could conflate different qualified subjects with the same suffix | Preserve full issuer/instrument subject identifiers in grouping and presentation. |
| Snapshot capture could replace retained evidence, and refresh could mutate the input registry | Refuse occupied capture targets and deep-copy registry enrichment. Preserve original knowledge time when a fact is unchanged. |
| A directory response could be partial or structurally ambiguous | Track part completeness, invalid and duplicate rows, footer time, hashes and capture time separately. Suppress denominator claims for unusable captures. |
| Signal-only rows hide cohort members that received no classification | Evaluate the frozen archived membership, including zero-classification members and unavailable outcomes. |
| Repeated intraday runs can inflate the sample | Select one admissible run per era, frozen cohort and eligible entry session, then label its declaration timing. |
| Failed or synthetic archives can masquerade as no-signal observations | Preserve run quality evidence during ingestion and exclude unusable runs explicitly. |
| Several short horizons could repeatedly acquire the same price history | Reuse a coherent compatible immutable larger acquisition while preserving provider identity and adjustment vintage. |
| A request-start timestamp could make a slow response appear available too soon | Stamp new acquisition availability at response completion, separately from retry bookkeeping. |

Run selection, time availability, horizon maturity and missing-price handling
are covered by synthetic regressions. Offline tests verify behavior of supplied
inputs; they do not certify live upstream reliability.

## Dependency maintenance discovered during verification

The current dependency audit reported four advisories against the baseline
`soupsieve==2.9.2`, pulled in by `beautifulsoup4==4.15.0`:
CVE-2026-126190, CVE-2026-126191, CVE-2026-126192 and CVE-2026-126193.
The minimal fixed version clearing all four reported ranges is `4.0` (the
published spelling of the advisory's `4.0.0` floor). The patch uses the exact
two-hash dependency hunk already proposed in open PR #13; its unrelated changes
are not merged by this work.

BeautifulSoup's declared dependency range permits the upgrade. The maintainer's
[release metadata](https://pypi.org/project/soupsieve/4.0/) requires Python
3.11.5 or later, so setup guidance and the local diagnostic now disclose that
floor. The [changelog](https://facelessuser.github.io/soupsieve/about/changelog/)
also describes selector behavior changes, which makes fresh hash-locked
installs and the normal parser/offline regression suite necessary. This is a
dependency-version remediation, not a new vulnerability discovery or a claim
that all provider-input paths were security-reviewed.

## Current public listing coverage

The committed registry contains **42 instruments**: 29 instrumented collection
targets and 13 editorial-only targets. The registry records 38 snapshot-matched
identities, one primary-verified identity and three unverified entries. All 42
listing starts are unknown. It contains no dated provider capability
observations and no provider bindings. Those absences mean unknown coverage,
not proven unavailability.

A fresh directory-only capture at **2026-11-25 15:26:25 UTC** contained 5,609
Nasdaq rows and 7,703 other-listed rows, or 13,312 unique directory symbols.
Both directory parts were complete; SEC enrichment was not requested. The
directory's own footer read `1125202610:01`; its timezone was unspecified and
was not silently interpreted as the capture clock.

Under the audit's conservative name/flag-based common-stock, ADR and ETP
classification, **11,373 symbols** qualified for its diagnostic denominator.
It found **25 instrumented** and **13 editorial-only** configured symbols in
that subset. The instrumented fraction is therefore about **0.22% of this
particular current directory subset**. It is not the percentage of historical
opportunities captured, nor a statement about economically useful coverage.
ETPs are included; this is not an operating-company count.

The captures' SHA-256 identities are:

- Nasdaq: `41341df9ed3e80adb248a266f4ab117cc3da345414bbed6c58ed53d7b952fc49`
- Other-listed: `dfd2bac829002ce52b148642ea326bf2296f662feea8a59a26af3b0c5fcbc6fc`

Nasdaq documents separate symbol, exchange, test-issue, financial-status and ETF
fields. Those fields support a coverage audit; they do not establish a price
provider's symbol mapping, liquidity or historical membership. [S1]

### Concrete listing maintenance case: ROKU

The configured ROKU symbol was absent from the current directory. That absence
alone is not evidence of delisting. A separate primary-source check found
Nasdaq's corporate-action notice: merger closing and last trading date
6 November 2026, with suspension effective 9 November. Roku's 8-K also describes
the merger, requested trading halt and removal from listing. [S2, S3]

This is a reason to maintain dated corporate-action assertions and distinguish
current collection eligibility from historical cohort membership. It is not a
reason to delete ROKU from earlier archives. A historical price gap must remain
unresolved until the missing history is obtained from a suitable source.

`SI=F` and `^VIX` are a futures proxy and an index, respectively. Their absence
from an equity directory is expected and their stock-return measurement basis
is unsupported by this verifier. STTDF also requires separate venue, identity
and provider evidence; a Nasdaq directory cannot certify its OTC coverage.

## Prioritized improvements after this patch

### First: complete the prospective evidence record

Connect the actual collector's immutable archive directory to the frozen
monitor. Supply a real SEC application/contact identity before a full collector
run. Retain exact source payload identities, source health, observation times
and all eligible members even when no classification was emitted. Keep changed
eras and changed universes separate. New calibration or thresholds require a
new declaration rather than rewriting the original study.

Maintain at least three different missingness counters: acquisition failure,
classification absence, and unavailable outcome. Keep a listing/corporate-action
queue for symbols that disappear or change identity. Each decision needs dated
primary evidence; daily directory absence is only a review trigger.

### Second: collect the rejection trail needed for causal diagnosis

The verifier can distinguish no recorded classification, directionless
classification, matching direction, opposing direction and conflicts. It cannot
reconstruct why every unretained source candidate was rejected. A useful next
schema addition is a bounded candidate-disposition ledger: source record and
document hash, instrument mapping result, publication/observation times,
freshness result, deterministic score components, thresholds, selected/rejected
status, and the exact rule or constraint responsible.

This would separate an absent source, a mapping failure, a stale arrival, a
deliberate suppression, and a weak forecast. Those require different repairs.
Do not label all five a predictive miss.

### Third: test simple predictive baselines

Begin with declared targets, price-only controls and frozen deterministic
classifications, followed by a regularized event model and a restricted tree
ensemble. Evaluate proper probabilistic scores and calibration on later data.
Rank or confidence scores are not probabilities without that contract. Track
all attempted variants, including failed ones; selection among many backtests
is itself a source of optimism. [S4, S5]

Preserve later-session entry and fixed cost assumptions. Measure incremental
information separately from hypothetical portfolio value. Cohort members on
one session and overlapping long horizons are dependent observations; the
number of rows is not an independent sample size.

### Fourth: evaluate planning and larger machines separately

Use the same frozen forecasts, scenarios, costs and constraints for MCTS,
myopic and random-search comparators. Search effort can improve optimization
within a supplied model; it cannot demonstrate that the model describes future
prices. In the small fabricated prototype both MCTS and random shooting recover
the exhaustive optimum, so there is no demonstrated MCTS advantage.

The [research programme](mcts-and-predictor-programme-20261125.md) covers event
forecasting, MCTS planning, symbolic feature search, volatility/abstention,
foundation-model shadow evaluation and offline RL, with separate promotion
gates. The [verification guide](../SIGNAL_VERIFICATION.md) defines the implemented
study and missed-move measurements.

## Sources and reproducibility

- **S1:** Nasdaq, [Symbol Directory Definitions](https://www.nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs), accessed 25 November 2026.
- **S2:** Nasdaq Trader, [Equity Corporate Actions Alert 2026-806](https://www.nasdaqtrader.com/TraderNews.aspx?id=eca2026-806), 6 November 2026.
- **S3:** Roku, [Form 8-K](https://www.sec.gov/Archives/edgar/data/1428439/000119312526451874/d181620d8k.htm), 6 November 2026, Item 3.01.
- **S4:** Scikit-learn, [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html).
- **S5:** Bailey et al., [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).

Reproduce the read-only listing analysis with `python marketbot.py listings`
and `--snapshot` pointing to a newly captured directory. A later capture may
have different counts. Run the hash-locked Python 3.11/3.12 offline suite and
the explicit loopback checks described in the root contributor contract.
