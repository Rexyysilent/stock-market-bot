# Historical monthly-trend reference: SPY, 2005–2026

Research date: **2026-10-23**. This is an actual provider-data run of one fixed
reference rule in `portfolio_lab`, using hypothetical fractional economic units.
It is not a report of the private bot's performance, a broker account, or earned
income. No private bot signals were used.

The reference returned **7.7580% annualized after the configured costs**, versus
**10.7858%** for buying and holding the same SPY return proxy. Its full-period
maximum observed drawdown was smaller: **−27.0009% versus −55.4229%**. In the later
2022–2026 slice, however, it had both a lower return and a slightly larger
drawdown than the benchmark. These results establish a reproducible historical
comparison under the assumptions below; they do not establish alpha, future
profitability, or a passive-income withdrawal rate.

## Question and fixed rule

The experiment asks whether a simple, infrequent trend rule improves on a
buy-and-hold reference after explicit frictions. It uses a familiar rule from
Mebane Faber's *A Quantitative Approach to Tactical Asset Allocation*: compare a
completed month-end price with a ten-month simple moving average. See the
[original journal paper](https://doi.org/10.3905/jwm.2007.674809) and the
[author's 2013 revision](https://mebfaber.com/wp-content/uploads/2016/05/SSRN-id962461.pdf).

The exact implementation is in [reference.py](../../portfolio_lab/reference.py):

1. Observe the last ten consecutive completed monthly adjusted closes, including
   the current completed month. There is no signal before all ten are available.
2. If the latest close is strictly above their arithmetic mean, target 100% SPY.
   Otherwise, including exact equality, target cash.
3. Timestamp the hypothetical decision one second after the month's final
   exchange-session close. Its information cutoff is that completed close.
4. Apply the separate engine's 60-second latency and execute at the first
   supplied session open strictly after eligibility. A sensitivity run adds one
   further session of delay.

Only one rule parameter setting was run: ten months and 100% maximum equity
weight. The two additional runs alter costs and execution delay, not the signal
parameters. The asset and dates were selected retrospectively; a single declared
parameter setting does not remove research-selection bias.

This is not a replication of Faber's reported returns. The
[author's methodology description](https://mebfaber.com/2013/06/07/qtaa-paper-update-manage-your-risk-4/)
uses signal-day closing prices, Treasury-bill returns for cash, and excludes
taxes, commissions and slippage. This experiment instead uses later openings,
zero cash yield and explicit trading frictions, on a different period and a
single ETF. Its outcome cannot be transferred directly to an Indian ETF or to
a different bot strategy.

## Data, calendar and accounting

| Input | Recorded value |
| --- | --- |
| Data source | Yahoo Finance through `yfinance` 1.6.0 |
| Acquisition completed, UTC | `2026-10-23T12:40:17.678297+00:00` |
| Request started, UTC | `2026-10-23T12:40:09.341451+00:00` |
| Requested history | 2004-01-01 through 2026-10-22, inclusive |
| Acquired sessions | 5,738; 2004-01-02 through 2026-10-22 |
| Evaluated sessions | 5,486; 2005-01-03 through 2026-10-22 |
| Calendar | `XNYS`; `pandas_market_calendars` 5.4.0, `NYSE` schedule |
| Exchange timezone | `America/New_York` |
| Initial hypothetical capital | USD 10,000 for both accounts |
| Contributions and withdrawals | None |
| Benchmark | 100% SPY proxy, bought at the first evaluation open and held |
| Cash return | Zero |
| Declared universe and rule | SPY; fixed ten-month rule, declared on 2026-10-23 |

The [provider adapter](../../portfolio_lab/providers.py) requests daily history
with `auto_adjust=True`, `back_adjust=False`, `repair=False`, `actions=False`,
`keepna=True` and `raise_errors=True`. The exclusive provider end date was
2026-10-23. These are provider-adjusted open/close values, representing an
approximation to split- and dividend-adjusted total returns. Dividends are
already reflected by the adjustment factor; no second dividend payment is
added. The adjustment history and any later provider revisions are not
independently verified. The
[yfinance history API](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.history.html)
documents the data interface; the saved acquisition records the actual options
and dependency versions used.

The expected session schedule is generated independently of returned quote
rows. The adapter requires complete matching sessions and positive finite
prices; it does not fill missing observations. The month-end calendar extends
through the actual end of the final calendar month before selecting its last
exchange session. As a result, October 22 is not misclassified as October's
month end. The calendar library's
[documented schedule interface](https://pandas-market-calendars.readthedocs.io/en/latest/usage.html)
supplies exchange opens and closes, including timezone and special-session
handling; a successful match is not independent certification of calendar or
provider accuracy.

Warmup uses the acquired 2004 history. The last eligible warmup decision,
timestamped `2004-12-31T21:00:01+00:00`, carries a 100% equity target into the
first evaluation open on January 3, 2005. Older warmup decisions are superseded
before that start. The final observed monthly decision uses September 30,
2026's close and becomes effective on October 1. There is no invented October
month-end decision.

The [accounting engine](../../portfolio_lab/engine.py) holds fractional
total-return economic units. Adjusted prices and quantities are not broker
quotes, executable share counts, tax lots or a dividend-cash ledger. Trades
must be affordable after costs, with no borrowing or short positions. The
baseline contains 262 retrospective decisions and 33 hypothetical transactions;
unchanged target exposure does not itself create a trade. No trades were skipped
and no decisions were stale or pending in that run. Ending values mark any
remaining position at the last close; there is no compulsory final sale or
terminal liquidation charge.

Returns are time-weighted, neutral to external contributions; no contributions
occur in this particular experiment. CAGR uses actual elapsed calendar time
with a 365.25-day year. Drawdown observes every supplied open, the post-trade
value at that open, and each close. Unobserved intraday lows are outside this
measurement, so the drawdown statistic is not an execution-risk bound.

## Cost assumptions

Both accounts use the same generic tariff:

| Assumption | Baseline |
| --- | --- |
| Trading charge, each purchase or sale | 5 basis points of notional plus USD 1 per transaction |
| Additional slippage charge, each side | 5 basis points of notional |
| Combined proportional charge | 10 basis points per transaction, plus the fixed USD 1 |
| Minimum hypothetical trade | USD 10 |
| Annual infrastructure cost | USD 0 for both accounts |
| Maximum decision age at execution | 35 calendar days |
| Leverage and short selling | None |

These are declared modelling assumptions, not a verified broker tariff or
measured fill-quality estimate. Slippage is a cash-equivalent deduction from
notional, not an observed trade price. Buy quantities shrink to pay expenses
from available funds. Personal taxes, FX conversions, remittance charges,
settlement, integer-share constraints and actual liquidity are not modelled.
An operating bot with nonzero hosting, data or maintenance expense requires a
separate costed study; this run assumes those expenses are zero.

The doubled-cost scenario multiplies commissions, the fixed charge and
slippage by two for both accounts. The extra-session scenario delays only
candidate decisions; the predeclared buy-and-hold reference still starts at the
first evaluation open. The gross comparator removes configured trading,
slippage and infrastructure charges while preserving each account's rules.
It is not a measure of returns before expenses already embedded in the
provider's instrument-price history.

## Full-period results

Amounts are USD. Percentages are nominal and before personal taxes. All rows
use the same 2005-01-03 through 2026-10-22 evaluation path.

| Scenario | Trend ending value | Buy-and-hold ending value | Trend CAGR | Buy-and-hold CAGR | Trend max drawdown | Buy-and-hold max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Baseline, configured costs | 50,977.27 | 93,266.34 | 7.7580% | 10.7858% | −27.0009% | −55.4229% |
| Double all configured costs | 49,248.07 | 93,163.94 | 7.5875% | 10.7802% | −27.4505% | −55.4229% |
| One additional execution session | 52,393.12 | 93,266.34 | 7.8935% | 10.7858% | −27.6694% | −55.4229% |
| Gross: zero configured costs | 52,765.16 | 93,368.95 | 7.9285% | 10.7914% | −26.5490% | −55.4229% |

Baseline cumulative returns were **409.7727%** for the trend rule and
**832.6634%** for buy-and-hold. The candidate incurred USD **921.42** in direct
modelled charges across 33 transactions; the benchmark incurred USD **10.99**
on its initial purchase. Doubled costs raised those amounts to USD **1,805.74**
and **21.96**, respectively. Direct costs are not the whole difference between
gross and net ending wealth, because paying costs also changes subsequent
compounding and trade sizes.

The candidate's annualized return trails the benchmark even with zero
configured costs. Its main favorable result in this sample is lower
full-period drawdown. The delayed variant happens to return more than the
baseline in this history; that observation does not justify selecting the
delay after seeing the result. These are sensitivity observations, not an
optimization or a statistical significance test.

## Chronological split and failed validation requirements

The saved study names January 1, 2022 as its holdout boundary. The corresponding
exchange-local midnight is `2022-01-01T05:00:00+00:00`. However, the study was
actually declared at `2026-10-23T12:40:09.340081+00:00`, after the entire observed
period. Consequently, both partitions are retrospective diagnostic slices.
The names `development` and `holdout` in the JSON do not make the second slice
an untouched, preregistered validation sample.

| Historical slice | Sessions | Trend cumulative return | Buy-and-hold cumulative return | Trend CAGR | Buy-and-hold CAGR | Trend max drawdown | Buy-and-hold max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Earlier: 2005-01-03–2021-12-31 | 4,280 | 319.7439% | 444.2351% | 8.8087% | 10.4847% | −23.3152% | −55.4229% |
| Later: 2022-01-03–2026-10-22 | 1,206 | 21.4485% | 71.3714% | 4.1248% | 11.8563% | −27.0009% | −26.2858% |

Each slice's return and drawdown are rebased for that measurement interval.
Holdings carry through the boundary; capital is not reset to equal starting
dollar amounts. The later slice starts with USD 41,974.39 in the candidate and
USD 54,423.51 in the benchmark. In that interval the candidate's lower return
was accompanied by a slightly larger measured drawdown.

The saved evaluation explicitly records:

- `declared_before_holdout: false`.
- Status: `holdout_declaration_not_prior_to_observed_period`.
- 1,206 later-slice sessions, satisfying the declared minimum of 252 sessions.
- 58 retrospective later-slice decisions and **zero paper decisions**, failing
  the declared minimum of 12 paper decisions.
- `statistical_or_investment_approval: false`.

The sample-count threshold is a procedural floor, not a proof of predictive
power. This run cannot pass the declared paper-evidence requirement. It also
does not reveal the private bot's decision logic, source timing, trial count
or investable results. No income withdrawal, capital-depletion or retirement
spending policy was tested.

## Reproduce the run

Run from the repository root in the environment installed from its locked
requirements. The recorded acquisition and study are produced by
[portfolio_reference_study.py](../../scripts/portfolio_reference_study.py):

```sh
python scripts/portfolio_reference_study.py \
  --symbol SPY \
  --history-start 2004-01-01 \
  --evaluation-start 2005-01-03 \
  --end 2026-10-22 \
  --holdout-start 2022-01-01 \
  --initial-cash 10000 \
  --annual-fixed-cost 0 \
  --cost-bps 5 \
  --slippage-bps 5 \
  --fixed-per-order 1 \
  --out workspaces/reference-spy-20261023
```

The destination must not already exist. For a new acquisition, use a new output
directory. The script records the actual current declaration and acquisition
times; it does not backdate a new study. A later download may contain provider
revisions, and its identity changes with its acquisition timestamp. Fresh
acquisition therefore reproduces the procedure, not necessarily the exact
original input bytes or results.

With the retained original inputs, the baseline can be rerun entirely offline:

```sh
python -m portfolio_lab evaluate \
  --study workspaces/reference-spy-20261023/base/study.json \
  --dataset workspaces/reference-spy-20261023/base/dataset.json \
  --decisions workspaces/reference-spy-20261023/base/decisions.json \
  --cost-multiplier 1 \
  --delay-sessions 0 \
  --out workspaces/reference-spy-frozen-replay
```

For the two sensitivities, keep the same frozen inputs and use
`--cost-multiplier 2 --delay-sessions 0` or
`--cost-multiplier 1 --delay-sessions 1`, each with a fresh output directory.
Each report includes its own gross comparators. Replaying the frozen inputs
preserves their retrospective labels; it does not create new paper evidence.
An independent offline replay of the baseline command produced an identical
report JSON value and the report identity listed below. The numeric result
tables and all listed identities were also checked against the saved outputs.

The generated raw price files remain local in the ignored `workspaces/`
directory. This public note publishes methodology, aggregate results and
identifiers. It does not grant redistribution rights to third-party prices.
Someone without the retained inputs needs an independently authorized
acquisition to repeat the experiment.

## Audit identifiers

The following SHA-256 identities use `portfolio_lab.data.digest`: UTF-8 JSON
with sorted keys, compact separators, `ensure_ascii=False` and nonfinite
numbers disallowed. They are hashes of canonical JSON values, not hashes of
the indented on-disk file bytes. The decision-array identity differs from the
identity of its wrapper document.

| Canonical value | SHA-256 |
| --- | --- |
| Study | `3edf1973057bfba0172c9082ac86b3f5cc463780ddacf50544c5b71c1529916d` |
| Evaluation dataset | `e54233ad398aeabee63d20cce8120f09bd693d365c5b1b702158a5ebebdd4f3a` |
| Decision array (`report.decisions_sha256`) | `e57edba4bf2f8b57adcef66f3a3873053b811720d8d25fe5a7969c23828b3fa0` |
| `decisions.json` wrapper | `becbbe3b9373712f1fd079e09374fc1aab4e3e9b9f120b490d84415972408d66` |
| Full acquisition including warmup | `5c27e1586652a010466f714d05f39981c30b5942722cf4aaa5b43ca32c68beec` |
| Exact reference rule | `3f6ef5f6d163df357f9e93f53b71490411c1e7245f618c9858fc22df4c33a8b5` |
| Independent month-end session array | `430c5cc694b6f44bbae73c49245f18f1d25db6bd9a614c8309f7e1b75d3dc6af` |
| Baseline report JSON | `aed62da23da0d4adf123018a44abbd5fab6e50fc45cdd45b19ec5e5e9fbd5286` |

The three scenarios share the study, dataset and decision identities. The
baseline save receipt was created at `2026-10-23T12:40:18.918748+00:00`.
Hashes permit exact comparisons against retained artifacts; they do not
independently certify market data accuracy, historical availability of those
data, authorship, or performance.
