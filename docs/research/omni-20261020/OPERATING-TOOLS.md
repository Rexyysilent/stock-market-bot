# OMNI operating tools: rights, reviewed briefs and measured costs

Engineering candidate, 2026-10-20. **AI/domain labels remain pending human review.**
These tools implement local evidence records, permission checks, reviewed previews,
exports and operating calculations. This document supplies no actual source grant,
human approval, customer interview, cost observation, prospective pilot or authority
to distribute. Readiness never enables collection, changes membership or activates
a service.

This is the implementation guide for Workstream 6. The earlier
[operating review](../omni-20261019/OPERATING-REVIEW.md) supplies the proposed
interview and operating definitions; its statement that correction invalidation
was still a proposal describes that earlier revision. The current local lifecycle
is described in [Workstream 1](WORKSTREAM-1.md). The executable contracts are
[rights](../../../omni/rights.py), [briefs](../../../omni/briefs.py),
[operating observations](../../../omni/operating.py),
[dependent gates](../../../omni/gates.py) and
[the CLI dispatcher](../../../omni/operating_cli.py).

## 1. Operator evidence and the authentication boundary

An immutable receipt establishes the exact recorded payload, its references and
its recorded observation time. Typed consumers also check the subject, purpose,
current implementation and policy, relevant source revisions, time ordering and
revocations. An imported envelope is not approved merely because its hash is
valid. See [receipts](../../../omni/receipts.py) and
[attestations](../../../omni/attestations.py).

The software does **not** authenticate a reviewer, verify a legal rightsholder,
validate a signature, contact an external evidence holder, establish an invoice's
authenticity or prove that a customer and consent exist. A reviewer name and an
external reference/hash are structured operator assertions. The operator must
verify the underlying evidence through an appropriate real process before
recording it as `operator_evidence`. Changing a synthetic record's label does not
turn the exercise into evidence of a real action.

`record_review` records an explicit approve/reject decision against the exact event
revision and current evidence state. It cannot override mandatory source,
identity, scope or freshness failures. The supplied reviewer string is not a login
or authenticated identity. Briefs expose both `recorded_review: approved` and
`human_authentication: not_proved_by_software`, while preserving
`ai_domain_labels: pending_human_review`. See [review](../../../omni/review.py).

The SQLite store and trusted internal APIs retain permitted evidence, which can
include source bodies. Rights filtering applies to the public CLI views and safe
brief/export functions described here; it is not database encryption, operating
system access control or a sandbox around arbitrary Python/SQL access. Keep the
database, operator evidence, policies and generated artifacts in the operator's
private local workspace, outside tracked repository data.

## 2. Exact field, source, purpose and audience rights

Source-purpose policy and field rights are separate checks. The existing source
policy controls acquisition, storage, analysis and permitted uses of an exact
route. It remains necessary even after a field grant is recorded. The default
policies do not become approved through installation or use of this guide.

Each `omni-field-rights-1` record names exactly:

- `source_ref`: `{source_id, native_id, revision_id}` resolving to a retained
  document; an inventory row alone is insufficient.
- One `field`, one `purpose`, one `audience`, and `decision: approved` or `denied`.
- `rightsholder`, `attribution`, `retention_rule` and an allowed transformation.
- The current full `policy_hash` and `implementation_hash`.
- A bounded `effective_at`/`expires_at` interval and an `attestation_ref` approving
  the exact record body, with matching policy, implementation and source binding.
- `evidence_kind: operator_evidence`, or explicitly `synthetic` for an exercise.

The four field purposes are `review_display`, `local_export`,
`commercial_display` and `model_processing`. Local brief tools support the first
two. `local_export` requires its own field grant and the source policy's baseline
`review_display` permission. It does not authorize commercial display, uploading,
model processing or any external distribution. A grant does not propagate to
another field, audience, purpose, document or corrected revision.

| Exact field | Required transformation | Implemented projection |
| --- | --- | --- |
| `source.url`, `source.title`, `source.native_id`, `source.times` | `canonical_metadata` | Separately permitted source metadata; source time remains distinct from observation time |
| `source.text` | `exact_excerpt` or `full_text` | Packet text bounded by explicit integer `max_characters` from 0 to 500000 |
| `claim.value`, `claim.scope` | `derived_summary` | Validated semantic claim and its scope |
| `claim.numeric` | `derived_summary` | Scoped clinical numeric claims/context, with proof text removed |
| `claim.amount` | `derived_summary` | Scoped amount claims/atoms, with proof text removed |
| `review.identity` | `canonical_metadata` | Optional recorded reviewer identity |
| `review.decision`, `coverage`, `correction.history` | `sanitized_history` | Review state, coverage disclosure and correction references |

The required fields for a brief card are `source.url`, `source.times`,
`claim.value`, `claim.scope`, `review.decision` and `coverage`. A corrected card
also needs `correction.history`. Each must be allowed for **every** supporting
result document and every separate clinical definition document. Missing a required
field withholds the card. Missing optional numeric, amount, reviewer or title rights
withholds those projections; a card with no remaining permitted claims is withheld.

A protocol or endpoint-definition document can be historical without being treated
as a stale result announcement. It still has its own exact evidence dependency,
current source policy and field rights. A result document's grant cannot stand in
for the definition document's grant. See
[clinical dependency integration](../../../test_omni_integration_scope.py).

An effective denial blocks the matching projection even if a later approval exists.
It must expire or receive a separate revocation before it ceases to participate.
Revoked, expired, future, mismatched or synthetic-without-opt-in records do not
authorize use. If several valid text grants match, the smallest character limit
applies. Policy or implementation changes require revalidation and appropriate
new evidence; editing recorded fingerprints to conceal the change is not a valid
migration. `retention_rule` records the reviewed obligation; it does not implement
automatic deletion or establish a lawful retention period.

## 3. Safe views, claim text and local files

`preview_brief` starts from currently eligible reviewed events and emits a permitted
projection. Claim values, scope and attribution remain distinct from verbatim
support. The clinical and amount serializers remove proof spans and their text;
numeric/amount permission never implicitly grants `source.text`.

The CLI `packet` command uses `safe_packet`, which returns separately filtered
document fields and current-state reasons. It does not dump raw event JSON, claim
support objects or unfiltered source bodies. Text appears only under an explicit
`review_display` text grant and its character limit. The `permitted_claims` helper
similarly projects semantic claims, bounded support references and permissioned
numeric/amount fields without verbatim proof text. It is distinct from the raw
internal `event_packet` API.

`history-search` searches event/revision identifiers, not source-body full text.
It requires `correction.history`, `review.decision` and `source.times` rights for
all supporting documents. It returns sanitized revision/correction/review records;
the `history EVENT_ID` command uses the same filtering. Queries are bounded to
250 characters and result limits to 1–1000.

Before export, `validate_brief` checks current source/review state, versions,
purpose, audience and every retained grant. It recomputes the **whole** safe
projection, including cards, packet bindings, withheld fields, rights inventory
and disclosure fields. Injecting raw text into a top-level or withheld field,
omitting a rights reference, or changing a card cannot be authorized by importing
a newly hashed receipt. The generation clock must match the immutable receipt.

`export_brief` accepts only a retained `local_export` brief and a new `.json`,
`.md` or `.html` path. It refuses to overwrite an existing file. Source-controlled
Markdown punctuation and HTML are escaped; the HTML renderer displays escaped
text rather than executing source markup. A `brief_export` receipt binds the
brief, format, content hash and byte count. Every export still states
`external_distribution: not_authorized`, `signal_eligible: false` and
`automatic_activation: false`.

Validation occurs on each requested operation; no background process recalls an
already written file. Changed rights, policy, evidence, reviews or the permitted
candidate set can require a fresh preview. Old files remain historical artifacts
and must not be presented as newly validated output.

## 4. Actual CLI and API entry points

Run commands from the checkout with the locked Python 3.11 or 3.12 environment.
Global arguments precede the subcommand:

```sh
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json plan
```

Paths, IDs, hashes and receipt references below are operator-supplied placeholders.
`--sources` loads a policy configuration, not a rights approval. An optional global
`--clinical-catalog PATH` loads an explicitly reviewed clinical scope catalog.
There is no general CLI `--registry` or `--now` option: the main CLI uses current
UTC, while source publication, observation, use and reporting-window clocks remain
separate evidence fields. The default registry is the repository registry.

For each row below, append the command to the same `python -m omni --db ...
--sources ...` prefix. JSON import files are bounded to 5 MB.

| Command and arguments | Effect |
| --- | --- |
| `rights-import INPUT [--synthetic]` | Validate and append one exact field-rights record |
| `rights-revoke RIGHTS_REF --reason TEXT` | Append a revocation; preserve the old record |
| `brief-preview --scope-hash HASH [--purpose review_display\|local_export] [--audience local_operator] [--synthetic] [--persist]` | Produce safe current cards; `--persist` retains a `reviewed_brief` receipt |
| `brief-export BRIEF_REF DESTINATION [--audience local_operator] [--synthetic]` | Revalidate and write a new local JSON/Markdown/HTML file |
| `history-search [--query TEXT] [--limit 100] [--audience local_operator] [--synthetic]` | Inspect permitted identifier/revision history |
| `operating-import INPUT [--synthetic]` | Validate and append one scoped operating observation |
| `economics-report --scope-hash HASH --window-start TIMESTAMP --window-end TIMESTAMP [--synthetic] [--persist]` | Derive a closed-window report; optionally retain its receipt |
| `readiness-context PROTOCOL_ID CANDIDATES_JSON [--operating-window-start TIMESTAMP --operating-window-end TIMESTAMP]` | Derive exact context from a real retained protocol and a JSON candidate list |
| `readiness-report INPUT [--persist]` | Check typed dependencies in `{"evidence": {...}, "candidates": [...]}`; optionally retain the result |

`--synthetic` explicitly permits fictional fixtures in the relevant local command;
it does not create real gate credit. `--persist` writes a local immutable receipt,
not a publication. Import an independently prepared attestation envelope with
`receipt-import INPUT` before importing records that reference it. The generic
receipt importer preserves the envelope; consuming functions still enforce the
exact typed contract.

The Python interfaces allow an explicitly supplied registry, policy set and clock:

| Module | Public functions used by the workflow |
| --- | --- |
| `omni.rights` | `record_rights`, `validate_rights`, `field_decision`, `revoke_rights` |
| `omni.briefs` | `preview_brief`, `validate_brief`, `export_brief`, `safe_packet`, `search_history`, `permitted_claims` |
| `omni.operating` | `record_observation`, `validate_record`, `cost_components`, `economics_report`, `record_economics_report` |
| `omni.gates` | `context_request`, `dependent_gates`, `record_gate_report` |
| `omni.review` | `record_review`; raw internal `event_packet` for trusted integration only |

All functions operate on an explicit local store. API clocks support deterministic
tests; supplying a past clock does not prove a historical observation or authorize
backdated evidence. See [main CLI](../../../omni/__main__.py) for supporting
`plan`, `status`, `operations`, `queue`, `packet`, `review`, `ingest` and backup
commands. `brief --mode off|shadow|reviewed` remains the compatibility view; use
`brief-preview` when an exact operating scope or local-export purpose is required.

## 5. Operating observations and workbook reconciliation

`omni-operating-observation-1` accepts `cost`, `run_cost`, `review_effort`,
`useful_brief`, `customer_feedback`, `cost_completeness` and
`useful_event_census`. Actual records bind an immutable observation ID, exact
scope, current implementation/policy, closed reporting interval, observation
clock, external evidence reference/hash, retained dependencies and exact approval
attestation. Differing content cannot reuse an observation ID. `template` and
`assumption` rows can be retained but cannot establish actual measurements.

For `run_cost`, all seven decimal-string inputs are explicit:
`review_minutes`, `review_hourly_rate`, `operator_minutes`,
`operator_hourly_rate`, `vendor_amount`, `compute_storage_amount` and
`other_amount`. Also supply currency, stable run/allocation ID, allocation basis
and exactly one of `incurred_at` or `incurred_date`. A timestamp must fall inside
the reporting interval. A date-only row requires `cost_timezone: UTC` and its
entire calendar day inside the interval; no midnight availability is invented.

The calculation matches the companion operating workbook's direct-cost build:

```text
reviewer labor = review minutes × review hourly rate / 60
operator labor = operator minutes × operator hourly rate / 60
direct cost = reviewer labor + operator labor + vendor + compute/storage + other
unit cost = direct cost / unique useful reviewed event count
```

Labor minutes/rates are nonnegative. Vendor, compute/storage and other monetary
amounts may contain evidenced signed credits. Arithmetic uses decimal values;
there is no implicit currency conversion or invented invoice rounding. Mixed
currencies cannot produce a single cost total. Blank workbook inputs and absent
JSON values mean unknown; only an explicit evidenced zero means zero.

The offline demonstration uses this **fictional** complete row:

| Component | Synthetic input | Calculated amount |
| --- | --- | ---: |
| Reviewer labor | 30 minutes at 120 per hour | 60 |
| Operator labor | 15 minutes at 80 per hour | 20 |
| Vendor | Explicit amount | 10 |
| Compute/storage | Explicit amount | 4 |
| Other | Explicit signed credit | -8 |
| Direct cost | Sum of five categories, USD | 86 |
| Useful event denominator | Two distinct supported event units | 2 |
| Cost per useful event | 86 / 2 | 43 |

Its allocated labor is 0.75 hours; separate evidenced clock intervals total
2700 seconds. The example verifies the formula and signed-credit treatment; it
does not fill the workbook's blank Actuals sheet or establish any incurred cost.
Spreadsheet cells and editable receipt-reference cells are not automatically
imported into the store or accepted by readiness gates. Planning scenarios stay
separate from actual observations.

A complete report needs all five direct-cost categories, exactly one matching
cost-completeness census covering the exact cost receipt inventory, and exactly
one useful-event census covering the exact assessment inventory. Repeated source
evidence hashes or repeated run/allocation IDs block cost completeness. Explicit
category zeros still need evidence. With missing or invalid inputs, the report
returns null measurements and reasons rather than substituting zero.

Usefulness binds a retained reviewed brief, distinct event IDs and an explicit
`assessed_at` inside the reporting window. The brief must have existed before that
assessment. The countable unit is revision-insensitive assertion origin, event
family and scope: a copied release or revision is not extra yield. `false`, `null`
and conflicting assessments remain negative, unknown or contested. Zero useful
events gives no finite unit cost. A review decision alone supplies no usefulness
assessment.

Feedback requires a prior consent attestation for the exact pseudonymous
participant, task, brief and structured-retention purpose. The brief must exist
before the session, which must lie inside the reporting interval. Results preserve
`useful`, `not_useful`, `mixed` and `unknown`. The importer rejects `quotation`,
`participant_name` and `email`; this is a bounded structured record, not contact,
quotation publication or authentication of consent. Do not place personal data in
other free-text fields to bypass that boundary.

## 6. Reproducible offline synthetic demonstration

```sh
python scripts/omni_synthetic_demo.py --output /tmp/new-omni-demo
```

The output directory must not exist and must be outside the checkout. The API is
`scripts.omni_synthetic_demo.run_demo(output_directory)`. The
[script](../../../scripts/omni_synthetic_demo.py) uses fictional `.example`
sources, its own synthetic registry/policy, explicit synthetic attestations and
fixed 2026-10-18 clocks. It performs no network request, changes no default policy
and leaves its SQLite database only in the selected output directory.

Inspect these generated artifacts in order:

1. `rights-withheld-baseline.json`: both otherwise reviewed fictional cards are
   withheld until exact field grants exist.
2. `reviewed-brief.json`, `.md` and `.html`: permitted local cards and attribution
   after synthetic grants, with pending AI labels and no distribution authority.
3. `cost-formula.json` and `economics.json`: the 86 / 2 = 43 example, explicit
   completeness/census receipts and one preserved `not_useful` feedback record
   with prior synthetic consent. `measured_actuals` remains false.
4. `correction-withheld.json`, `stale-export-refusal.json` and
   `economics-after-correction.json`: a corrected source invalidates the old
   review/export and makes the affected historical usefulness evidence pending.
5. `corrected-brief.json`, `.md` and `.html`: a fresh synthetic review and rights
   for the corrected revision restore the permitted current projection, including
   its changed outcome and correction references.
6. `readiness.json`: OMNI-10/11/12 stay pending. `receipts.json`,
   `demo-manifest.json` and `artifacts.sha256` retain resolvable references and
   artifact integrity information; `events.sqlite3` retains the local exercise.

The initial economics file is a pre-correction snapshot. The later report retains
the evidenced synthetic cost but cannot reuse an invalidated denominator. Fresh
review does not manufacture use in an earlier reporting window. All programmatic
reviews and approvals in this demonstration are explicitly fictional; the example
proves neither a prospective run nor actual user value. The bounded subprocess
regression is [test_omni_demo.py](../../../test_omni_demo.py).

## 7. Local correction, re-review and export incident flow

When a source, definition, mapping or right changes, stop using the affected
current output and preserve its prior evidence and receipt IDs. Back up before an
operator migration or incident recovery:

```sh
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json store-backup /operator/omni/incident-backup.sqlite3
```

Append the authorized new source observation/revision with exact native identity,
source time and actual observation time. `source-observation-import INPUT` records
an inventory observation; it cannot substitute for a corrected body. `ingest`
accepts a permitted retained document and reanalyzes it. Imported evidence is not a
past successful live poll. For example:

```sh
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json ingest /operator/omni/corrected-document.json --source SOURCE_ID
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json queue --limit 25
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json packet EVENT_ID NEW_REVISION_ID
```

The lifecycle appends source-change evidence, updates the current state and reopens
affected review work. Current views withhold stale or quarantined claims. A cached
hydration result, replayed old body or old clinical catalog cannot clear the change.
Historical definition corrections invalidate dependent current review even though
definition documents do not use result-announcement recency. An absent index row
is a discrepancy requiring investigation, not an inferred withdrawal.

For a rights incident, append `rights-revoke RIGHTS_REF --reason TEXT` and apply
the separately reviewed retention/incident procedure. Re-review only after the
exact current result and definition evidence passes mandatory validation. Import
new grants for any changed source revision; old grants do not transfer. Record the
real review, persist a new permitted brief and export to a new filename:

```sh
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json review EVENT_ID NEW_REVISION_ID --reviewer REVIEWER_ID --decision approve --note "Reviewed the current evidence and correction references."
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json brief-preview --scope-hash SCOPE_HASH --purpose local_export --persist
python -m omni --db /operator/omni/events.sqlite3 --sources /operator/omni/sources.json brief-export NEW_BRIEF_REF /operator/omni/corrected-brief.json
```

Use the same `--clinical-catalog PATH` across operations when the event depends on
an explicit catalog. Record a rejection when appropriate; the example approval is
an available action, not an instruction to approve an unresolved packet. Re-run
economics and dependent gates when their source/review dependencies changed.

### Retrospective limits

Correction handling starts when the operator's process discovers and retains the
change. These tools do not continuously monitor every source, infer a retraction
from missing metadata, or recover original source availability from a later fetch.
Backfill does not repair a missed prospective session or establish its timeliness.

Operating use is checked at its evidenced use time so ordinary aging and later
unrelated cards do not erase the historical observation. Current source, review,
registry or implementation changes can nevertheless invalidate its dependent
brief and remove it from a current derived report. This is conservative current
validation, not a complete reconstruction of everything known at the old time.
Rights that were valid at historical use are not automatically reinterpreted as a
new current permission. Existing exports and immutable reports are not rewritten
or recalled; retain them with the later correction/refusal record and regenerate
the current report. No external correction message or deletion workflow is sent
by these commands.

## 8. First authorized run checklist and readiness

1. **Pin the run.** Use the reviewed commit and locked environment; retain the
   implementation, registry, policies, optional clinical catalog, query scope,
   timezone and reporting interval. Run the repository release checks required by
   `AGENTS.md`. Keep real receipts and artifacts outside tracked fixtures.
2. **Verify actual source authority.** Obtain evidence for each route and required
   acquisition/storage/analysis/display use. Confirm the real local credentials
   through the approved mechanism. Leave unknown purposes disabled. Review field
   rights separately; a public URL or government source name is insufficient.
3. **Inspect the plan and existing state.** Use `plan`, `status` and `operations`.
   Examine gaps, caps, budgets, backlogs and source clocks. A dry command or an
   imported inventory establishes no successful live cycle. Execute only the
   source scope already authorized; this guide installs no poller or scheduler.
4. **Establish the measurement context first.** If the run is intended to support
   readiness, use the retained prospective protocol and candidate inventory with
   `readiness-context`. Supply both operating-window flags when that closed window
   differs from the prospective window; it must contain the prospective interval
   and start no earlier than protocol freeze. Use the returned exact scope for
   briefs, attestations and observations. A standalone scoped brief may be useful
   locally, but an arbitrary scope hash is insufficient for a readiness gate.
5. **Acquire and review exact evidence.** Preserve real source/observation clocks,
   result and definition dependencies, missingness and origin relationships.
   Inspect rights-filtered packets; record explicit decisions bound to the current
   state. Keep AI labels pending human review and failures visible.
6. **Approve only evidenced projections.** Import verified attestations and exact
   per-document field grants for the intended purpose/audience, including separate
   clinical definition documents. Confirm required-card withholding before grants
   and optional text/claim withholding afterwards. Retain grants and revocations.
7. **Persist and inspect a permitted brief.** Use `brief-preview --persist`, then
   `brief-export` only for a permitted local-export receipt and new path. Confirm
   attribution, source times, correction references and coverage limitations.
   Neither successful export nor a nonempty card list authorizes distribution.
8. **Observe actual operating activity.** Obtain prior participant consent before
   any authorized feedback session. Record negative/unknown results as well as
   positive ones, exact use/session times, review effort, actual cost evidence,
   allocation basis and all categories. Do not replace blanks with assumed zeros
   or deduplicate away adverse observations.
9. **Close and reconcile the reporting window.** Import operating observations,
   the exact cost-completeness census and the exact useful-event census. Persist
   `economics-report`; investigate exclusions and pending reasons. Reconcile the
   workbook against the retained run-cost formula and event-unit denominator.
10. **Request human readiness review only with the required evidence.** Persist
    `readiness-report` against current versions and the exact protocol context.
    Resolve each missing/invalid dependency; never fill placeholders to obtain a
    green result. An output of `ready_for_human_review` is not activation.

OMNI-10 requires verified prospective evaluation, architecture/user-value review
and exact vertical source grants. OMNI-11 additionally requires measured expansion
for the exact candidate/protocol scope. OMNI-12 additionally checks actual useful
reviewed briefs, their exact rights inventory, a counsel attestation bound to that
inventory/context, the complete feedback inventory including adverse responses,
and aligned complete operating economics. Later proof cannot bypass an earlier
pending dependency. The gate recomputes current economics and rejects synthetic
or incomplete evidence; its review/authentication limitations remain explicit.

Installation and the offline demonstration supply none of those real-world
records. Until the operator supplies and reviews them, costs, customer value,
prospective quality and activation readiness remain unestablished.
