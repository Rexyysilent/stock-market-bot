# Portfolio measurement lab

`portfolio_lab` is an optional, isolated way to measure **operator-supplied
hypothetical exposures**. It accepts an explicit study, one coherent price
acquisition, and timestamped decisions; it compares their full economic return
path with a frozen passive benchmark. It does not infer that a source label is
a profitable strategy, choose a personal portfolio, or connect an account.

The public collector, brief schemas, legacy state, and scheduling are unchanged.
This module is research tooling. Its adjusted economic units cannot be sent to a
broker as executable share quantities.

## Start with a reproducible demonstration

Use Python 3.11 or 3.12 and the repository's locked environment. The local
accounting path uses the standard library; an IANA timezone database is required
and is provided by the locked environment where the operating system lacks one.
Only explicit data-acquisition commands import the existing optional provider
packages.

```sh
python -m portfolio_lab demo --out workspaces/portfolio-demo --stress
python -m portfolio_lab validate --study workspaces/portfolio-demo/study.json --dataset workspaces/portfolio-demo/dataset.json --decisions workspaces/portfolio-demo/decisions.json
python -m portfolio_lab evaluate --study workspaces/portfolio-demo/study.json --dataset workspaces/portfolio-demo/dataset.json --decisions workspaces/portfolio-demo/decisions.json --out workspaces/portfolio-demo-replay --stress
```

Every number in this demo is fabricated. Read `report.md` before the JSON
metrics: the assumptions, evaluation status, and evidence gaps are part of the
result. A repeat run needs a new output directory; the command never overwrites
an earlier result.

Each completed run contains:

| File | Purpose |
| --- | --- |
| `study.json` | Exact frozen assumptions, costs, benchmark, and evaluation dates |
| `dataset.json` | Exact acquired or synthetic prices, session schedule, and provenance |
| `decisions.json` | Supplied exposure hypotheses and time/evidence references |
| `report.json` | Daily economic paths, hypothetical transactions, skipped transactions, costs, partition metrics, and evidence gaps |
| `report.md` | Human-readable result and limitations |
| `receipt.json` | Canonical content identities and completion time |

The receipt is written last. A directory without it is incomplete. The saved
inputs are sufficient to replay the calculation with the same code revision;
retain the git commit and locked environment as well. Local hashes detect
inconsistent content, but do not provide independent time attestation.

## Input contracts

Unknown fields, duplicate JSON keys, nonfinite numbers, implicit currency
conversion, missing prices, and ambiguous timezones are rejected. Input files
are limited to 64 MiB. A study has 1–64 assets, at most 30,000 sessions, and at
most 250,000 asset/session price cells. The acquisition adapter checks the
cell limit before requesting any price history.

### Study

The demo's `study.json` is a complete example of
`portfolio-lab.study.v1`. For a separate private experiment, create a new file
in an ignored directory and explicitly review these fields:

| Field | Meaning |
| --- | --- |
| `study_id` | Stable name for this experiment, not a performance label |
| `strategy_sha256` | SHA-256 identity of the operator's exact rule/model/prompt configuration |
| `currency`, `calendar` | One currency, INR or USD; XNYS or XNSE session calendar |
| `assets` | Complete candidate and benchmark universe, with an explicit instrument kind |
| `universe_basis`, `universe_declared_at` | Fixed operator selection or asserted point-in-time membership, with declaration time |
| `initial_cash` | Hypothetical starting capital in study currency |
| `benchmark_weights` | Frozen buy-and-hold allocation; omitted weight remains cash |
| `cost_model` | Explicit generic friction or the dated India delivery scenario below |
| `slippage_bps` | Additional one-way adverse price friction, in basis points |
| `annual_fixed_cost` | Candidate-specific annual data, model, hosting, and other operating expense |
| `benchmark_annual_fixed_cost` | Benchmark's own annual operating expense |
| `execution` | Latency, additional session delay, maximum decision age, and minimum transaction value |
| `limits` | Maximum supplied single-asset and gross weights; neither can exceed 1 |
| `evaluation` | Holdout boundary, declaration time, minimum observations, and written objective |
| `contributions` | Explicit positive deposits on exact sessions; empty means none |

These are measurement inputs, not suggested allocations or thresholds. A
strategy hash is an identity supplied by the operator; the evaluator cannot
prove that a proprietary model actually ran that configuration. Include model
version, prompt, source-selection rules, all parameters, and code revision in
the privately retained definition. Changing one of them starts a new study.

The `point_in_time` label is an assertion, not a membership database. An
experiment on today's surviving stock list has selection bias unless historical
membership, delistings, and unavailable securities are independently resolved.

### Price dataset

`portfolio-lab.dataset.v1` requires a source name/URL, acquisition instant,
rights note, calendar source, adjustment policy, explicit synthetic flag,
independent expected-session list, and an open/close bar for **every** study
asset on **every** expected session.

The only supported basis is `total_return_proxy`. In the provided adapter,
Yahoo/yfinance supplies adjusted daily OHLC and the calendar package supplies
expected exchange sessions independently. No forward fill, provider substitution,
mixed acquisition vintages, or silent truncation is allowed. An unknown/mismatched
currency or exchange timezone aborts acquisition.

After creating a valid private study, acquire a completed interval explicitly:

```sh
python -m portfolio_lab fetch-yahoo --study workspaces/private/study.json --start 2026-10-01 --end 2026-10-22 --out workspaces/private/prices-20261022.json
```

The date range is inclusive and uses exchange-local dates. Choose the last
completed session at the time of the request. This command makes read-only
network requests, uses the existing locked `yfinance` and
`pandas_market_calendars` packages, and needs no brokerage credentials. A
provider outage, gap, suspension, new listing, or metadata mismatch can prevent
the strict complete-panel study; absence is not converted into a zero return.

XNSE support in this module does not convert the existing U.S. evidence
collector into an Indian issuer-data pipeline. Indian instruments need their
own evidence sources and benchmark. Provider calendar coverage is not proof of
exchange certification, and data access does not grant redistribution or model
processing rights. Retain acquired data privately and check applicable terms.

### Historical decisions

A `portfolio-lab.decisions.v1` document wraps a `decisions` list. Each entry
contains `decision_id`, the matching `strategy_sha256`,
`decided_at`, `information_cutoff`, `weights`, `origin`, and
`evidence`. Historical reconstruction uses `origin: "retrospective"`.
All timestamps are ISO instants with explicit timezone offsets.

Weights are long-only, cash-funded target economic exposures supplied by the
operator. Empty weights mean cash. The module does not generate stock picks.
Evidence references contain an exact archive digest, generation time, known
time, and an OK/WARN health status. Their ordering must satisfy:

`generated_at <= known_at <= information_cutoff <= decided_at`

For generic files these times are operator assertions. Labeling a reconstructed
decision as paper in a JSON file does not make it prospective evidence.

## Connect a private bot to the prospective journal

The public exporter timestamps an evidence brief. That is not the completion
time of a downstream private model. A downstream model must finish processing
before a resulting hypothesis can participate in this measurement.

1. Freeze the exact private rule, eligible universe, benchmark, costs, objective,
   and future evaluation boundary in a study file.
2. Create a separate journal before that future period begins.
3. Let the private bot finish consuming eligible archived evidence.
4. Record its supplied hypothetical weights with the exact archive paths.
5. Periodically acquire completed prices and evaluate the journal against the
   same benchmark. Retain all failures, abstentions, and changed versions.

```sh
python -m portfolio_lab freeze --study workspaces/private/study.json --workspace workspaces/private-paper
python -m portfolio_lab record --workspace workspaces/private-paper --decision workspaces/private/decision.json --archive archive/briefs/ACTUAL_ARCHIVE.json
python -m portfolio_lab journal --workspace workspaces/private-paper
python -m portfolio_lab evaluate-journal --workspace workspaces/private-paper --dataset workspaces/private/prices.json --out workspaces/private-evaluation-001 --stress
```

The paths in this example are operator-created private inputs, not bundled live
decisions. A minimal `decision.json` contains:

```json
{
  "decision_id": "private-run-unique-id",
  "strategy_sha256": "EXACT_64_CHARACTER_LOWERCASE_HASH_FROM_YOUR_STUDY",
  "weights": {}
}
```

Replace the hash placeholder with the actual identity. Empty weights provide a
cash-only connectivity check. Supplying any other weights is the operator's
private research choice. Use one unique ID for each completed decision and reuse
that ID only for an identical retry.

The private application can call the same interface directly:

```python
from portfolio_lab.journal import record_decision

# Call only AFTER the private model has finished and its output is validated.
receipt = record_decision(
    "workspaces/private-paper",
    {
        "decision_id": private_run_id,
        "strategy_sha256": frozen_strategy_hash,
        "weights": validated_hypothetical_weights,
    },
    archive_paths=exact_archives_consumed_by_the_private_model,
)
```

This is the bridge contract; it does not contain or implement the private
strategy. The journal reads and hashes the same archive bytes, obtains the
current local UTC time after file reads and lock acquisition, and records that
as the earliest usable decision time. Caller-supplied recording times and
nonempty asserted evidence references are refused. Retry identity includes the
request and archive contents, not incidental file paths.

The journal validates brief schema family, parseability, top-level health, and
time consistency. It does not certify all source fields, provider rights, issuer
mapping, freshness of every component, synthetic status of the brief, or model
truth. WARN evidence still needs source-specific review by the private rule.
Maintain the existing clock checks and source-health gates.

An append-only SQLite record and chained hashes protect ordinary consistency;
an operator with control of the machine can replace files or alter its clock.
This is not an external notarization service. The report shows both the study's
asserted declaration time and the actual local workspace freeze time.

## Accounting and execution assumptions

- The first eligible simulated open must be **strictly later** than the
  decision's known time plus configured latency. Paper recording time is also
  respected. Additional delay is counted in observed exchange sessions.
- Conflicting decisions mapped to one open are rejected instead of being
  silently reordered. Future decisions remain pending. Stale decisions and
  uneconomic transactions are reported explicitly.
- The candidate rebalances only on supplied decisions. The benchmark initially
  acquires its frozen allocation and adds contributions proportionately; it does
  not silently rebalance daily.
- Sales pay configured fees and slippage. Purchases are scaled to available
  cash, including their own costs. The engine does not create hidden borrowing.
- Deposits are tracked separately from investment gains. The time-weighted
  calculation respects the overnight return before an opening deposit.
- Gross comparisons remove all configured friction and overhead. Net
  comparisons include trading fees, adverse-price friction, and elapsed-calendar
  running expense. Candidate and benchmark overhead are configured separately.
- Operating expense is modeled as proportional economic drag on cash and
  adjusted units. It is not a simulation of broker sales to pay a subscription.
- Maximum drawdown observes session opens, post-transaction values, and closes;
  unseen intraday extremes are not reconstructed.
- Annualized returns are unavailable for periods shorter than one year.
- Idle cash earns zero. Interest-bearing cash requires an explicit supported
  return series and separate assumptions; no yield is invented.

The output is a research return model, not an account statement. It omits actual
share lots, separate cash dividends, settlement, limit fills, order queues,
market impact beyond configured friction, taxes, withholding, FX/remittance
costs, and corporate-action cash accounting. Fractional adjusted units are
especially unsuitable as executable Indian share quantities.

With `--stress`, the same inputs are rerun with twice the configured costs
(including slippage and annual overhead) and with one additional session of
decision delay. Neither scenario changes the rule to improve its result.

## Costs

Generic model:

```json
{"kind": "generic", "buy_bps": 5, "sell_bps": 5, "fixed_per_order": 1}
```

This is an illustrative friction assumption, not a broker quote. Five basis
points is 0.05%. The fixed fee is in the study's currency.

For an INR/XNSE cash-delivery experiment, the dated India scenario is:

```json
{"kind": "india_delivery", "broker": "zerodha", "as_of": "2026-10-23", "dp_sell_fee": 15.34}
```

`dp_sell_fee` above is an **illustrative operator entry including GST**.
Replace it with the applicable account/instrument fee. The model does not infer
holder characteristics, account concessions, or an instrument's tax status.
`upstox` is the other implemented brokerage scenario.

This snapshot models brokerage, NSE transaction charge plus IPFT, SEBI levy,
STT, stamp duty, GST, and an explicit sell-side DP fee. Shares,
equity-oriented ETF units, and operator-verified STT-exempt non-equity ETF units
have distinct treatment. Tariff sources and exclusions are documented in
[`costs.py`](../portfolio_lab/costs.py). Applying today's costs to historical
prices is a current-cost scenario, not historical contract-note reconstruction.

No Indian fee scenario is applied to a U.S. security or USD study. The generic
model also does not reproduce IBKR's per-share schedule or fractional-trade
exceptions. A real broker reconciliation needs its actual contract notes.

## Reading evaluation results

Development and holdout are separate date partitions. Their metrics retain
carried positions and prior history; they are not fresh portfolios that restart
with knowledge of each partition's future. The holdout boundary is midnight in
the configured exchange timezone, even when its first date is a holiday.

If a historical period is chosen today, the report calls its declaration
retrospective. A carried position originating in a reconstructed decision also
remains reconstructed. Meeting configured observation counts is only a count
check. It does not turn overlapping observations into independent evidence,
prove a statistical edge, or authorize investment. No evaluation status
automatically approves a strategy.

Before interpreting a comparison, establish the objective: greater return at
comparable risk, lower drawdown with an acceptable return tradeoff, or reduced
human work. Report all rule/model/prompt trials and changes. Compare against an
investable same-currency benchmark and test cost and delay sensitivity.

The [fixed monthly reference study](research/portfolio-reference-20261023.md)
demonstrates the complete acquisition-to-report route. It is a known public
reference, uses current-vintage historical data, and is not a private-bot
backtest or untouched holdout.

## Capital and operating-cost arithmetic

```sh
python -m portfolio_lab income --capital 100000 --currency INR --annual-rates="-20,0,4,8,12" --annual-fixed-cost 6000 --monthly-goal 10000
```

This computes hypothetical annual gain, expense drag, monthly equivalent,
reinvestment/withdrawal arithmetic, and capital implied by the chosen assumptions.
It does not forecast a return, create monthly distributions, model sequence
risk, or estimate a sustainable withdrawal rate. A negative year can consume
principal, and taxes, inflation, and currency changes are outside this command.

## Privacy and operation

Inside this repository, generated study data belongs only in ignored
`workspaces/`, `study-runs/`, `output/`, or `review-results/` directories.
A dedicated external private directory is also supported. Keep private model
definitions, decisions, watchlists, raw prices, and journals out of git. Back up
important private receipts securely; the public git branch preserves the
tooling, not the ignored data.

No command starts a schedule, opens an account, transmits broker credentials,
or places an order. The existing daily collection schedule is independent.
The dated [operating-path research](research/investing-pipeline-20261023.md)
explains why data collection, paper measurement, and any operator-managed
investing process have separate requirements.
