# From evidence collection to a measurable investing process

Research date: **23 October 2026**. This document describes operational routes
and an evaluation protocol. It is not a personal allocation, a selection of
securities, a broker endorsement, or a statement that a private strategy has
been inspected or validated.

## What the repository establishes

The public project has useful foundations: immutable brief archives, explicit
source and observation times, health/missingness reporting, reproducible
measurement eras, and evidence-history views that distinguish as-operated
knowledge from retrospective interpretation.

Those capabilities do not establish an investing edge. The public configuration
is U.S.-oriented: SEC filings, NYSE session policy, USD measurements, and a
selected company universe. The legacy outcome ledger starts from the brief's
`generated_at`; a later private model's decision cannot inherit that earlier
knowledge time. Endpoint event returns also do not constitute a funded,
cost-adjusted portfolio path.

This implementation adds the missing measurement layer:

1. A frozen private rule/universe/benchmark/cost specification.
2. Actual locally recorded decision time after private processing finishes.
3. Exact archive identity and separate source-health limitations.
4. A complete, coherent, same-market adjusted price path.
5. Cash-funded economic accounting with later-session fills and explicit costs.
6. Gross/net benchmark comparisons, date partitions, and delay/cost sensitivity.
7. A prospective local journal whose limits are disclosed.

The [guide](../PORTFOLIO_LAB.md) contains working commands and the private bridge
interface. No public implementation or historical replay proves a particular
private model's profitability.

## Two distinct operating routes

| Route | What it can support | Main limitations |
| --- | --- | --- |
| An operator's existing Indian mutual-fund/SIP facility | Recurring contributions with little software operation | Personal suitability, fund expenses/tracking, liquidity needs, and equity risk remain separate decisions |
| Indian cash-equity/ETF research | INR/XNSE performance measurement with Indian delivery-cost scenarios | Requires Indian evidence and instruments; U.S. filing observations are not an Indian selection rule |
| U.S. cash securities through an eligible overseas account | A market match for the current SEC/U.S. evidence universe | FX, remittances, overseas reporting, broker costs, and actual account eligibility |
| Unattended API order execution | A different engineering and broker-compliance project | Static IP, authentication, permissions, reconciliation, failure handling, and order controls; not implemented here |

A low-frequency process can have two parallel components: an operator-managed
long-term contribution routine and a private research challenger measured
against a passive baseline. This keeps the evaluation question explicit: what
does the challenger add after its additional costs and work?

For example, Coin supports linking a bank/UPI mandate to recurring SIPs. That
automation belongs to the platform; it does not require running a market-data
API bot continuously. Mandate failures still need attention.
[Zerodha's mandate documentation](https://support.zerodha.com/category/mutual-funds/payments-and-orders/coin-mandates/articles/sip-mandate-on-coin)
describes the mechanism and restrictions.

This is an operational comparison. Selecting a fund, amount, equity exposure,
or withdrawal policy requires the operator's capital, horizon, emergency
reserve, debt, and loss tolerance. No such inputs are inferred by this tooling.

## Data access and ongoing expense in India

The current **Upstox Analytics Token** is free, read-only, and valid for one
year. Its market-data endpoints do not require a static IP; specified
account/history endpoints do. It cannot place or modify orders. This is a
potential data route for an operator who already has an appropriate account,
not an integrated adapter in this commit.
[Official documentation](https://upstox.com/developer/api-documentation/analytics-token/).

**Kite Personal** includes account/order APIs but no historical or real-time
market data. **Kite Connect** adds those data services for ₹500 per app per
month. From October 2026 the published terms also describe brokerage-based
renewal credits; a low-turnover delivery study should not assume it qualifies.
[Official pricing](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis).

The repository's explicit Yahoo acquisition is useful for an initial historical
measurement and was exercised with real data. It is not a licensed exchange
feed, an execution-price source, or a guarantee of availability.
[yfinance history contract](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.history.html).
Raw acquisitions remain local. A paid or broker feed should be selected only
when its coverage, rights, and operational benefit justify its cost.

An extra account can also change maintenance charges and Basic Services Demat
Account eligibility; compare total account economics before adding an account
solely to obtain a free API.
[CDSL's BSDA FAQ](https://www.cdslindia.com/downloads/Investors/FAQs/16%20Demat%20CDSL%20Way%20-%20XVI%20-%20Basic%20Services%20Demat%20Account%20-29012025.pdf).

### Delivery fees are not one flat percentage

The implemented dated cost scenario separates brokerage, transaction charge
plus IPFT, SEBI levy, STT, stamp duty, GST, and sell-side DP fees. Instrument
classification matters: delivery shares and equity-oriented fund units do not
have the same STT treatment. Zerodha and Upstox delivery brokerage also differ.
The precise implemented constants, primary links, and omissions are in
[`portfolio_lab/costs.py`](../../portfolio_lab/costs.py).

The model is a **23 October 2026 current-cost scenario**, not a reconstruction
of every historical tariff. DP fees must be supplied for the actual account.
There is no implicit capital-gains tax, FX charge, or real-lot accounting.

### API orders are a separate operational burden

Zerodha states that all API-based order placement requires a whitelisted static
IP from **1 April 2026**. Market-data access is distinct. The exchange's
retail-algo standards also distinguish registration thresholds from broader
API requirements: low order frequency does not remove every control.
[Broker implementation](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/static-ip),
[NSE implementation standards](https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf),
[NSE retail-algo FAQ](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/FAQ_Retail%20Algo_03112025_NSE.pdf).

A human approval button in a custom application still leads to an API order if
the application submits it through an API. It should not be treated as a way
around the applicable API controls. Order-entry through the broker's own
interface is an operationally different path.

Authentication is another maintenance requirement: standard Kite access tokens
normally expire at 6 a.m. the next day, and Upstox standard access tokens at
3:30 a.m. the next day. The separate Upstox read-only token is not a trading
credential.
[Kite authentication](https://kite.trade/docs/connect/v3/user/),
[Upstox token documentation](https://upstox.com/developer/api-documentation/get-token/).

These requirements make a small, unattended order bot materially more work
than scheduled contributions and periodic research review. No broker order
adapter, account login, credential capture, or execution schedule is included.

## A U.S. route matching the existing evidence universe

Interactive Brokers publishes an overseas-only cash-account route for eligible
Indian residents, including stocks, bonds, and ETFs. Its page's USD 500 figure
refers to maintaining market-data/research subscriptions; it should not be
misrepresented as a universal account-opening minimum.
[IBKR account information](https://www.interactivebrokers.co.in/en/accounts/individual-account-india.php).

RBI's LRS master direction allows resident individuals up to USD 250,000 per
financial year for permitted transactions, subject to the scheme and overseas
investment rules. Remittances for overseas margin or margin calls are not
permitted under LRS. This supports evaluating a cash-funded route, not assuming
an overseas leveraged trading account is allowed.
[RBI master direction](https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=10192).

The actual economic comparison must include bank FX spreads, remittance fees,
broker charges, withholding, personal tax treatment, and the relevant overseas
asset/income reporting. For investment-purpose LRS remittances, the Income Tax
Department's rate table lists 20% TCS on the amount above the applicable
₹10 lakh threshold. TCS is a tax-credit/cash-flow matter, not simply an extra
permanent 20% commission.
[TCS rates](https://www.incometaxindia.gov.in/w/tcs-rates),
[foreign asset reporting guidance](https://www.incometax.gov.in/iec/foportal/nudge/nudge-schedule-fa).
Applicability depends on residency and the current tax rules; confirm with the
remitting bank and an adviser for the actual transaction.

IBKR's U.S. commission schedule contains tiered/fixed per-share pricing and
special fractional-trade treatment. A generic percentage-plus-ticket scenario
in this lab is not an exact IBKR tariff implementation.
[Official commissions](https://www.interactivebrokers.com/en/pricing/commissions-stocks.php).

The friction and administration can dominate small, frequent cross-border
investments. That is an economic reason to quantify this route before choosing
it, even when access is available. A U.S. strategy's USD result also cannot be
presented as its investor's INR return without an explicit FX path.

## Which hypotheses deserve measurement?

### Passive exposure is a demanding baseline

S&P's SPIVA India Year-End 2025 report says 76.3% of Indian active large-cap
funds underperformed their benchmark over ten years. This is evidence about
that fund population, not proof about an individual bot; it nevertheless
supports requiring a strong, investable passive comparison.
[Primary scorecard](https://www.spglobal.com/spdji/en/spiva/article/spiva-india-year-end-2025/).

The new fixed monthly reference study is an integration benchmark. Over the
tested historical path it reduced the largest drawdown but trailed buy-and-hold
in annualized return; in the later historical slice it did not improve either
metric. See the [full protocol and results](portfolio-reference-20261023.md).
Those results do not identify a winning private strategy.

### Insider activity needs better definitions before performance claims

Purchase clustering is a plausible research question, not a validated trading
instruction. Form 4 code P includes private transactions and can appear for
derivative securities. Distinct reporting owners can also represent shared
economic ownership. Routine/opportunistic trading and clustered purchases are
different hypotheses in the original literature.

The [insider study protocol](insider-purchase-hypothesis.md) identifies the
original papers, the public-disclosure timing problem, transaction exclusions,
and a falsifiable event-study design. It does not introduce a new runtime
recommendation or equate an academic long–short result with a retail long-only
cash return.

### A complex model still needs prospective evidence

Freeze a small number of candidate rules before evaluation. Retain rejected
variants, prompts, model versions, and parameter trials. Historical LLM output
can contain training knowledge of later events even when its visible prompt
uses old documents; a reconstruction is not an untouched historical decision.

The next useful observation is an actual private decision recorded before its
future outcome, with a documented opportunity-cost benchmark and all operating
expenses. Several months of paper operation can establish whether collection
and accounting work; a monthly rule generates only a few decisions in that
time. That is not statistical proof of a return advantage.

## Passive income is also a capital question

For a hypothetical annual return (r), capital (C), and annual operating
expense (F), the simple monthly equivalent is:

`(C × r − F) / 12`

This is arithmetic, not a forecast or a sustainable withdrawal calculation.
Investment gains arrive unevenly, can be negative, and differ from dividend
or interest payments. Spending a hypothetical average gain can erode capital.

Illustrative gross equivalents before costs, tax, and inflation:

| Hypothetical return | Monthly equivalent on ₹1 lakh | Capital arithmetically corresponding to ₹25,000/month |
| --- | ---: | ---: |
| 4% a year | ₹333 | ₹75 lakh |
| 8% a year | ₹667 | ₹37.5 lakh |
| 12% a year | ₹1,000 | ₹25 lakh |

None of these rates is a promised return or a safe withdrawal rate. A fixed
₹500 monthly subscription costs ₹6,000 a year: 6% of ₹1 lakh or 12% of ₹50,000,
before trading friction and investment losses. Include the actual total of
data, model calls, hosting, account maintenance, and review time when deciding
whether a more elaborate process earns its operating cost.

```sh
python -m portfolio_lab income --capital 100000 --currency INR --annual-rates="-20,0,4,8,12" --annual-fixed-cost 6000 --monthly-goal 25000
```

## Evidence required for the next stage

A private strategy comparison needs the exact rule/model version and its
timestamped decisions, the eligible instruments and currency, a frozen
benchmark/objective, credible complete prices, and actual cost assumptions.
Personal implementation additionally needs investable capital, monthly
contributions, existing accounts, horizon, and acceptable loss.

The implementation provides acquisition, accounting, a private bridge contract,
prospective records, and reproducible reports. It deliberately does not infer
the missing private strategy or account inputs. A positive backtest would
still be a research result; real broker fills, reconciliation, taxes, and an
appropriate personal investing decision remain separate evidence.
