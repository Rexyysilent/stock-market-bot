# Predictor and planning research programme

Research date: **25 November 2026**. Scope: the daily US-market pipeline, its
+1/+5/+20 NYSE-session outcome measurements, and the separate `portfolio_lab`.
This programme distinguishes observed implementation results from proposed
market experiments. No predictive performance is established here.

## Findings and decision

The most useful first predictive experiment is a modest, calibrated model of
incremental event information, compared with price-only and existing-rule
baselines. MCTS warrants a separate experiment after the forecast layer can
survive a point-in-time comparison with explicit costs. Its initial role is
choosing among a few hypothetical exposure transitions under a supplied return
distribution. Searching a bounded grammar for useful formulaic features is a
different MCTS application and requires a separate experiment.

A planner can improve decisions without improving forecasts. Feature search
can improve forecasts without planning transactions. More search reduces
search error; it does not remove stale inputs, historical leakage or an
incorrect simulator.

The direct trading paper by Vittori, Likmeta and Restelli is instructive: its
cost-free EUR/USD experiment appeared profitable, but introducing transaction
costs drove the agent toward inactivity. The authors discuss horizon and
generative-model limitations. This is a finding about that experiment, not a
theorem against MCTS. [S1]

## Implemented offline prototype

[`portfolio_lab/planning.py`](../../portfolio_lab/planning.py) implements seeded,
bounded open-loop MCTS; exhaustive finite-model search; matched-budget random
shooting; greedy one-step and no-transaction comparators; post-cost weight
accounting; optional charged terminal liquidation; and separate retrospective
evaluation of an already chosen schedule.

This is one hypothetical risky asset plus cash. The **same action schedule
applies to every supplied scenario**. It cannot choose a different schedule
after discovering which path occurred. `hold` permits weight drift and creates
no transaction. It is different from daily constant-weight rebalancing.

The objective is expected terminal log wealth under supplied probabilities.
Each complete schedule is evaluated over every forecast scenario. Randomness
is in the schedule search; simulated paths are not independent market evidence.
Root-node averages are search diagnostics, not calibrated confidence intervals.

### Reproduction

```sh
python -m unittest -v test_portfolio_planning
python - <<'PY'
import json
from portfolio_lab.planning import compare_planners, synthetic_problem
print(json.dumps(compare_planners(synthetic_problem(), simulations=512, seed=17),
                 indent=2, allow_nan=False))
PY
```

The fabricated example has three steps, three scenarios and four actions: 64
possible schedules. At 512 rollouts, **both MCTS and random shooting recovered
the exhaustive optimum**. The greedy one-step comparator did not. This verifies
the small comparison; it provides no evidence that MCTS beats random shooting
generally or predicts market returns.

The 18 focused tests passed on Python 3.12.14. They include analytic round-trip
costs, weight drift, one-step/exhaustive agreement, a pathwise hindsight trap,
late forecast rejection and separation of realized outcomes. An additional
deterministic check covered 30 randomly generated synthetic three-step problems:
no heuristic exceeded its exhaustive finite-model optimum, and MCTS recovered
that optimum in these small cases at 256 rollouts. Neither check used real
market data.

| API | Purpose |
|---|---|
| `synthetic_problem()` | Complete fabricated input contract. |
| `validate_problem(problem)` | Normalize a copy; reject unknown or missing fields. |
| `mcts_plan(problem, simulations=512, seed=0, exploration=0.1)` | UCT search; return best fully evaluated common schedule. |
| `random_shooting_plan(problem, simulations=512, seed=0)` | Uniform schedule search under the same budget. |
| `exhaustive_plan(problem, max_sequences=100000)` | Bounded complete enumeration. |
| `myopic_plan(problem)` / `hold_plan(problem)` | One-step and no-transaction controls. |
| `evaluate_sequence(problem, sequence)` | Conditional scenario metrics. |
| `replay_sequence(problem, sequence, realized_returns)` | Retrospective scoring only; never optimize. |
| `compare_planners(problem, ..., include_exhaustive=True)` | Pure-data comparison for a caller or CLI. |

Input kind is `synthetic` or `archived_forecast`. The latter is an assertion, not
proof that the forecast existed historically. The declared timestamps must obey
`data_through <= information_cutoff <= created_at <= decision_at`. Hashes identify
inputs; they do not notarize time or validate scenario probabilities. A
`realized_returns` field in planning input is rejected.

The prototype has no fixed ticket fees, liquidity, market impact, taxes, FX or
broker fills. Total-loss states require additional bankruptcy semantics and
are outside its log-wealth domain. This is not a general risk bound. Existing
`portfolio_lab` remains the accounting engine for empirical hypothetical
portfolio studies; the small planner isolates the search question.

## Existing evidence and the new evaluation boundary

At recovered baseline `dc6e4cde3cfe0a521c9eeff9ec9b355086964367`, the README,
ledger configuration, paper journal and October reference-study note establish
immutable briefs, source/observation times, missingness, version-separated
outcomes, costs, delayed hypothetical execution and benchmarks. The 29
instrumented and 13 editorial names are sample coverage, not holdings.

The old ledger holdout was declared on 22 September 2026 with a 1 October
boundary. New models designed after inspecting those outcomes cannot inherit
that interval as untouched evaluation. Freeze a new version and boundary.

The monthly-SPY historical reference did not evaluate private bot decisions.
It records zero prospective paper decisions and a retrospectively declared
later slice. Its performance is not the bot's accuracy or profits. The local
journal also correctly distinguishes hash consistency from independent clock
attestation.

## What the primary literature supports

Each published result belongs to its own experiment. None has been reproduced
on this pipeline.

| Work | Relevant evidence | Practical consequence |
|---|---|---|
| Vittori et al., 2021 [S1] | Stochastic MCTS for trading/hedging; costed FX experiment became inactive. | Validate costs, horizon and scenarios before increasing search. |
| Aydinhan et al., 2022 [S2] | MCTS plus neural approximations for specified multiperiod financial models. | Planning evidence, not an equity-return predictor. |
| RiskMiner, 2024 [S3] | MCTS searches formulaic alpha expressions on Chinese equities. | Investigate feature discovery separately from allocation planning. |
| AlphaCFG, 2026 preprint [S4] | Grammar and semantic constraints organize bounded formula search; reports US and Chinese-market experiments. | Typed operators and deduplication are useful design ideas. |
| Gu, Kelly and Xiu [S5] | Comparative US-equity forecasting study finds useful nonlinear interactions. | Regularized regressions and tree ensembles deserve serious baseline status. |
| Kronos, 2025 [S6] | Financial-candlestick foundation model with forecasting, volatility and generation experiments. | A frozen shadow challenger; generated realism is not simulator validation. |
| MOPO, 2020 [S7] | Learned-dynamics uncertainty penalties for offline-control tasks. | Motivation for conservatism, not an imported financial guarantee. |
| Gibbs and Candès, 2024 [S8] | Online conformal inference under distribution shift, including volatility experiments. | Monitor interval coverage and width together. |
| Gao et al., revised 2026 [S9] | LLM lookahead contamination associated with remembered firm-date outcomes. | Historical re-prompts stay retrospective; preserve prospective outputs. |
| Backtest-overfitting/deflated-Sharpe work [S10, S11] | Selection-induced inflation and adjusted inference. | Record unsuccessful trials and the whole model-selection history. |

MuZero's original experiments establish learned-model planning in games. They
do not validate financial forecasts or profitability. [S12]

## Target 1: test incremental event information

Keep collection labels descriptive. A separate experiment should identify a
return-distribution target, a binary outcome with fixed threshold, a volatility
target, or a fact-extraction target. These are different notions of accuracy.
Each needs its horizon, benchmark and earliest permitted entry convention.
An output score of 80 is not an 80% probability unless calibrated to a named
outcome. Even a calibrated positive-return probability does not determine
economic value without conditional gain/loss sizes and costs.

| Model arm | Inputs | Question |
|---|---|---|
| Base rate/reference | Historical prevalence or declared reference policy | What happens without incremental information? |
| Price-only | Lagged returns, volatility, volume, sector and market | Is event value ordinary price information? |
| Existing classifications | Frozen deterministic labels | What does the present pipeline add? |
| Regularized event model | Price controls plus event type, novelty, magnitude and freshness | Is incremental information stable and interpretable? |
| Restricted tree ensemble | Identical point-in-time fields | Do nonlinear interactions improve performance? |
| Frozen text/embedding model | Same documents, cutoff and downstream estimator | Does representation add useful information? |

Fit preprocessing and calibration only on earlier information. Show reliability
bins with counts alongside proper scores. Scikit-learn documents the need for
independent calibration information and explains why lower Brier loss alone
does not establish better calibration. [S13]

Prioritize better interpretation of existing evidence: original disclosure
versus repetition or amendment; public availability versus transaction date;
known denominators with their own reporting dates; surprises against genuinely
available expectations; and explicit source failures versus measured absence.
Multiple articles repeating one filing are not independent confirmations.
Lagged liquidity, volatility and market/sector context should first be controls.

Use language models as auditable extractors before asking whether their
interpretation predicts returns. An old article supplied to a newer model does
not remove later events encoded in training. A prompt saying to ignore future
knowledge is not evidence that this contamination is absent. [S9, S14]

## Target 2: isolate planning value

The empirical hypothesis is: given identical forecasts, information cutoffs,
costs and constraints, planning improves the declared objective relative to
simpler action selection. Changing the forecaster simultaneously would confound
that comparison.

Start with a small fixed universe or the same frozen selection rule for every
arm. State contains hypothetical cash/exposures, mature features, input/model
identities, session, publicly scheduled events and required objective state.
Keep actions few. A 5-session primary horizon and a separately declared
20-session sensitivity align with current outcome windows; these are proposed
choices, not optimal settings established here.

The prototype commits one shared schedule. A later model-predictive-control
extension would apply its first hypothetical action, receive the next actual
observation, then replan using only the observed prefix. A decision node must
not inspect an unrevealed realized return. Optimizing each entire future path
separately and averaging the resulting profits violates nonanticipativity.

Use this scenario-model ladder: a no-predictability control; modest conditional
forecasts plus block-resampled earlier residuals; regime conditioning only with
sufficient evidence and shrinkage; an uncertainty-checked ensemble; and an
optional generative model with known training coverage. For several assets,
resample dated residual vectors together to retain joint moves. Thousands of
resampled paths reduce conditional simulation noise, not historical uncertainty.

Compare the same forecaster with a fixed reference, no-change/threshold policy,
myopic policy, equal-budget random shooting and exhaustive/scenario optimization
where tractable. Report model-call budgets and runtime. If a simpler method
matches MCTS, that is a useful result.

Choose one interpretable objective and fixed constraints. Report net returns,
drawdown, turnover, costs and concentration separately. Do not tune an
arbitrarily weighted objective after seeing outcomes. Risk-seeking formula
exploration is not a reason to prefer risk-seeking capital allocation.

### Why search cannot resolve model error

If every admissible policy has value error at most E between the actual and
simulated processes, and search is within delta of the simulated optimum,
adding and subtracting the two simulated values yields:

**Actual optimality gap <= 2E + delta.**

This elementary decomposition is not an estimated bound for the bot. Search
can reduce delta without establishing E. Changing regimes, long horizons and
missing state can make the model term dominate. Measure search-budget curves
on untouched outcomes as well as simulated value.

## Falsification and multiweek verification

| Check | Failure exposed |
|---|---|
| Same visible past, changed unrevealed future | Leakage through a cache, feature or loader. |
| Costs but no forecast advantage | Turnover gains fabricated by omitted costs. |
| Shared schedule across contradictory scenarios | Pathwise hindsight masquerading as planning. |
| Extreme adverse joint move | Underestimated cross-asset tail dependence. |
| Event after close plus processing latency | Unavailable same-close entry. |
| Cash shortage, unchanged target, absent price | Borrowing, phantom transactions or invented fills. |
| Open terminal exposure | Hidden liquidation convention. |
| Increasing simulation budget | Model exploitation when simulated gains rise but realized results deteriorate. |
| Temporally blocked shuffled labels | Accidental predictability or excessive selection. |

Daily OHLC does not reveal whether an intraday stop or target was touched first
when both occurred. Preserve ambiguity or a frozen conservative convention.
A daily print does not prove arbitrary order size could fill there. Price-taking
replay tests an approximation; large-action market impact needs a different
dataset and simulator programme.

Freeze commit, archive identities, universe/membership times, feature schema,
training/calibration boundaries, model checkpoint, target, costs, delay,
objective, baselines, seeds and every attempted variant. Changed prompts,
thresholds or missing-data policies require a new version or an already declared
adaptation. Retain every candidate and rejection, not only selected outputs.

Keep collector, predictor and verifier separate. Later outcomes cannot rewrite
old forecasts or create backdated paper decisions. Report five metric groups:
source/universe coverage; audited entity/time/event extraction; probabilistic
and ranking prediction quality; hypothetical economic outcomes; and operational
delays, failures and unresolved outcomes. A reliability improvement can be
useful even when directional prediction is unchanged.

Stocks on one date and overlapping 20-session labels are dependent. Use session
or longer block uncertainty estimates with block-length sensitivity. Thousands
of ticker rows need not represent thousands of independent trials. The first
week tests operation. The earliest 20-session labels need about a month of
exchange sessions to mature. Four to eight weeks can reveal operational defects
or gross miscalibration, but need not establish a small economic edge.

Keep review cadence fixed and new development separate from the prospective
record. Backtest-overfitting and deflated-Sharpe diagnostics, when appropriate,
complement this separation; they do not replace it. [S10, S11]

### Missed-opportunity attribution

Fix the eligible universe and common entry/exit/cost convention before the
period. A later favorable outcome is a retrospective label, not proof that it
was predictable. Distinguish outside-universe, absent/late source, mapping
error, evidence present but weak forecast, explicit economic/portfolio
constraint, processing delay, and unavailable/ambiguous outcome.

Only the weak-forecast case is immediately a forecasting miss. A constraint can
be correct restraint. Report top-outcome capture at fixed review capacity
alongside precision. A portfolio of future winners is a descriptive hindsight
bound, not an investable benchmark. Do not sum overlapping “missed profits”
without capital, correlation and capacity constraints.

## Implemented prospective monitor

[`ledger/monitor.py`](../../ledger/monitor.py) wraps the cohort-wide verifier in
a frozen local workspace. `freeze_monitor(workspace, policy, archive_dir,
min_observation_days=28)` records an effective declaration no earlier than its
actual local freeze time. Identical initialization is idempotent; changed
measurement assumptions require another workspace.

`run_monitor(workspace, fetch=False, as_of=None, archive_dir=None)` inventories
archives and appends immutable JSON/Markdown snapshots plus hash-linked
receipts. Price fetching is explicit. An archive-directory override lets the
workspace move to another machine without rewriting its policy; each receipt
records the location used. Explicit cutoffs cannot precede the freeze or move
backward across committed reviews. A local clock earlier than the previous
receipt is rejected before database access or new report writes.

Missing directories, malformed files, failed exports, future timestamps and
declared synthetic/demo fixtures are exclusions, not quiet observations.
Synthetic flags reuse ingestion's quality-evidence convention, including
`fixture_metadata` and `meta`. Accepted bytes are retained privately under their
content identities. Changed source archives are excluded while their original
captured bytes remain. Ingestion touches only the dedicated workspace ledger.

`monitor_status(workspace)` verifies policy, receipt chain, both committed
report formats and retained inputs. Crash-orphan reports are preserved and
reported. Valid receipts can recover a lagging head pointer; exact retained
bytes can recover a crash either before database ingestion or before its
receipt commit, even if the original source has disappeared. Recovery checks
the content-addressed filename and the archive's quality and timing again;
ineligible candidates are disclosed as exclusions. Modified or missing
committed artifacts fail the consistency check.

Statuses distinguish `awaiting_archives`, `historical_only`, `collecting`,
`awaiting_outcomes`, `ready_for_review`, `awaiting_declaration`, and `failed`.
Readiness requires both elapsed and observed archive windows, plus observed
prospective comparisons. It does not establish statistical power or trading
skill. A historical archive cannot become prospective just because weeks pass.

The 21 monitor lifecycle tests cover empty sources, mixed archive quality,
fixture conventions, immutable policy, report/receipt/input tampering, source
mutation, backward/future dates, clock rollback between reviews, relocation,
fetch opt-in, failed evaluations and crash recovery before and after ingestion.
These are synthetic engineering checks. Local files and
clocks do not supply independent custody attestation; these APIs start no
scheduler.

## Further research targets and promotion gates

**Volatility/abstention:** compare simple historical estimates, small conditional
models and interval calibration. Track conditional failures near events and
interval width; arbitrarily wide intervals are not useful success. [S8]

**Symbolic features:** constrain lagged operators, units and expression length;
canonicalize duplicates; ban future offsets and future constituent information.
Compare MCTS with random formula search at equal evaluation budgets, keeping
an untouched chronological slice outside the search. [S3, S4]

**Foundation-model shadow forecasts:** pin checkpoints, tokenizer, preprocessing
and sampling. Record training overlap. Published relative RankIC improvement
does not establish economic value for this universe and its costs. Kronos is a
candidate to test, not a selected replacement. [S6]

**Offline RL:** first establish a real sequential advantage. Deterministic
legacy-policy logs do not identify all alternative-action outcomes without
assumptions. CQL and MOPO address general offline-learning difficulties; neither
certifies market profitability. [S7, S15]

Promotion requires correct measurement, incremental forecast information,
planning value with forecasting held fixed, prospective evidence outside the
development loop, and worthwhile operational cost. No fixed number of weeks or
trials establishes statistical power on its own.

## Primary sources

Sources checked on 25 November 2026. Published empirical claims are not
reproduced results for this project.

- **S1:** Vittori, Likmeta and Restelli (2021), *Monte Carlo Tree Search for
  Trading and Hedging*, ICAIF. [Author PDF](https://edoardo-v.github.io/downloads/2021_Vittori_MCTS.pdf),
  [DOI](https://doi.org/10.1145/3490354.3494402). Costed results pp. 4–5; conclusion p. 7.
- **S2:** Aydinhan, Li and Mulvey (2022), *Solving Multi-Period Financial Planning
  Models: Combining Monte Carlo Tree Search and Neural Networks*.
  [Paper record](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4143433).
  Abstract and experiment scope inspected; no replication.
- **S3:** Ren et al. (2024), *RiskMiner: Discovering Formulaic Alphas via Risk
  Seeking Monte Carlo Tree Search*. [Paper](https://arxiv.org/html/2402.07080v2),
  [author implementation](https://github.com/ShawnWangXin/RiskMiner).
- **S4:** Yang et al. (2026), *Alpha Discovery via Grammar-Guided Learning and
  Search*. [Preprint](https://arxiv.org/html/2601.22119v1).
- **S5:** Gu, Kelly and Xiu, *Empirical Asset Pricing via Machine Learning*,
  NBER Working Paper 25398 (2018, revised 2019). [Record](https://www.nber.org/papers/w25398).
- **S6:** Shi et al. (2025), *Kronos: A Foundation Model for the Language of
  Financial Markets*. [Paper](https://arxiv.org/html/2508.02739v1),
  [author implementation](https://github.com/shiyu-coder/Kronos).
- **S7:** Yu et al. (2020), *MOPO: Model-based Offline Policy Optimization*,
  NeurIPS. [Paper](https://arxiv.org/abs/2005.13239).
- **S8:** Gibbs and Candès (2024), *Conformal Inference for Online Prediction
  with Arbitrary Distribution Shifts*, JMLR 25(162):1–36.
  [Paper and code](https://www.jmlr.org/papers/v25/22-1218.html).
- **S9:** Gao, Jiang and Yan, *Detecting Lookahead Bias in LLM Forecasts*,
  version 2, 12 June 2026. [Paper](https://arxiv.org/abs/2512.23847).
- **S10:** Bailey, Borwein, López de Prado and Zhu, *The Probability of Backtest
  Overfitting*. [Author manuscript](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf).
- **S11:** Bailey and López de Prado (2014), *The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality*.
  [Author manuscript](https://davidhbailey.com/dhbpapers/deflated-sharpe.pdf).
- **S12:** Schrittwieser et al., *Mastering Atari, Go, Chess and Shogi by Planning
  with a Learned Model*, Nature 2020. [Paper](https://arxiv.org/abs/1911.08265).
- **S13:** Scikit-learn official documentation, [Probability calibration](https://scikit-learn.org/stable/modules/calibration.html).
- **S14:** Li, Wang and Ma (2026), *Summoning the Oracle to Slay It: Mitigating
  Look-Ahead Bias in Financial Backtesting with Large Language Models*, version
  2, 29 August 2026. [Paper](https://arxiv.org/abs/2605.24564).
- **S15:** Kumar, Zhou, Tucker and Levine (2020), *Conservative Q-Learning for
  Offline Reinforcement Learning*. [Paper](https://arxiv.org/abs/2006.04779).
