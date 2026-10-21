# Operating review for OMNI-12

Prepared 2026-10-19. **Draft pending human review.** No rights approval, counsel
opinion, customer feedback, revenue, observed cost or authorization to distribute
is supplied by this document.

## Field-level rights inventory

The unit of approval is a field, source revision, purpose and audience. A hostname
allowlist or a single whole-product checkbox cannot replace this inventory.
The six runtime purposes are defined in `omni/evidence.py`; current source
policies remain unchanged. The source-specific evidence is in
`source-policy-register.json` and `vertical-source-ledger.json`.

| Field family | Provenance to retain | Review before use |
| --- | --- | --- |
| Native ID, source URL, filing/trial/project identifier | Source policy, exact source row and observed time | Approved acquisition route and permitted metadata retention; review display/model/commercial purposes separately |
| Headline, release title, abstract or excerpt | Author/reporting party, original URL, revision and exact text span | Source-specific storage, excerpt length, audience and redistribution terms |
| Full HTML, PDF text, tables or images | Document revision, content-completeness proof and source/rightsholder | Explicit storage, analysis, review-display and model-processing decisions; third-party exceptions |
| Qualitative claims and numeric results | Exact evidence spans, scope, validator version and AI-pending label | Permitted derivation and display; human review of every material claim |
| Plant, trial, issuer and ownership mappings | Native identifiers, relationship role, validity/known-from time and row proof | Source provenance plus human domain adjudication; uncertain parent/stake stays unknown |
| Origin equivalence and corroboration | Independently reviewed source-chain references | No automatic independence from different hosts or vendor names |
| Source dates and observation times | Raw value, precision, timezone basis and collector receipt | No inferred midnight or generated-at substitute; disclose incomparable times |
| Coverage and health status | Frozen query, source interval, result cap, backlog and request receipts | Define denominator and limits; exclude credentials, private hostnames and personal contacts |
| Human review and correction record | Reviewer identity/role, exact input hash, decision time and superseded record | Permitted audience and retention for reviewer information; keep internal identifiers private |
| Customer feedback or quotations | Consented interview record and approved quotation scope | Separate consent and data retention; no fabricated interviews or implied endorsement |
| Costs, time and vendor invoices | Matched period, allocation basis and evidence reference | Access controls and approved redacted summary; no raw invoice/account redistribution |

For each approved field create a versioned decision with: field path, source-policy
fingerprint, source author/rightsholder, purpose, audience, permitted transformation,
retention/deletion rule, attribution requirement, effective dates, reviewer, evidence
reference and revocation procedure. Required values are presently **pending**.
Secrets and raw request credentials have no publication purpose.

Government-data notices are useful evidence, not a substitute for review of an
issuer attachment, licensed image, data service restriction or the actual offering.
Source details and failed policy retrievals are recorded in the ledgers. No
unverified terms have been converted into runtime approval.

## Correction and disclosure policy

Every displayed item should show what changed, its direct subject/asset, what the
source establishes, the reporting party, material uncertainty, the exact source,
source-time precision, first observation and review status. Label issuer-reported
outcomes as such. Do not use a live status color to imply that coverage is complete.

Give the declared coverage scope and operating interval, last successful receipt,
backlog/caps, unavailable sources and known publication lags. State that broad
metadata discovery does not mean every document has been acquired or reviewed.
NRC-derived EIA records are one lineage. Historical replay and prospective records
must remain visibly separate.

When a source or mapping changes:

1. Append the new source metadata/content revision and a correction record linking
   the prior and current evidence hashes, scope and reason.
2. Mark dependent current claims and review decisions as requiring re-review.
   Retain the earlier displayed version in the permitted audit history with a
   clear supersession link.
3. Remove affected items from the current approved output until exact replacement
   evidence and review are complete. A cached document must not clear quarantine.
4. Require a new human decision bound to the corrected packet hash. Keep false
   positives, source withdrawals and unknown outcomes visible in evaluation.
5. If external distribution is later authorized, create a correction entry in the
   same permitted channel and audience. Outbound corrections still require the
   authorized publishing workflow.
6. If retention rights are revoked, apply the reviewed source-specific retention
   rule. Immutable audit design is not permission to retain prohibited text;
   preserve only permitted identifiers, hashes, decisions and deletion receipts.

Steps 2–4 depend on Package B's proposed correction lifecycle. Current metadata
change receipts alone do not implement claim invalidation. No external correction
or publication occurred during this work.

## Feedback interview protocol

**Status: zero interviews and zero customer claims.** The protocol is ready for an
owner to review; it is not authorization to contact anyone.

Recruit a small, explicitly selected mix of current workflow users, infrequent
users and people who did not find a brief useful. Record selection and incentive
bias. Obtain consent for notes, any recording, retention and quotations. Use only
approved or synthetic briefs. Freeze the task prompts before showing outputs.

For each session:

1. Ask how the participant currently finds and verifies a specific material event,
   including a recent example and the cost of a missed or misleading item.
2. Give a defined verification task with a reviewed brief. Observe whether the user
   identifies the right company, trial/project, stage and source limitations.
3. Ask the user to open the underlying evidence and explain which parts are facts,
   issuer claims, estimates or unknown. Record incorrect confidence and where
   context or source access was missing.
4. Ask what the user would remove, which missing event mattered, and what other
   source or workflow they would still need. Include a negative-result and an
   abstention case.
5. Ask for a concrete follow-up workflow or repeat-use choice. Treat stated
   willingness to pay as an interview response, not revenue or validated demand.

Measure task completion, verification errors, source-open success, observed task
time and repeat use over a specified interval. A time-saving comparison needs
matched tasks and a stated baseline; a user's impression is a different measure.
Retain disagreements and adverse feedback. Do not turn a convenience sample into
a population estimate or use simulated investment returns as customer value.

Blank interview record: interview_id, consent_ref, recruitment_basis, task_version,
brief_packet_hash, prior_workflow, task_start/end, verification_result, observed
errors, participant_feedback, quote_permission, followup_choice, analyst_notes,
human_review_status. All are unpopulated until a real authorized interview.

## Operational runbook

### Before the first authorized run

Pin code, lockfiles, registries, policies, query scope, classifier/validator versions,
calendar/timezone and the pilot manifest. Verify source permissions and exact
credentials through the approved local mechanism. Keep credentials out of logs and
responses. Complete the required offline checks and a source-specific canary only
after authorization. Record missing coverage instead of weakening a check.

The operator must confirm receipt authentication and the prospective-gold,
latency and untouched-test importers before using them as activation evidence.
Installing or running a scheduler is separate operator work.

### Each authorized session

- Confirm the session from the frozen calendar and timezone. Read the actual
  clock/source health receipts and outstanding coverage debt.
- Run only the approved scope and budget. Capture counts of metadata/document
  attempts, retry/backoff, successful objects, unresolved candidates and backlog.
- Hydrate only permitted documents. Validate exact revisions, supported scopes,
  rights and source timing before creating review packets.
- Collect the independently sampled source inventory and human adjudications
  defined in Package C. Preserve misses, abstentions and unresolved events.
- Generate any permitted review brief with explicit pending/approved labels and
  current correction state. Distribution uses its separate approval.
- Append cost/time evidence and the session/run receipts. Reconcile counts and
  affected versions; never count a retry, duplicate origin or replay as a new case.

### Incident and recovery actions

| Observed incident | Required action |
| --- | --- |
| 429/503, quota limit or gate outage | Persist retry time/coverage debt, defer the candidate and stop that source as required |
| Empty/truncated response or metadata available before document | Keep hydration pending; do not mark the source interval complete |
| Wrong issuer/trial/project, false approval or invented human approval | Stop the affected output route, preserve the incident, quarantine dependent claims and obtain human review |
| Changed source or stale mapping | Append revision and correction records, then revalidate and re-review current claims |
| Rights revoked or secret exposure | Stop the affected purpose/route, apply the approved incident and retention procedure, preserve a redacted incident receipt |
| Scheduler/clock/receipt failure | Mark the session ineligible until its required evidence exists; do not backfill a fictional successful run |
| Cost or review backlog exceeds the reviewed limit | Pause additions and the affected source; record the shortfall and keep existing scope visible |

Rollback disables only the affected adapter/purpose or versions editorial membership.
Keep unrelated histories intact. A rollback cannot erase evidence of a failed
prospective session or recover deleted source rights. Resume only after the
specific failure, new version and required re-review are recorded.

## Economics worksheet

The companion `omni-operating-economics.xlsx` contains two worksheets:

- **Economics:** editable planning inputs and a visible cost build. Ten sessions,
  ten scheduled review slots per session, eight minutes per initial review,
  a 25% second-review planning allocation, eight minutes per second review and
  twenty operator minutes per session are explicit illustrative assumptions.
  This describes capacity, not measured event yield or a pilot sampling rule.
  Package C's actual independent-review protocol controls the pilot.
- **Actuals:** blank run-cost inputs, reporting window, cost-completeness receipt
  and independently evidenced count of unique, useful, human-reviewed events.
  No source-derived price, time log, invoice or cost is supplied.

Unknown hourly rates, vendor fees, compute/storage costs and other direct costs
remain blank. Enter a numeric zero only when verified. Planning direct cost and
cost per scheduled review remain unavailable until all required prices exist.
Actual direct cost requires complete, nonduplicate in-window rows and a
cost-completeness receipt. Actual unit cost requires a positive event denominator
and its receipt; zero useful events does not mean zero unit cost.

The scope is direct operating cost for the selected window: reviewer labor,
operator labor, vendor fees, compute/storage and other explicit costs. Allocate
shared costs once using a documented basis. Record one-time setup in the appropriate
explicit cost category and identify it in the evidence reference; do not silently
mix recurring and one-time comparisons. There is no forecast of revenue, profit,
pricing, customer conversion or financial performance.

Spreadsheet receipt references are editable metadata, not authenticated pilot
evidence. This workbook does not feed `dependent_gates` automatically. Its final
verification record states the calculation engine used and the tested blank,
zero, duplicate, date-window and recalculation behaviors.
