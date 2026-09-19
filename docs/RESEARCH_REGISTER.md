> Historical September 16 handoff record. For the integrated OMNI-compatible
> 2.6.4 changes and current verification scope, read [INTEGRATION_2_6_4.md](INTEGRATION_2_6_4.md).
> Source and program descriptions below are dated research, not freshly verified terms.

# Research register and architectural choices

Reviewed on 2026-09-16. This is a decision-oriented register of primary project
and provider documentation plus original research abstracts. It is not an
exhaustive literature review, an audit of competing implementations, or evidence
of live coverage in this repository. Status below distinguishes implemented
changes from proposals. Source wording and numerical results are not copied into
our product claims.

## Architecture: borrow contracts, not entire stacks

**OpenBB: normalized provider models.** Its standardization documentation describes
consistent query/output types, explicit field conventions, and missing-value
normalization. Adopt the boundary between source acquisition and normalized
measurements; do not make a large platform dependency mandatory merely to obtain
that pattern. This repository's existing adapters remain useful. A common
`ProviderResult` envelope is proposed, not implemented in 2.6.3. [S1]

**LEAN/QuantConnect: identity survives a ticker change.** Its security-identifier
model distinguishes stable instrument identity from changeable display symbols
and market/type information. Adopt a dated instrument registry before large or
multi-market universe growth. Do not repurpose this descriptive tool into an
execution engine. Current JSON profile hashes identify configuration, not a
historical security master. [S2]

**FinRobot: deterministic computation with model-assisted narration.** The current
project README describes this separation and multiple generations of agent
frameworks. Borrow the separation, not a claim that agent debate validates facts.
Keep machine-readable numbers and source references upstream of prose, require
abstention on missing inputs, and measure factual fidelity. No FinRobot component,
new LLM framework, or autonomous action layer was added here. This comparison is
of documented design, not independently measured quality. [S3]

## Coverage, identity, and lawful acquisition

**Nasdaq symbol directories.** The official definitions include exchange/security
fields, test-issue markers, ETF distinctions, and file creation records; directory
files change through the day. Proposed importer: retain the source snapshot time,
exclude test issues, preserve share-class and venue distinctions, and publish a
candidate registry rather than auto-activating every symbol. A current directory
cannot by itself establish historical membership. [S4]

**OpenFIGI v3.** Mapping can yield multiple instruments or no result, with exchange,
MIC, currency, and security-type constraints. Preserve ambiguity rather than
selecting the first match. Respect returned rate-limit metadata. The documentation
contains differing unauthenticated batch-limit statements, so test conservative
requests rather than baking one excerpt into a universal guarantee. The registry
adapter is proposed; profiles do not call OpenFIGI. [S5]

**SEC fair access.** The published ceiling is ten requests per second per user
across machines, not per thread or agent. Our next adapter layer should use a
shared host budget, identified user agent, bounded retries, and cached issuer
metadata. More workers must not multiply the effective limit. The existing
OpenInsider shared-acquisition pattern is a useful local precedent. [S6]

**yfinance scope and repairs.** The maintainer warns that the library is unofficial
and points users to the source's usage terms. Its price-repair documentation also
explains reconstructed prices and adjustment changes. Keep raw, adjusted, and
repaired lineage distinct; do not enable repairs globally and call the result
verified. The implemented cache fix requires compatible endpoints in a refill,
but does not establish complete corporate-action provenance. Public fixtures stay
synthetic; MIT code licensing is not a market-data redistribution license. [S7, S8]

## Point-in-time availability

**ALFRED vintages.** ALFRED preserves historical versions of economic observations,
including their revisions. A future macro adapter should store the vintage and
availability context used for each report; using today's revised series in an
old replay would answer a different question. No ALFRED adapter was added in
this release. [S9]

**ClinicalTrials.gov.** Its API documentation supplies a refresh marker through
`dataTimestamp`; it also documents ingestion changes. The study structure
separates last update submission from public posting. A completion estimate,
public update, provider refresh, and our observation time must be separate fields.
Add version-marker/schema fixtures before treating registry diffs as precise
new-event timestamps. A planned event is not an outcome. [S10, S11]

**SEC dissemination.** Official access documentation distinguishes submission,
filing, and public dissemination behavior. Date-only filing labels should not be
silently upgraded to exact midnight availability. Preserve a precision flag and
use the available source timestamp or a conservative availability bound in a
future replay contract. [S12]

## Research integrity and numerical narration

**Multiple-signal selection.** Novy-Marx's original paper analyzes how choosing and
combining signals can inflate apparent in-sample results. Before changing weights
or searching a large indicator set, record every tried variant and freeze a
forward evaluation cohort. Cross-family agreement is not an estimated success
probability. This release changes correctness and disclosure, not learned
predictive weights. [S13]

**Deflated Sharpe ratio.** Bailey and Lopez de Prado discuss adjustment for
selection bias and non-normal returns. That is a useful future research reference,
not a reason to paste a Sharpe statistic onto an event-outcome ledger. Define the
measured process, trial family, return series, dependence, and costs first. No DSR
implementation or investment-performance claim is made here. [S14]

**FinQA and TAT-QA.** These original benchmarks test numerical reasoning over
financial reports, with supporting text/table evidence and explicit operations.
They motivate test cases for period alignment, unit scales, signs, derived values,
and traceable arithmetic in an optional narrative layer. Their 2021 model results
are not evidence of today's model accuracy. Build repository-specific held-out
fixtures as well, because these benchmarks do not certify news/event provenance
or live provider freshness. [S15, S16]

## Maintainer support and adoption

**Codex for Open Source.** OpenAI's current form emphasizes active maintenance,
usage, adoption or ecosystem importance, and describes six months of ChatGPT Pro
for selected maintainers, with other support conditional. It does not publish a
numerical star requirement. A reliable project and truthful maintenance evidence
are the goal; selection is not assured. No application was submitted. [S17]

## Primary references

- [S1: OpenBB standardization](https://docs.openbb.co/odp/python/developer/standardization)
- [S2: QuantConnect security identifiers](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/security-identifiers)
- [S3: FinRobot project README](https://github.com/AI4Finance-Foundation/FinRobot/blob/master/README.md), reviewed directly through the GitHub connection.
- [S4: Nasdaq symbol-directory definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs)
- [S5: OpenFIGI API documentation](https://www.openfigi.com/api/documentation)
- [S6: SEC developer resources and fair access](https://www.sec.gov/about/developer-resources)
- [S7: yfinance maintainer documentation](https://ranaroussi.github.io/yfinance/)
- [S8: yfinance price repair](https://ranaroussi.github.io/yfinance/advanced/price_repair.html)
- [S9: ALFRED help](https://alfred.stlouisfed.org/help)
- [S10: ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/api)
- [S11: ClinicalTrials.gov study data structure](https://clinicaltrials.gov/data-api/about-api/study-data-structure)
- [S12: SEC accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [S13: Novy-Marx, Backtesting Strategies Based on Multiple Signals, NBER 21329 (2015)](https://www.nber.org/papers/w21329)
- [S14: Bailey and Lopez de Prado, The Deflated Sharpe Ratio (2014)](https://doi.org/10.3905/jpm.2014.40.5.094)
- [S15: Chen et al., FinQA (EMNLP 2021)](https://aclanthology.org/2021.emnlp-main.300/)
- [S16: Zhu et al., TAT-QA (ACL-IJCNLP 2021)](https://aclanthology.org/2021.acl-long.254/)
- [S17: OpenAI Codex for Open Source](https://openai.com/form/codex-for-oss/)

Recheck provider terms, endpoint behavior, and program details before a later
release. A documentation review is not an entitlement check or a live data test.
