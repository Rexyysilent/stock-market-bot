# Source-contract and operating-evidence gap ledger

Checked 2026-10-20 against the pinned public repository and primary documentation.
This ledger distinguishes missing source contracts from engineering already
implemented. All source-use decisions and AI/domain labels remain pending human
review. Research retrievals are not prospective pilot observations or permissions.
Raw source bodies, credentials and actual operator/customer evidence are absent
from the public candidate.

## Verified source contracts and bounded implementation

| Source / exact primary reference | Verified evidence and implemented support | Unresolved input / consequence |
| --- | --- | --- |
| [SEC EDGAR access guidance](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), post-acceptance corrections and index-file sections | Daily index fields, current-quarter full-index scope, weekly full/quarterly rebuilding; bounded parser, snapshot comparisons, separate acquired-set/correction receipts | Daily history cannot prove later deletions; index dates cannot prove first public availability. Missing entries remain discrepancies. Source-approved real surveillance/retention and actual availability evidence are still required. |
| [SEC full-index directory](https://www.sec.gov/Archives/edgar/full-index/) | Exact master-index and quarter route shapes; disabled full/quarterly collector, New York quarter binding, idempotent snapshots/comparisons | The 5 MB response budget can reject large quarters rather than silently truncate. Complete source snapshots and source-purpose approval are not supplied. Full-index rollover cannot compare as the same quarter. |
| [FDA MedWatch feed help](https://www.fda.gov/safety/medwatch-fda-safety-information-and-adverse-event-reporting-program/medwatch-rss-feed) and [observed canonical RSS](https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml) | Official help route redirected from the old mixed-case path; bounded research GET of canonical route returned 20 GUID/date items; strict RSS parser checks native IDs, dates/timezones, duplicates, caps and missing bodies | Retention and longitudinal GUID stability are unknown. Midnight EDT entries are feed dates, not demonstrated first-availability times. Runtime redirect following remains refused and canonical route use requires exact typed approval. |
| [FDA oncology notifications](https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications) | Named limited notification index with date-only semantics; metadata parser and route contract | It is not an all-FDA approval, device, recall or labeling denominator. Broader endpoint guesses remain blocked. |
| [Beam official RSS help](https://investors.beamtx.com/stockholder-tools/rss-feeds) and [canonical feed](https://investors.beamtx.com/rss/news-releases.xml) | Bounded research GET returned 10 GUID/date items; disabled exact single-issuer feed support | Retention, longitudinal ID stability, availability timestamps and actual source-use/field grants are absent. Other issuers need their own verified canonical route; no inferred URL fallback. |
| [ClinicalTrials.gov API documentation](https://clinicaltrials.gov/data-api/api) and retained A/B research | Existing version-pinned trial snapshots/changes; new optional exact evidence-backed clinical definition catalog | No genuine reviewed clinical endpoint/arm/cohort catalog is installed. Current trial metadata cannot be projected into older disclosures; results posting does not establish outcome or approval. |
| [NRC APS Developer Guide v1.0](https://adams-search.nrc.gov/assets/APS-API-Guide.pdf), sections 3.1 and 4 / appendices A and C | Exact POST search and GET accession routes, bounded filter/sort/body contract, accession/docket identity, separate document/addition/observation times and metadata-first leads | Subscription key not obtained. Example DateAddedTimestamp has no explicit timezone; no assumed timezone. Mutable results/counts and undocumented stable snapshot pagination cannot certify a closed historical denominator. Project/applicant/issuer mappings and authorization scope need source-backed review. |
| [NRC licensing process](https://www.nrc.gov/materials/fuel-cycle-fac/new-fac-applicants/understanding-the-licensing-process) | Separate licensing-stage representation; pre-application, filing/docketing and issued authorization are not interchangeable | A title/stage mention cannot establish a project-specific approval. Earlier primary retrieval succeeded; a later browser text reopen failed and is retained as a failed check in the vertical ledger. |
| [EIA API documentation](https://www.eia.gov/opendata/documentation.php) and [official OpenAPI ZIP](https://www.eia.gov/opendata/eia-api-swagger.zip) | Narrow facility-nuclear-outage metadata/data GET routes, explicit sorts/offset/length, data strings, units/facets and response caps; credential references and request-echo redaction | Current route metadata/units/facet values and actual credential/approval are absent. Generic OpenAPI does not substitute for a pinned real metadata response. Revised periods/mutable offsets need actual surveillance. |
| [EIA-860 annual data](https://www.eia.gov/electricity/data/eia860/) and [2025 workbook bundle](https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip) | Actual 2025 generator and ownership filenames, sheets, second-row headers and Excel percentage cells verified; bounded OOXML parser separates operator/owner and vintage/stake validity | Other vintages/layouts are unsupported. Local ZIP hashes prove byte identity, not authenticity. Operator-as-owner fallback needs complete matching generator and ownership inventories; annual vintage is not an exact current sale/effective date or parent-company crosswalk. |
| [USAspending award contract at pinned revision](https://github.com/fedspendingtransparency/usaspending-api/blob/a31240757e5987b994692322c317ae55668c6983/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md) | Narrow read-only award POST; exact generated IDs, recipient and aggregate fields; bounded page metadata | Award aggregate is not a transaction delta; source update/deletion completeness and parent issuer mapping remain unknown. |
| [USAspending transactions contract at pinned revision](https://github.com/fedspendingtransparency/usaspending-api/blob/277763cd72dc9e5838f52ec4176edbfef79fd5e6/usaspending_api/api_contracts/contracts/v2/transactions.md) | Narrow transaction POST, action date, modification, obligation delta, deterministic IDs and bounded pages; delayed-scope parser | Currency is not explicit in inspected contract; missing/null modification stays unknown. Action date is not publication/availability. Arrival/update/delete surveillance and recipient-to-listed-parent evidence remain missing; no real-time or issuer-revenue inference. |
| [USAspending data guide](https://www.usaspending.gov/data/about-the-data-download.pdf) | Delayed reporting and distinct action/publication semantics inform the parser's explicit lag uncertainty | A general delay description cannot establish record-specific latency. |

Primary retrieval details, exact hashes and failed checks are preserved in
[source-route-observations.json](source-route-observations.json),
[vertical-source-contract-ledger.json](vertical-source-contract-ledger.json) and its
referenced checksum manifest. A direct Beam/FDA research request verified only the
observed small snapshot. No scheduled watch, NRC/EIA credential request, purchase,
account, real pilot or source-policy activation was performed.

## Clinical and amount grammar intentionally refused

| Missing representation | Safe implemented behavior | Evidence needed for a future extension |
| --- | --- | --- |
| General HTML/PDF tables, merged/inherited cells, footnotes or units in separate cells | Reject the complete unsupported table; accept only declared plain-text pipe tables with exact block/header/cell proof and repeated row scope | A verified structural adapter preserving every row, column, unit, footnote and provenance boundary |
| Censoring, unevaluable/missing assessments, changing risk sets or recurrent-event denominators | Explicit abstention; enrollment is not substituted as the result denominator | Source-specific disposition denominator, unit, population/cohort, assessment time and analysis rule |
| Severity versus seriousness, SAE/TEAE/causality and dose-cohort safety transfers | No safety endpoint projection from unsupported language | Exact separate safety definitions, denominator and cohort contract |
| Arbitrary model adjustments, transformed/approximate statistics, unsupported tests or multiplicity methods | Bounded HR/RR/OR/percentage-point difference and declared CI/p/count forms only; unsupported qualifiers abstain | Method-specific grammar and exact scope/units, supported by adversarial controls |
| Postposed/conflicting p qualifiers and negated/compound analysis descriptions | Both supplied fixes remain conservative; nine previous failures remain negative controls | No broadening is proposed without exact unambiguous source support |
| Money tables, shared-currency shorthand, unsupported scales/realization, multiple ambiguous contracts | Keep safe atoms only; remaining statements become leads and unknown fields stay null | Explicit per-atom currency/operator/role/identity/period/contingency/additivity support |
| Currency conversion, arithmetic totals, annualization, received cash, revenue or runway | No derived output; exact scalar control and scoped bounds/ranges remain separate | Outside this workbench's admitted evidence contract |

Primary outcomes sharing a trial scope are deliberately treated conservatively:
a genuinely different endpoint/analysis version may remain contested until the
operator provides the exact distinct scope. This can reduce yield. It cannot be
resolved by assuming two contradictory statements belong to different milestones.

## Real evidence required before operating readiness

| Missing actual record | Implemented intake / validation | Consequence now |
| --- | --- | --- |
| Source-purpose and route approvals, field grants, counsel review | Typed scoped current attestations, policy checks, expiry/revocation, field/purpose rendering | New routes disabled; unauthorized fields/card exports withheld |
| Reviewed clinical/issuer/security/crosswalk definitions | Versioned exact source evidence and registry/catalog fingerprints | Unsupported identity or scope remains unresolved; no membership changes |
| Frozen future protocol and elapsed complete observation window | Source-first enumeration, independent publication census, closure/coverage/timing/run importers | No genuine recall, precision, timing or scheduled-session result |
| Human adjudication and external untouched custody | Exact review/mapping/custody/access/seal/consumption receipts with current rederivation | Missing decisions stay pending; local logs do not authenticate a human or prove external non-access |
| Actual review effort, cost inputs, useful briefs and consented customer feedback | Exact clocks, complete category/useful inventories, signed decimal economics, feedback census | Actual costs/customers/useful-event economics remain unknown; templates and synthetic rows count zero |
| Independent deployment/scheduler/model/private-state verification | Runbook, reproducible offline checks and handoff | No public push/merge, deployment, model execution, account changes or distribution was performed |

These are evidence and authorization boundaries. They are not hidden engineering
TODOs or successful gate results. Every corresponding gate remains pending.
