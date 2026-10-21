# Work Package B — coverage, origins, permissions and amounts

Research date: 2026-10-19. Baseline: `d35f667aebd3c2590cabbbe80e51076b3a1fa788`.
All source-policy interpretations and proposed labels are AI drafts pending human
review. The runtime policy and universe are unchanged. This package contains
three bounded fixes plus designs for the remaining lifecycle work.

## 1. Existing behavior confirmed

`acquisition.collect_edgar` reads at most 12 pages of 100 Atom entries per explicit
call, defaults to three pages, retains overlapping watermark entries, and marks
initial windows and unfinished/resumed mutable offsets as partial. It does not
convert an offset continuation into continuous coverage. `SourceClient` bounds
response size, attempts, metadata and documents; reserves daily budgets before
attempts; refuses redirects and nonpublic DNS destinations; and applies the local
shared SEC gate. That gate spaces local reservations at one-third second but does
not coordinate requests from other machines.

`reconciliation.reconcile_days` accepts one to seven calendar days, waits until
06:00 America/New_York the following day, and receipts observed daily indexes
separately from feed progress. Weekend schedule receipts are not positive index
observations. `parse_daily_index` checks rows, paths and filing dates, groups
co-registrants under an accession, preserves date precision, and caps input at
50,000 rows and the shared five-million-byte response limit. A 404, malformed
index or open day stays a gap. Holidays are not guessed to be empty indexes.

`runner.watch_edgar` resumes at most three pending exhibits per pass, retains
cached successful bodies and combines the current filing's evidence before
validation. Cached bodies are not automatically refreshed on every run.
`regulatory.parse_notifications` and `feeds.parse_feed` have scoped metadata
contracts; neither establishes all-approval coverage. `amounts.contract_amounts`
accepts one unambiguous explicit exact USD contract ceiling, option or obligation.
It does not accept general accounting, financing proceeds or complex amounts.

## 2. Concrete findings and applied patch

| Priority | Reproduced defect | Applied change and practical limit |
| --- | --- | --- |
| 1 | A second index added co-registrant CIK 456 to an accession first observed under CIK 123; the old-candidate merge discarded 456 despite a successful changed-index receipt. | `merge_index_candidate` keeps new index-owned metadata, a separate snapshot/hash, and immutable before/after differences. It preserves the observed feed timestamp and cached work for the same route/form. |
| 2 | A permitted-looking path containing `../../../../files/...` passed policy checks; Requests normalized it outside the EDGAR path. | `evidence.public_url` rejects dot segments, encoded separators, nested escapes and control characters before permission, DNS or budgets. This is a route-boundary repair, not a claimed private-network exploit. |
| 3 | A shared SEC gate backlog/clock-skew `RuntimeError` escaped collection without a coverage receipt. | `SourceClient.get` turns that refusal into `Deferred(sec_rate_gate_unavailable)`. The watcher persists partial coverage and makes zero HTTP calls. Unexpected other exception classes remain errors. |

The index receipt contract advances to `omni-edgar-index-2`. New receipts record
`comparison_basis`, prior/current metadata and hashes, changed/newly observed
fields, `human_review_status=pending`, and `claim_revocation_performed=false`.
A first comparison with a feed candidate is distinguishable from comparison
with an earlier index observation. Repeating identical metadata does not create
a second metadata-change observation. Existing source receipts remain immutable.
`watch_edgar` preserves index-only metadata when a later short feed entry arrives.
A changed route/form does not reuse an inapplicable cached listing or status;
existing document-job history is retained.

**This patch does not implement automatic claim revocation or retraction
adjudication.** A metadata difference can be enrichment, a correction, or an
unresolved discrepancy. Previously reviewed evidence is not automatically
re-fetched or revoked by the new metadata receipt. That lifecycle needs a
separate revision-bound design below.

## 3. Verified source evidence and route/purpose register

The machine-readable [source-policy register](source-policy-register.json)
contains eleven route records, primary URLs, section locators, access date,
verified facts, implementation inferences, ambiguities and actual configuration
state. No raw source bodies or new approvals are included.

### Primary evidence that affects implementation

- [SEC Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), “Business hours and dissemination” and “Post-acceptance corrections and deletions”: nightly index publication is not immediate delivery; later removals need not update old daily/feed/oldload indexes. Weekly full/quarterly rebuilds incorporate post-acceptance changes. The page's historical publication date is not its retrieval date.
- [SEC Webmaster FAQ](https://www.sec.gov/about/webmaster-frequently-asked-questions), reuse, lag and timestamp questions: the SEC describes broad reuse for government-created and public filing content, while identifying exceptions; there is no guaranteed first-website-availability timestamp. [Developer Resources](https://www.sec.gov/about/developer-resources) requires aggregate fair access across machines and a declared contact User-Agent.
- [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces), “Submissions” and “Update Schedule”: current submission arrays have a bounded recent history and point to additional history files. Typical processing delay is not an availability guarantee. Last reviewed April 8, 2025; accessed October 19, 2026.
- [FDA oncology index](https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications), opening scope statement: the index omits some approvals and label changes and refers readers to Drugs@FDA. Its action/date text does not eliminate the need to validate the underlying action and its scope.
- [FDA website policy](https://www.fda.gov/about-fda/about-website/website-policies), copying/linking section: general FDA content is reusable unless an exception is noted; source links and copying dates help readers check subsequent updates. This statement is evidence for review, not an operator approval in OMNI.
- [MedWatch RSS documentation](https://www.fda.gov/safety/medwatch-fda-safety-information-and-adverse-event-reporting-program/medwatch-rss-feed) exposes a legacy HTTP/mixed-case route. The configured HTTPS feed remains disabled, and its current redirect/canonical behavior was not live-polled.
- [Beam RSS page](https://investors.beamtx.com/stockholder-tools/rss-feeds) confirms an issuer RSS facility. The research reader did not expose its XML endpoint. [Beam corporate terms](https://beamtx.com/terms-of-use/) describe conditional noncommercial informational uses; applicability to the investor subdomain and the project purpose requires review. No guessed RSS URL was configured.
- [BWXT terms](https://www.bwxt.com/terms-of-use/), site-use section, restrict copying, distribution and retrieval-system uses. [Business Wire terms](https://www.businesswire.com/legal/terms-of-use), effective January 1, 2024, distinguish limited reading/RSS from other uses. [PR Newswire terms](https://www.prnewswire.com/terms-of-use/) restrict systematic extraction, redistribution and software/AI development uses absent applicable permission. A release on an issuer site does not automatically inherit a different host's permission.

### Purpose matrix

`E` = existing metadata acquisition configuration, unchanged; `P` = pending,
not newly approved; `U` = material terms or route evidence unverified. These are
project states, not legal conclusions about the documents.

| Route | Metadata acquisition | Body retention | Local analysis | Review display | Model processing | Downstream distribution |
| --- | --- | --- | --- | --- | --- | --- |
| SEC feed / daily index / submissions | E | P | P | P | P | P |
| FDA oncology notifications | E | P | P | P | P | P |
| FDA MedWatch | E; feed disabled | P | P | P | P | P |
| ClinicalTrials.gov | E; substantive terms U | P/U | P/U | P/U | P/U | P/U |
| Drugs@FDA additional routes | P/U | P/U | P/U | P/U | P/U | P/U |
| Beam single-issuer RSS/archive | P; XML endpoint U | P | P | P | P | P |
| BWXT issuer release routes | P | P | P | P | P | P |
| Business Wire / PR Newswire | P | P | P | P | P | P |

The ClinicalTrials.gov terms/API pages returned only site-shell text; substantive
terms were not verified. A Drugs@FDA documentation fetch timed out. A SEC
website-dissemination URL attempted during research was unavailable; the FAQ's
reuse statement was successfully read. These gaps must not be replaced by
assumed permissions or by changing the four existing policy snapshots.

## 4. Concrete remaining designs and adversarial cases

### Continuous interval and later correction handling

Keep three distinct coverage objects: feed interval observation; acquired
index-record set; correction/retraction surveillance. A prospective receipt needs
source policy hash, route/query, requested interval, collection start/end,
page/continuation receipts, raw metadata hashes, item identities/counts, source
timestamp precision, status and every gap. Bounded backfill may recover records
while leaving historical real-time latency and continuous-coverage status unknown.

A future weekly correction pass should compare reviewed full/quarterly index
snapshots under a separately approved `/Archives/edgar/full-index/` policy.
Compare accession plus registrants/form/date, not ticker or headline. Additions,
changes and missing entries become discrepancy observations. Missing entries need
an authoritative action or confirmed source correction before an adjudicated
retraction. An amended filing normally has its own native accession and must not
be merged into an original solely from `/A` wording or the same issuer/date.

For outage repair, keep the old watermark until a complete head traversal closes
the interval under the frozen rules. Reconciliation has its own cursor and
receipts. Re-fetches are capped and budgeted. A content refresh is explicit and
needs retained-body/local-analysis permission; it creates immutable evidence
revisions. Never substitute the repair observation time for original availability.

**Future correction gate:** bind a durable correction record to old/new evidence
revisions and affected event revisions. `review.event_packet`, `record_review`
and `build_focus` must consult it before display; a completed hydration job must
not clear it. The independent reviewer reproduced why a queue-status-only design
fails: cached listing plus parsed jobs changed `metadata_changed_review_required`
back to `parsed` with zero fetches and no review. Removing the listing instead
caused another request. Preserve both work history and a separately adjudicated
correction state. The current patch deliberately does not claim this gate exists.

### Explicit origin equivalence

The current evidence constructor accepts an operator-provided `origin_reference`;
same-filing accession grouping is deterministic, while generic cross-publisher
proof remains manual. The [proposed contracts](proposed-source-contracts.json)
define a relation record with member native IDs/revisions, reporting parties,
source-supported link/identifier proofs, relation type, actual observation and
source precision, supersession link and pending review.

Require an explicit issuer-to-wire native-ID link, filing exhibit attribution,
or equivalent source-supported identity. Same headline, similar wording, date,
ticker, related-company tags or common website operator are insufficient.
An issuer release, its wire copy and the embedded filing exhibit are one
assertion origin with multiple evidence paths. A regulator-hosted issuer letter
remains the issuer's submission; a regulator's actual decision is a different
reporting role. Independence must describe the underlying evidence, not votes
from website domains. Transitive copy chains count once.

Keep `same_release`, `republishes`, `independently_reports`, `corrects` and
`retracts` distinct. Correction/retraction proof must identify the affected
revision, not erase history. Changed numerical/subject/stage facts require new
adjudication. Generic cross-origin conflict resolution remains manual.

The A ledger supplies grounded examples: the BWXT release explicitly links its
Business Wire identifier; the GM release links its PR Newswire copy; EyePoint's
PAVIA release has a filing exhibit. No feed membership or extra corroboration
count was inferred from those research links.

### FDA and one-issuer route boundaries

`parse_notifications` defaults to 200 rows, permits at most 1,000, records a cap
gap, uses a named table, retains date-only values with `published=null`, and
returns zero validated claims. Missing tables, links, unapproved routes or dates
remain gaps. MedWatch safety alerts, Drugs@FDA application/label actions, biologic
routes, devices, recalls and oncology press notifications must have separate
route and event-family contracts. Do not reuse the oncology denominator for them.

Before configuring Beam's candidate feed, an operator must verify the actual XML
link, canonical HTTPS route, GUID stability, timestamp/timezone, retention window,
maximum entries, error/redirect behavior and purpose rights. Proposed initial
scope is one reviewed issuer feed with the existing small per-call budget;
cadence and daily request allowance need a real operating decision. This document
does not install a schedule or add the issuer to any cohort.

### Complex amounts

The proposed amount atom carries exact currency, decimal string, comparison
operator, role, contract/financing identity, period, contingency, additivity and
revision-bound support for every field. It distinguishes ceiling, included or
incremental options, current obligation, financing authority, gross proceeds and
net proceeds. A bound, range or approximation cannot become an exact scalar.
Unknown currencies and periods remain null; do not convert currencies, add
nonadditive amounts, annualize, or infer revenue, received cash or runway.

`fixtures/omni/coverage_rules.json` contains **12 executed amount cases** plus
**six design-only origin/lifecycle scenarios**. The existing amount extractor
still admits only the simple exact USD control. Multiple currencies, ceiling
plus options, obligation versus ceiling, annual amounts, contingencies,
approximations, bare dollars, unsupported scale abbreviations, periods, bounds
and financing authority abstain. The proposed richer contract is not wired into
runtime extraction.

## 5. Acceptance checks and unresolved inputs

Actually run: 40 focused reconciliation/acquisition/event tests passed after the
three fixes; the added amount fixture test then passed all 12 parameter cases.
Final combined and dual-Python release receipts accompany the delivery.
These were mocked/offline checks, not live-provider acquisition or legal review.

Acceptance for the applied patch is fresh co-registrants retained; feed cursor,
publication clock and cached jobs preserved; immutable before/after receipt;
no duplicate change for identical metadata; route rejection before DNS/budget;
and persisted coverage debt for the demonstrated SEC pacing failure.

Acceptance for future work is stricter: bounded outage repair without false
interval closure; correction quarantine survives cached hydration and invalidates
old approvals; copy-chain deduplication with exact provenance; cap/date/route gaps
remain visible; every active purpose has a real versioned operator decision; and
all complex amount fields either have exact scope/support or abstain. These are
criteria, not passed tests for unimplemented designs.

Unresolved inputs include authoritative retraction/discrepancy adjudication,
full/quarterly-index route approval, canonical MedWatch/issuer feed verification,
ClinicalTrials.gov substantive terms, product-specific FDA route design,
source-purpose sign-off and human review of common-origin relations.

## 6. Next bounded task

Package C freezes a prospective pilot protocol and independently reviews evaluator
logic using minimal counterexamples. It will produce templates rather than
fabricated sessions, gold labels, coverage attestations, cost logs or approvals.
