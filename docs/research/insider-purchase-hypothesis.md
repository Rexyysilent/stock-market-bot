# Insider purchase clusters: evidence and a proposed private study

Research note, 2026-10-23. This note specifies a hypothesis and its data
requirements. It does not report a completed strategy evaluation, identify
securities to purchase, or establish an income stream.

Purchase clustering is worth measuring because it is already an observable
feature in this repository. Its `HIGH` and `MEDIUM` labels encode configured
counts and direction; they are not probabilities, expected returns, or evidence
that a private strategy is superior. The original research below motivates a
test, but does not validate this repository's parser or its two-buyer/30-day rule.

## What the original research supports

### Routine versus opportunistic trading

Cohen, Malloy and Pomorski, *Decoding Inside Information*, Journal of Finance
(2012), study 1986-2007 transactions and exclude private transactions and option
exercises. Their classification uses prior trading patterns: eligible insiders
have at least one trade in each preceding three years; a recurring calendar
month identifies routine activity. Classification precedes the evaluated trades.

The reported 82 basis points per month in value-weighted abnormal returns belongs
to a **long-short portfolio of opportunistic purchases minus opportunistic
sales**. It is not a long-only purchase-cluster return. The manuscript attributes
over half of the improvement from classification to sales. Insiders without the
required history form an unclassified group. The authors examine disclosure-delay
robustness, but that does not establish present-day results after this collector's
actual delay and execution costs. "Opportunistic" is an empirical timing label,
not a determination that a particular transaction was unlawful.

Primary sources: [Harvard publication][cmp], [author manuscript][cmp-pdf],
[NBER working-paper version][cmp-nber].

### Clustering and the public-disclosure clock

Kang, Kim and Wang, *Cluster Trading of Corporate Insiders* (November 2018
working paper), analyze 1986-2016 data. Their clusters involve multiple insiders
trading in the same direction on the same or consecutive trading days. They
report 21-session characteristic-adjusted returns of 3.80% for purchase clusters
versus 1.95% for other purchases, measured from the insiders' transaction dates.

Public followers face a different clock. After the 2002 disclosure change, the
cluster-minus-noncluster difference was 0.57 percentage points around disclosure
days 0-1 and 0.52 points over days 2-21. The aggregate days 22-90 difference was
not significant. Same-day clusters had weaker later returns than nonclusters;
longer clusters looked stronger. Selecting a cluster by its eventual length
before that length is observable would use future information.

These are historical abnormal-return comparisons using cleaned vendor data, not
net cash returns from this parser. The authors' definition also differs from a
30-day window. The [author's page][kang-author] lists this as a working paper.
Read the [original conference-hosted manuscript][kang-pdf], especially sections
2.2 and 4.2 and Table 7.

### Published evidence on colleagues' clusters

Alldredge and Blank, *Do Insiders Cluster Trades with Colleagues? Evidence from
Daily Insider Trading*, Journal of Financial Research (2019), report clustering
among colleagues and subsequent-month abnormal returns above 2% after clustered
purchases. This is relevant motivation for studying clustering separately from
routine trading. Only the [publisher's abstract][alldredge] was available for this
review; its complete timing, cost, and sample specifications were not verified.
The reported figure is therefore not an assumed return for a deployable strategy.

## The current observation is narrower than verified open-market buying

[SEC Form 4 Instruction 8][form4] defines `P` as an open-market **or private**
purchase of a nonderivative **or derivative** security. `S` has the corresponding
sale meaning. A transaction-code regex cannot establish a purchase of common
stock on the open market. Footnotes, security class, and the economic transaction
matter.

The current collector counts distinct reporting identities and assigns a
filing's collected codes to its reporting owners. Joint beneficial owners may
report the same economic transaction. Its newest retrieved filing timestamp
also does not reconstruct the first moment a cluster became observable to the
collector. Source parsers and a bounded filing window are candidate generators;
they are not a complete historical event reconstruction. See
[`agents/sec_agent.py`](../../agents/sec_agent.py).

None of the wording corrections accompanying this note changes counts,
thresholds, source acquisition, runtime classifications, or archive schemas.

## Proposed study: freeze the rule before observing outcomes

**Primary hypothesis:** In a universe declared before evaluation, verified
purchase clusters containing at least one previously classified opportunistic
buyer have positive incremental **net 20-session returns after actual public
receipt**, relative to matched single-buyer events and all-routine clusters.

This is an adaptation for a private research process, not an exact replication
of the papers. Begin with the existing two-buyer/30-day candidate threshold;
any revised definition must receive a separate strategy identity and study
declaration. The study protocol must resolve the following choices before
reading holdout outcomes.

| Component | Predeclared rule or requirement |
| --- | --- |
| Universe | Freeze membership and the declaration time. A historical market-wide claim requires membership known at the time and delisted outcomes. A prospective fixed selection supports conclusions only about that selection. |
| Qualifying purchases | Require verified nonderivative common shares, code `P`, acquired disposition, positive quantity and price, and open-market provenance in the filing and footnotes. Explicitly exclude or quarantine ambiguous transactions, private placements, and derivatives. |
| Identity and amendments | Deduplicate accession/transaction records and amendments; distinguish separate reporting CIKs from independent economic purchases. Retain reasons for exclusions and unresolved identity. |
| Cluster event | Require at least two distinct eligible economic buyers whose transaction dates lie within a trailing 30-calendar-day window. The event occurs when the second eligible buyer becomes known. This transaction-based reconstruction is additional private work, not a claim about the existing filing-window parser. |
| Routine classifier | At each calendar year's beginning, use only prior history. Require a trade in each of the preceding three years. Classify as routine if at least one calendar month contains a trade in all three years; otherwise classify eligible insiders as opportunistic. Preserve insufficient-history insiders as unknown. |
| Information cutoff | Preserve original filing contents/hash, accession, acceptance time where available, actual acquisition time, classification version, and decision receipt. A later filing or correction cannot change an earlier decision's information set. |
| Entry and exit | Enter at the first permitted session open strictly after the recorded decision and latency. Use the open 20 trading sessions later as the predeclared exit reference. Evaluate an additional session of entry delay as a named stress case. |
| Repeated events | Permit one active event per issuer over the holding window. Do not count daily rediscovery of the same cluster as independent evidence. |
| Comparison | Compare with single-buyer and all-routine events. Match or control for contemporaneously known size, industry, prior returns, and liquidity where credible historical data exist. Keep unknown-history events in a separately reported group. |
| Economic costs | Deduct modeled commissions, spread/slippage, and allocated recurring costs. Report omitted personal taxes, currency conversion, remittance, settlement, and other account-specific effects explicitly; do not label a result fully net when these matter but are absent. |
| Primary outcome | Estimate the incremental net return and uncertainty allowing for repeated issuers and common calendar shocks. The null is no positive incremental net effect. A broad-market benchmark separately measures opportunity cost, not complete risk adjustment. |
| Evaluation boundary | Declare development and holdout periods, the minimum independent-event requirement, and the analysis before evaluation. Keep additional counts, windows, roles, and horizons as separately named specifications. |

If transaction provenance cannot be established, the stricter open-market
hypothesis is not measurable from that record. Missing prices, delistings, or
failed acquisitions are coverage failures, not zero returns or permission to
discard losing events. Insufficient independent events means **inconclusive**;
a small number of paper decisions or a favorable calendar year is not a
statistical validation rule.

The private process must also freeze allocation size, maximum simultaneous
holdings, cash treatment, and turnover limits before a portfolio-level comparison.
An event-study average does not automatically describe the return, drawdown, or
capital requirements of a realizable portfolio.

## Implementation boundary

`portfolio_lab` measures supplied, frozen allocation decisions against one
validated price dataset with declared costs, observation timing, and execution
delay. It uses fractional adjusted economic units as a total-return proxy; these
are not executable share quantities or verified broker fills.

It does not implement the Form 4 verification process, the routine classifier,
an automatic purchase rule, or account-specific execution. The private process
must supply those verified observations and decisions. Keep source archives,
personal selections, and private decisions in the ignored workspace; public
examples must remain synthetic or explicitly cleared for redistribution.

## What could invalidate the result

- **Selection:** Backfilling a present-day watchlist does not remove survivorship
  or selection effects. Narrow observed coverage must remain visible.
- **Timing:** Insider trade dates, SEC publication, provider acquisition, and
  decision receipt are different times. Returns earned before feasible entry
  cannot support a follower's strategy.
- **Repeated specification search:** Choosing the best buyer count, dollar
  cutoff, holding period, or role after viewing returns selects noise along with
  any effect. Keep failed variants and reserve fresh data for the final rule.
- **Publication selection:** These positive studies are not a census of every
  unsuccessful investigation. The review cannot estimate the magnitude of
  publication bias from three selected papers.
- **Changing conditions:** Earlier samples, cleaned commercial databases, and
  historical execution assumptions do not establish the same opportunity in
  current public-data retail trading.
- **Cash-flow interpretation:** A positive total return is not a recurring cash
  distribution. A withdrawal plan requires separate capital, cost, drawdown,
  and funding analysis; none of the papers establishes passive income here.

## Sources and review scope

This was a bounded review of original author research, not a systematic review
or replication. Three Exa discovery queries requested 18 search results in total
(8 + 5 + 5); that search counter does not mean 18 independent empirical studies.
The full Cohen-Malloy-Pomorski and Kang-Kim-Wang manuscripts were inspected; the
Alldredge-Blank review is limited to the publisher abstract. SEC instructions
independently establish transaction-code meanings. All original-source links
above were checked during the 2026-10-23 research session.

[cmp]: https://dash.harvard.edu/entities/publication/73120379-15cc-6bd4-e053-0100007fdf3b
[cmp-pdf]: https://dash.harvard.edu/bitstreams/7312037e-2b77-6bd4-e053-0100007fdf3b/download
[cmp-nber]: https://www.nber.org/papers/w16454
[kang-pdf]: https://apjfs.org/file/download/6531?view=1
[kang-author]: https://sites.google.com/site/changmokang1/research
[alldredge]: https://onlinelibrary.wiley.com/doi/10.1111/jfir.12172
[form4]: https://www.sec.gov/files/form4.pdf
