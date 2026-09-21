# Measurement-integrity integration — 2.6.5

This unreleased integration branch changes measurement semantics and therefore
uses pipeline/ledger era 2.6.5 while retaining schemas 2.8 and 2.9. Historical
2.6.4 archives keep their original bytes and era and remain readable.

## Included corrections

- A ledger return window now resolves both endpoints from one immutable price
  acquisition. Overlapping refills and vendor revisions cannot replace only one
  endpoint. The acquisition records provider, interval, requested range,
  adjustment convention, currency when known, acquisition time and content hash.
- Legacy `(ticker, session)` rows are not rewritten or assumed compatible. A new
  strict calculation refetches the complete window or remains unavailable.
- Missing or non-finite operating cash flow no longer becomes zero burn, GREEN,
  or a 999-quarter sentinel. Missing debt and market capitalization remain null.
  The result records its source metric, periods, currency availability and valid
  quarter count. Free cash flow and non-current liabilities are not silently
  relabelled as operating cash flow or total debt.

## Compatibility and remaining limits

Empty provider responses remain retryable and existing valid raw, SPY and excess
return fields retain their meaning. Adjustment labels record the requested
provider convention; they do not certify every corporate action. Ticker remains
the current ledger instrument key, and currency may be unknown under the current
provider adapter.

This increment does not yet repair frozen historical cohort consumption, partial
benchmark finalization, matched-support statistics, social censoring, replay
knowledge cutoffs or reporting-origin lineage. Those capabilities must remain
descriptive or explicitly unavailable as documented in the v3.8 roadmap until
their acceptance cases pass.
