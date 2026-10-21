# Workstream 5: measured expansion tooling

Prepared 2026-10-20. **Engineering candidate; all AI labels remain pending human review.**
The executable examples and verification records are synthetic. They establish
no actual pilot accuracy, cost, useful-event yield, source permission or membership
decision. No live collection, membership change or external distribution occurred.

## Outcome and boundaries

`omni/expansion.py` implements an immutable proposal, detailed measurement import
and derived comparison report. `omni/expansion_cli.py` exposes local commands and
registration hooks for the main CLI. The workflow accepts an explicit operator
candidate list; it contains no production expansion list or membership writer.

The contract is `omni-expansion-1`. Its receipt kinds are `expansion_proposal`,
`expansion_observation` and `expansion_report`, stored using Workstream 1's
append-only receipt service. Existing receipt interpretation is unchanged; no
new database table or destructive migration is required.

Every proposal/report has `automatic_membership_change=false`,
`automatic_activation=false`, `signal_eligible=false` and
`human_review_status="pending"`. Even a complete actual report can become only
`eligible_for_expansion_review`. A separate authorized membership decision would
still be required, and this module has no operation that performs one.

## Requirement-to-implementation map

| Requirement | Implementation | Executable verification | Status |
| --- | --- | --- | --- |
| Explicit candidates; set difference against versioned current coverage | `proposal_request`, `create_proposal`, `_coverage` | Candidate duplicate removal, already covered names, unknown identities, no mutation and CLI proposal tests | Implemented; fixture verified |
| Bind scopes, identities, policy, future window and versions | Frozen WS3 protocol; instrument listing evidence and fingerprint; baseline/expanded scope hashes; dependency-lock, implementation, event-registry and policy hashes | Wrong baseline, post-hoc proposal, policy/coverage/instrument drift tests | Implemented; fixture verified |
| Source-first useful-event denominator | Current WS3 closed inventory/adjudications; typed `event_assessment` observations | Complete source inventory, unknown miss, negative miss, revoked adjudication, copied and bundled source cases | Implemented; fixture verified |
| Deduplicate candidate useful yield against baseline | Reviewed assertion origins, baseline/shared/unknown/candidate buckets, ambiguity exclusion | Duplicate usefulness fixture; copied release; correlated bundled events; shared origin exclusion | Implemented; fixture verified |
| Mapping errors, unknown/negative recall, abstention and backlog | `expansion_report` derives metrics from the exact current source adjudication and explicit assessment | E-01, E-02, E-04, E-05, E-10; mapping, incomplete inventory and missing assessment tests | Implemented; fixture verified |
| Requests and retries | Typed per-attempt observations, exact census, source-cycle hash, arm scope and actual attempt counter | Complete retry chain; duplicate request; missing telemetry; cross-arm reuse | Implemented importer/report; actual scoped telemetry absent |
| Review effort and aligned incremental cost | WS6 `validate_record`; complete category/cost inventory; `cost_components` when available; exact decimal arithmetic | E-01, E-03, E-06, E-07, E-08; missing and drift cases | Implemented; fixture verified; actual observations absent |
| Missing, unmatched, confounded, sparse and correlated inputs | Explicit reason codes, excluded references, missing unit IDs and null estimates | All eleven declared fixture scenarios plus drift, revocation and correlation tests | Implemented; fixture verified |
| Zero useful events have no finite unit cost | Cost difference and useful denominator checked separately | E-05 and revoked usefulness tests | Implemented; fixture verified |
| Typed Wave A/candidate review provenance | Exact purpose/subject/version-bound attestations revalidated for expiry and revocation | Truthy placeholders, wrong purpose, expired/revoked reviews, new evidence tests | Implemented; requires real operator reviews |
| Dependent gate revalidation | `validate_report` recomputes current actual report and rejects synthetic, stale or pending records | Persisted replay, forged ready status and synthetic report rejection | Implemented; actual gate remains pending |
| No activation or membership mutation | Read-only registry/config access; no collector or membership calls | Membership and instrument fingerprint invariance; local CLI plan | Implemented; fixture verified |

The exact test names, runtime output, dependency hashes and exit statuses are in
`expansion-test-receipt.json` and `expansion-python311.txt` /
`expansion-python312.txt`. Each final stage run passes 64 tests: 24 expansion
methods (including all eleven declared scenarios), 37 vertical methods (including
all twenty D-V fixtures), and three preserved GET transport/budget controls.
The final combined-candidate suite is a separate root
integration receipt; this stage's tests do not claim a combined suite pass.

The first staged secret scan identified public revision/file hashes, the local
execution path and an explicitly synthetic redaction canary as false positives.
Its complete failure output is retained in `expansion-secret-scan-initial.txt`.
Exact audit hashes and the execution directory now live in adjacent plain-text
manifests referenced by the JSON records; no hash evidence was dropped and no
scanner rule or baseline was changed. The source-contract commit pins remain
complete official GitHub document URLs. The redaction fixture uses an explicit
test canary and still verifies both raw and URL-encoded redaction.

The earlier Workstream 4 exit-zero secret-scan receipt ran before its new files
were tracked, so that scan did not cover those then-untracked files. It is not
proof of a staged Workstream 4 scan. The final staged scan and re-executed vertical
controls are recorded separately in this delivery.

## Proposal and scope model

`proposal_request` requires a current, already reviewed WS3 protocol whose window
has not started. Its captured editorial coverage must equal the caller's current
coverage snapshot. The standalone CLI obtains that snapshot from
`config.EDITORIAL_COVERAGE_TICKERS`; integration callers pass the actual current
configuration explicitly.

Candidate input is a JSON list. Duplicate strings are counted and removed for
set comparison. Already covered names remain visible under `already_covered`.
The additional candidates are the exact set difference, not a hardcoded list.
An empty result remains pending.

For every baseline/candidate symbol, the proposal records the dated instrument
resolution, listing evidence, instrument ID, issuer ID, registry fingerprint,
effective date and knowledge cutoff. A resolved instrument whose issuer is not
in the event registry remains unresolved for this workflow. Unknown or ambiguous
identities cannot be repaired by supplying a truthy review string.

The two comparison scope hashes include:

- The protocol ID and exact common observation window.
- The frozen source-scope definition hash, in addition to WS3's policy/source
  hash and event-registry fingerprint.
- The full implementation hash and dependency-lock hash.
- The dated instrument-registry fingerprint and the exact member sets.

At report time a current proposal is reproduced from these inputs. Imported
receipts cannot silently replace an identity mapping, membership set or scope
hash while retaining a valid-looking contract label. Version changes remain
visible as confounders and cannot produce an eligible current report.

## One source inventory and separate comparison arms

The comparison uses one frozen source-first WS3 inventory for both arms. Creating
two inventories from the same assertions would risk duplicating sample counts
and conflict with WS3's protection against event/origin reuse across protocols.
The common inventory includes misses and unknown issuers independently of what
the classifier emitted.

Current adjudicated units are assigned to baseline, incremental candidate,
shared, unknown issuer or other buckets. A candidate unit whose assertion origin
already appears in baseline/shared coverage is shared, so it cannot create net
new useful yield. Known issuer mappings come from explicit adjudications, not
headline or ticker similarity. Unknown issuers remain in their own recall stratum.

An `event_assessment` references one exact current source adjudication. It must
contain explicit Boolean `useful` and `mapping_error` decisions and one of
`detected`, `abstained`, `backlog` or `not_detected`. A useful result requires a
detected, correct, meaningfully exercised source event with no critical or mapping
error. Its assessment time cannot precede the source adjudication.

The report derives, rather than accepts, counts and denominators. Different
assessment IDs for the same unit or the same underlying external evidence are
ambiguous and excluded. Exact import replay is idempotent. A correction may
supersede an exact prior assessment; revoking the replacement does not restore
the older usefulness decision. All prior receipts remain available for audit.

Useful candidates are counted once per reviewed assertion origin. Bundled event
units remain visible, and shared-origin correlation is explicitly pending. The
report exposes useful origin IDs, representative unit IDs and the full set of
useful unit IDs. Its sample floor is prespecified in the proposal (default 100).
Synthetic small-sample fixtures use a smaller comparison floor only to exercise
arithmetic; the existing WS3 release thresholds remain unchanged and must still
pass for actual expansion review eligibility.

## Measurement definitions

| Metric | Denominator or source | Missing/negative behavior |
| --- | --- | --- |
| Useful-event yield | Distinct useful incremental candidate origins / distinct incremental candidate origins | Unassessed/incomplete evidence gives `estimate=null`; observed counts remain visible |
| Mapping errors | Explicit current assessment decisions | Unit IDs retained; errors keep eligibility pending |
| Overall recall | Detected source events / all adjudicated event-present source units | Miss IDs retained; incomplete source adjudication leaves the complete estimate unknown |
| Unknown-issuer recall | Same calculation in the independently labelled unknown stratum | No unknown gold gives an unknown denominator and a pending reason |
| Negative-outcome recall | Same calculation for adjudicated negative outcomes | Negative outcomes and missed negative events remain visible |
| Abstention / backlog | Explicit processing state, separately for baseline and candidate units | Missing assessments are `unassessed`, not zero abstention |
| Requests / attempts / retries | Distinct logical request IDs, exact attempt records, ordinal retries after attempt 1 | Missing census/counter, duplicate attempts or mismatched scope gives null complete counts |
| Review effort | Valid WS6 review-effort clock intervals | No default duration, no rate assumption, no conversion into money |
| Incremental direct cost | Expanded direct cost minus baseline direct cost in the same currency/window/version | Signed difference retained; unmatched, incomplete or reused evidence makes it unknown |
| Incremental cost per useful event | Aligned cost difference / deduplicated useful candidate origins | Zero useful events, incomplete assessments or invalid source inventory gives null |

Descriptive estimates are labelled separately from complete estimates. A
synthetic fixture may show an arithmetic result, but `real_gate_observations=0`
and the report remains pending. The report also requires current WS3 manual-pilot
and recall eligibility; a supplied Wave A attestation does not bypass those gates.

## Request and cost evidence contracts

A `request_attempt` contains the arm, request ID, attempt ID and positive ordinal,
source cycle, source route, GET/POST method, start/completion clocks and observed
HTTP status (or null). It retains the full source-cycle hash. No URL, request body
or credential is needed in the measurement record.

A `request_census`, observed after the window closes, lists the exact attempt
receipt IDs and source-cycle IDs. Each cycle's `health.metrics` must contain:

```json
{
  "expansion_scope_hash": "the exact frozen baseline or expanded scope hash",
  "request_counts": {"attempt": 2}
}
```

This is an integration contract for actual authorized run telemetry. Existing
budget-role counters are not proof of HTTP attempt or retry counts. Old cycles
that lack these fields remain usable historical evidence but leave the expansion
request measurement pending. The tool does not run either arm, fabricate a cycle,
fan out collectors or convert a budget allocation into an observed request.

Costs and review effort are validated through Workstream 6's public
`operating.validate_record` API. Costs require exact monetary strings, currency,
allocation basis, source references and typed external evidence. All five direct
cost categories must be represented, including explicit zero observations where
appropriate. One exact `cost_completeness` record enumerates every included cost
receipt. Its category claim alone cannot invent missing zero costs.

When WS6 provides `run_cost`, the comparison uses its `cost_components` projection
for the evidenced formula `review_minutes * review_hourly_rate / 60 +
operator_minutes * operator_hourly_rate / 60 + vendor_amount +
compute_storage_amount + other_amount`. Every input must be supplied and validated;
there are no default rates. Signed vendor, compute/storage and other credits are
retained. Calculations use 40 significant decimal digits without inventing a
currency-rounding policy. Run-cost labor minutes do not become independently measured review
effort. A missing compatible component validator leaves run costs pending.

Repeated underlying cost evidence is not a second cost. Shared cost/request
evidence across arms is a confounder. Different reporting windows or policy/code
versions cannot be combined. This workflow uses its own reviewed candidate-event
denominator and does not relabel WS6 useful-brief assessments as candidate events.

## Review and gate contract

The typed review purposes are:

| Purpose | Exact subject | Bindings |
| --- | --- | --- |
| `expansion_observation` | Normalized event assessment, attempt or census | Proposal ID plus all WS3 version bindings |
| `expansion_candidate_identity` | Frozen instrument/issuer/listing evidence for one added candidate | Proposal ID plus all WS3 version bindings |
| `expansion_wave_a_review` | Protocol ID, current source-evaluation hash and coverage-snapshot hash | Proposal ID plus all WS3 version bindings |
| `expansion_candidate_review` | Proposal hash, current source-report hash, observation/operating refs, coverage hash and explicit candidate list | Proposal ID plus all WS3 version bindings |

Attestations must have the expected schema, exact subject hash and purpose,
reviewer, external reference/hash, permitted evidence kind, valid clocks and exact
nonempty bindings. Expired or revoked records fail current validation. Generic
receipt import does not bypass observation shape checks or evidence rederivation.

`validate_report(store, receipt_id, registry, policies, *, current_coverage, now,
instruments=None)` is the dependent-gate hook. It loads the persisted report,
requires actual operator evidence, reconstructs the current report from the exact
references, rejects pending/stale/synthetic data, verifies the receipt reference
set and returns the original validated envelope. A persisted `status` or truthy
placeholder is never enough. Its caller may expose readiness for human review
only; it must not treat readiness as permission to change membership.

These checks validate consistency of supplied local records. They do not
authenticate a human, prove an invoice or observation happened, grant source
rights, or establish legal approval. No genuine approval, cost or pilot record
was created by this engineering task.

## Local commands

The standalone entry point is usable without changing the existing CLI:

```bash
python -m omni.expansion_cli expansion-plan
python -m omni.expansion_cli --db /path/to/local-events.db expansion-propose PROTOCOL_ID /path/to/operator-candidates.json
python -m omni.expansion_cli --db /path/to/local-events.db expansion-observe PROPOSAL_ID /path/to/observation.json --prepare
python -m omni.expansion_cli --db /path/to/local-events.db expansion-observe PROPOSAL_ID /path/to/attested-observation.json
python -m omni.expansion_cli --db /path/to/local-events.db expansion-report PROPOSAL_ID
python -m omni.expansion_cli --db /path/to/local-events.db expansion-report PROPOSAL_ID --reviews /path/to/review-refs.json --persist
```

`expansion-plan` creates no store, selects no candidates and makes no network
request. `--prepare` returns the normalized review subject, subject hash, required
attestation purpose/bindings and exact references without importing an assessment.
The operator supplies an independently reviewed attestation through the common
receipt importer; this workflow does not manufacture one.

The reviews file accepts only `wave_a_review_ref`, `candidate_review_ref` and
`identity_review_refs` (a map from explicit added symbols to exact receipt IDs).
The report itself contains the exact review subjects for inspection. If evidence
changes, produce and review the new subjects; old approvals are preserved but
cannot approve the changed comparison.

Main CLI integration hooks are `expansion_cli.COMMANDS`,
`register_commands(subparsers)` and
`dispatch(args, *, store, registry, policies, instruments, current_coverage, now)`.
Root owns that integration and dependent-gate wiring.

## Remaining real-world dependencies

The software cannot create elapsed observation windows, authentic operator
reviews, an approved shadow-run scope, source-purpose decisions, genuine attempt
logs, actual labor/invoice records or real useful-event adjudications. Those
records are absent. Real request measurement also requires the source-cycle
telemetry contract above to be supplied by an authorized run; current legacy
budget counters alone do not satisfy it.

The workflow is ready to import and validate such records when they exist.
Until then, actual expansion eligibility remains pending, source routes retain
their existing disabled/approval state, and editorial/quantitative membership
and signal eligibility remain unchanged.
