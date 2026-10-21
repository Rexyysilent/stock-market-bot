# Work Package D — vertical sources, measured expansion and operating design

Prepared 2026-10-19 against public revision
`d35f667aebd3c2590cabbbe80e51076b3a1fa788`.
**Design only. AI assessments, entity mappings and all new source approvals are
pending human review.** No vertical adapter, subscription, scheduler, membership,
commercial distribution or customer contact has been activated.

## 1. Existing behavior confirmed from the exact code

- `omni/identities.py:EntityRegistry` already supports issuer, security, asset,
  trial, project and contract kinds, evidence-backed aliases, `known_from` and
  validity intervals. `resolve` returns mentions and separately sourced
  relationships. A mention does not establish economic exposure. There is no
  seeded NRC/EIA plant crosswalk or typed ownership-stake validator.
- `omni/acquisition.py:SourceClient.get` permits only approved public HTTPS
  routes, rejects redirects, reserves shared daily and cycle budgets, applies a
  response-size limit, and persists backoff. It currently implements GET with
  fixed headers, not the authenticated POST searches required by NRC APS.
- `omni/evidence.py:source_decision` checks six purposes separately:
  acquire, store_text, local_analysis, review_display, model_processing and
  commercial_display. `document` requires both acquire and store_text permission.
  The existing registry grants only bounded metadata acquisition for the seeded
  SEC/ClinicalTrials.gov/FDA routes; no NRC, EIA or procurement route is approved.
- `omni/evaluation.py:dependent_gates` lists the missing OMNI-10/11/12 receipts
  and computes explicit candidate set difference. It never changes membership.
  Its truthy receipt-presence checks are requirements reporting, not authenticated
  validation of a rights review, cost measurement or customer interview.

Package D adds documents, proposed contracts and synthetic acceptance fixtures.
It makes no changes to those runtime functions or the source/entity registries.

## 2. Remaining requirements, in practical order

1. Finish the Package B correction/quarantine contract and Package C prospective
   evidence bridge before relying on additional sources. Shared architecture and
   reviewed user value remain prerequisites for OMNI-10.
2. Review each vertical source's exact route, content provenance and six purposes.
   Government hosting alone does not establish the status of issuer-authored PDFs.
   Confirm permitted credentials and quotas before a network canary.
3. Implement a small read-only JSON request extension with exact methods, endpoints,
   bounded bodies and secret redaction. Preserve GET behavior for existing adapters.
4. Establish versioned plant/unit/project/legal-entity mappings with separately
   evidenced ownership and operation. Unknown or stale ownership must stay unknown.
5. Prove source coverage, revisions and delayed availability for each adapter.
   A search hit without downloadable content is a lead, not complete evidence.
6. Obtain Wave A measurements before selecting an expansion batch. Obtain reviewed
   useful briefs, actual source-purpose/counsel review and customer feedback before
   a commercial-readiness claim.

## 3. Source evidence ledger and limits

The machine-readable ledger is `vertical-source-ledger.json`. Research verified
public documentation on 2026-10-19; it did not exercise authenticated data APIs.
Publication and reporting dates below are source facts, not local observations.

### NRC ADAMS and licensing

The [ADAMS notice](https://www.nrc.gov/reading-rm/adams) distinguishes WBA interface
retirement on February 27, 2026 from the June 30, 2026 WBA API retirement date.
The proposed integration uses APS, even though historical WBA examples remain on
some official pages.

The [APS API guide](https://adams-search.nrc.gov/assets/APS-API-Guide.pdf) requires
an approved subscription key in `Ocp-Apim-Subscription-Key`. It documents
`POST https://adams-api.nrc.gov/aps/api/search` and accession-specific GET,
`skip` pagination, and separate `DocumentDate` / `DateAddedTimestamp`.
Its sample added timestamp has no timezone. Neither a UTC assumption nor a
date-only watermark is justified by that example. The [APS home notice](https://adams-search.nrc.gov/)
says a newly indexed document can take up to 20 minutes to become downloadable.

The [fuel-cycle licensing process](https://www.nrc.gov/materials/fuel-cycle-fac/new-fac-applicants/understanding-the-licensing-process)
separates intent/pre-application, submission, acceptance, technical/environmental
review, hearings and license issuance. A docket can be opened at the intent stage;
a docket identifier alone does not prove an accepted application or issued license.
Reactor, fuel-cycle and other authorizations need their own applicable stage rules.

The verified [BWXT April 7 release](https://investors.bwxt.com/news-releases/news-release-details/bwxt-begins-nrc-engagement-licensing-new-defense-nuclear-fuel)
is a useful multi-project control: future Erwin enrichment licensing, a previously
announced contract, existing NFS operations and an Oak Ridge facility opening are
different facts. The expected Q1 2027 application is a quarter-precision future
event. Package A's 2022 BANR NRC-hosted issuer letter is a different project.

[NRC's reuse notice](https://www.nrc.gov/site-help/disclaimer) distinguishes government
work from copyrighted material requiring the original owner's permission. An
NRC-hosted issuer letter therefore retains issuer reporting-party and rights review.

### EIA observations and ownership

The [outage notice](https://www.eia.gov/nuclear/outages/) points to NRC reactor
status data and describes weekend/holiday lag. The [NRC status report](https://www.nrc.gov/documents-reports/document-collections/events-reports-associated-with/power-reactor-status-reports/ps)
labels its information facility-provided and unevaluated, with a 4–8 a.m. Eastern
collection window. That window is not a precise webpage publication timestamp.
NRC and EIA versions of the same observation share lineage.

The standalone [reactor ownership product](https://www.eia.gov/nuclear/reactors/ownership.php)
was discontinued September 3, 2026; its last release was August 20, 2025.
Use a frozen [Form EIA-860](https://www.eia.gov/electricity/data/eia860/) vintage.
The page identifies final 2025 data released September 10, 2026. Its Owner file
covers shared ownership and owner/operator differences. The documented
absent-owner-row fallback requires a complete, correctly joined, matching-year
dataset. A failed download, filtered file, unresolved generator or different
vintage cannot establish 100% ownership. An annual observation also does not
establish an unchanged stake as of today.

[EIA API documentation](https://www.eia.gov/opendata/documentation.php) requires
an individual key, uses string-valued data, and limits JSON returns to 5,000 rows.
It documents offset/length and sorting, including recent fixes to totals and
date-boundary behavior. Explicit ordering and coverage checks remain necessary.
The nuclear [browser route](https://www.eia.gov/opendata/browser/nuclear-outages/facility-nuclear-outages)
is verified, but its live facet names, types and plant crosswalk are still unknown
in this work. Do not invent those fields from a secondary catalog.

EIA requires its key in the URL, and its documented response echoes request
parameters. Scrub the key from URLs, responses, exceptions and receipts before
persistence. [Reuse guidance](https://www.eia.gov/about/copyrights_reuse.php) and
[API terms](https://www.eia.gov/opendata/terms-of-service.php) support a review of
government data use, with attribution and third-party exceptions. They are not
an operator approval for this proposed product.

### Public procurement, separately scoped

Propose USAspending v2 as delayed award/transaction context. Its
[endpoint documentation](https://api.usaspending.gov/docs/endpoints) currently
states that authorization is not required, and the [official award-search contract](https://github.com/fedspendingtransparency/usaspending-api/blob/master/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md)
specifies read-only POST search. A production data request was not made.

Treasury's [About the Data](https://www.usaspending.gov/data/about-the-data-download.pdf)
describes reporting plus publication delays, including a 90-day DoD/USACE
procurement delay. A signed/action date is not public availability. The documentation's
general timing statement and worked business-day examples are not a live-service
latency guarantee. This route cannot substantiate real-time defense award coverage.

The [GSA Contract Awards API](https://open.gsa.gov/api/contract-awards/) is a
separate alternative requiring an account/key. Its revealed responses omit certain
parent-entity fields, and deleted contracts have a separately described retrieval
path. No GSA credential or entitlement was created or assumed. Procurement rights
and current correction/update completeness still need their own review.
An attempted USAspending `/website-policies` page returned 404; this ledger does
not substitute a guessed policy or treat unauthenticated access as a use grant.

## 4. Concrete implementation proposal and adversarial fixtures

### 4.1 Small interfaces, shared controls

Add `omni/verticals/nrc.py`, `eia.py` and, only after separate review,
`procurement.py`. Each exposes a pure parser plus an explicit collector:

```python
parse_nrc_metadata(payload, *, query_fingerprint, observed_at)
collect_nrc_aps(client, *, approved_query, checkpoint)
parse_eia_observations(payload, *, metadata_snapshot, observed_at)
collect_eia(client, *, approved_series, checkpoint)
parse_award_transactions(payload, *, contract_snapshot, observed_at)
```

All return candidates, observed source metadata, coverage gaps, continuation and
request counts using the existing receipt/store pattern. They do not return
signal eligibility or alter editorial/quantitative membership.

Extend the client with a deliberately narrow `read_json(method, url, body=None,
credential_ref=None)` operation. Allow POST only for a reviewed read-only endpoint
with a bounded query schema. Default old policies to GET. Reserve each attempt,
deny redirects, apply the existing public-address and path checks, and keep
credential values out of persisted state. An unapproved method/body/route fails
before DNS and budget use. Do not increase the daily shared budget automatically.

For APS, freeze the official contract and selected query fields before writing a
live parser. Paginate deterministically, retain overlaps, and stop with a gap on a
cap, repeated page, changed query, unknown total or interrupted window. Preserve
metadata leads while downloads are pending. Retry under backoff and the existing
bounded queue; the 20-minute source notice is not permission for a busy loop.

For EIA, first pin the approved route's metadata, units, facets and frequency.
Reject unknown columns, undocumented sentinels, nonfinite values and mismatched
units. Keep period, source-revision marker and local observations distinct.
A zero reported power observation is not a cause-of-outage or safety conclusion.
Revised historic periods append revisions; they do not rewrite earlier observations.
NRC-to-EIA lineage links require explicit source attribution and row/unit mapping.

For procurement, separate the award-family key, native transaction key, modification,
action date, update marker and observed time. Preserve signed obligation changes,
ceilings, exercised options and outlays as different amount types. An award search's
aggregate cannot establish a new transaction. Never sum parent IDV ceilings with
child orders as realized revenue. Correction/deletion coverage is a blocking
contract question before activation.

### 4.2 Entity and time contract

`proposed-vertical-contracts.json` defines typed identifiers, edge fields, time
fields, lifecycle labels and validation requirements. Retain current entity kinds:
a physical plant/unit can be an `asset` with an explicit subtype; a proposed
facility is a `project`. Introduce no new universal entity framework.

Use source-native identifiers where actually supplied: NRC accession and docket
arrays; EIA plant code plus generator ID and dataset vintage; award-family and
transaction identifiers; recipient UEI; independently sourced issuer CIK and
security/listing identifiers. Never create a docket or CIK from a name match.

Ownership edges store direct legal owner, asset/unit, percentage, effective interval,
known-from time, source vintage, exact field/row proof and review state. Operator,
applicant, licensee, contractor and indirect parent relationships are distinct.
Do not multiply a parent percentage through a chain until each edge is reviewed
for the same date and economic definition. Percentage totals are a diagnostic,
not a reason to manufacture a residual owner.

Times have independent fields for document date, source posting time, reporting
period, contractual/effective date, first observed and retrieved time. Preserve
unknown timezone and date/month/quarter precision. Add explicit provenance to
future timezone mappings rather than changing `time_interval` to accept naive
instants as UTC.

`fixtures/omni/vertical_sources.json` contains 20 synthetic, design-only acceptance
cases. They cover the handoff's T08/T12/T14/T20–23/T28/T44 and new source-specific
cases. None is a live canary, parser execution result, human label or pilot sample.

### 4.3 OMNI-11 measured batch selection

1. Obtain the actual deployed cohort, collector targets and versioned Wave A review.
   Freeze those inputs and a common observation window. No Wave B list is bundled.
2. Require explicit candidate identity/alias evidence, direct subject roles,
   source availability and purpose permissions before considering an addition.
3. Run approved candidates in an isolated shadow scope. Use existing broad discovery
   where appropriate; adding a security must not silently fan out every collector.
4. Measure net new, correctly mapped, human-reviewed useful events after origin
   deduplication against baseline coverage. Also measure negative-outcome recall,
   unknown-issuer recall, abstentions, missed events, reviewer minutes, backlog,
   requests/retries and separately attributable direct cost.
5. Compare matched operating windows and disclose confounders, sparse counts and
   correlated events. Compute marginal cost per net useful event from aligned
   incremental cost and event denominators. Zero useful yield has no finite unit
   cost; missing measurements remain missing.
6. Choose a small explicit batch only after the owner reviews those measurements
   and baseline service degradation. Publish a versioned editorial membership
   change only with separate authorization. Retain prior events on rollback.

A proposed expansion receipt binds baseline/candidate scope hashes, measurement
window, implementation/policy versions, receipt references, counts, cost basis,
mapping errors, review load, missing evidence and human decision. A hand-filled
truthy object is insufficient evidence. No source yield, customer demand or
marginal cost was measured during this task.

### 4.4 OMNI-12 operating package

`OPERATING-REVIEW.md` supplies the field-level rights inventory, correction and
coverage disclosure policy, interview protocol, runbook and economics definitions.
`omni-operating-economics.xlsx` is delivered with the review bundle. Its planning
capacity/time assumptions are visibly hypothetical, all unknown prices and
actual costs are blank, and useful-event cost requires an actual denominator
receipt. It makes no revenue, customer-conversion or investment-return forecast.

## 5. Acceptance, actual execution and unresolved questions

**Executed for the A/B/C runtime patch:** 73 offline scripts passed on Python 3.11
and 3.12 in isolated public checkouts, including 106 OMNI unittest cases. Compilation,
export-schema checks, JavaScript syntax and the additional dashboard server/security
suite also passed. See the final validation manifest for complete commands and
secret-scan results. The historical public CI belongs to the pinned baseline.

**For D:** documentation/JSON consistency checks and spreadsheet calculation/visual
checks are reported separately in the final bundle. No vertical parser, API
canary, source agreement, operating pilot, cost collection or interview was run.

Before implementing a D adapter, require all 20 proposed fixture outcomes, unchanged
existing A/B/C tests, rejected unauthorized requests before transport, actual
coverage debt under caps/outages, append-only corrections, preserved source lineage,
and no membership/signal changes. A limited authorized canary must then report its
real route, interval, API contract, timing precision, quota and uncovered periods.

Open questions: APS timezone, quota and search completeness; the live EIA nuclear
schema and exact unit crosswalk; authorized secret injection/redaction; procurement
update/deletion keys and current field permissions; actual Wave A yield and review
load; rights/counsel review of the intended offering; prospective adjudication,
usefulness interviews and measured operating costs.

## 6. Next bounded task and dependencies

Review the A/B/C patch and the pending source/clinical interpretations first.
The next implementation task is Package B's immutable correction/quarantine record
and Packet/Review/Focus invalidation, followed by Package C's blinded prospective
gold and latency/untouched-test importers. Use a new implementation/corpus era.
NRC/EIA can then begin as a disabled, fixture-driven adapter spike once shared
architecture, source policy and user-value dependencies have genuine receipts.
