# Workstream 3: prospective evaluation and receipt importers

Prepared 2026-10-20. This is an implemented local workflow and a verification
record, not evidence of a completed real pilot. All executable fixtures are
synthetic. The nine public-source research cases retain their existing AI draft,
pending-human-review status. Automatic activation remains false.

## Provenance and scope

The implementation starts at corrected local candidate
`1a0ae6d2a277642e9466495323c11469f43f911a`, built on exact public revision
`d35f667aebd3c2590cabbbe80e51076b3a1fa788`. The supplied corrected patch has SHA256
`df63e09f5a3d79c27091ac8e5cf22bb0de99c6a976818d4304f14f0fda10cbbf`.
This work implements Workstream 3 of the supplied completion handoff and the
two-stream design in
[`PROSPECTIVE-PILOT-PROTOCOL.md`](../omni-20261019/PROSPECTIVE-PILOT-PROTOCOL.md).
The earlier document's statements about missing prospective/timing importers
describe the earlier implementation; the commands below now provide that bridge.

Workstream 1 supplies immutable receipt storage, source-cycle observation records,
reviewed source/native/revision correspondence, revision-aware event packets and
current lifecycle validation. This work consumes those interfaces. It does not
replace the main collector, install a scheduler, acquire new rights, change
coverage membership, publish a brief or enable any vertical.

## Implemented modules and contracts

| Module | Responsibility |
| --- | --- |
| `omni/prospective.py` | Future protocol, source enumeration, source inventory closure, reviewed event units, gold adjudication, run import, evaluation receipt and legacy migration |
| `omni/publications.py` | Complete emitted-proposal census, exact historical proposal inspection and independent publication scoring |
| `omni/timing.py` | Source availability, acquisition and candidate-completion proofs; conservative timing calculation and source/route/family reports |
| `omni/intervals.py` | Source retention contract and bounded polling-interval proof with explicit external attestation boundary |
| `omni/holdout.py` | Untouched partition seal, custody/access history, one-use evaluation, contamination and invalidation |
| `omni/prospective_cli.py` | Standalone public commands and registration/dispatch hooks for the parent OMNI command |
| `omni/evaluation.py` | `omni-evaluation-3`; descriptive JSON cannot establish verified release readiness |
| `omni/pilot.py` | `omni-pilot-3`; legacy frozen-corpus audit remains separate; implementation hashing includes nested OMNI modules |

All new workflow payloads use `contract: omni-prospective-1` inside the shared
`omni-receipt-1` immutable envelope. Receipt IDs are content addressed, references
are immutable and imports are transactional. An exact replay returns the
original receipt, including its original import time. A correction appends a
new receipt naming the current `supersedes` receipt. Replaying the old input
cannot make it current again.

Every protocol binds the recursive OMNI implementation hash, full source-policy
hash, registry fingerprint, individual SHA256 values and aggregate hash of both
dependency locks, classifier/validator/evaluation/evidence contracts, captured
coverage membership, source/route/family strata, query-plan hash, source
enumeration contract, sampling method/seed/buckets, rubric/exclusion hashes,
polling cadence, sample minima and stop rule. A changed code, policy, registry
or lock era cannot pool old evidence into a current gate.

`query_hash` is the frozen query-plan hash. Dynamic offsets, watermarks and
individual request parameters belong to the collector's request context; they
must not silently change the frozen selection rule.

## Two complete denominators

### Independent source recall

The source enumerator reads only exact immutable native-inventory membership
and its coverage receipts. It never queries classifier events to select the
recall denominator. Records that have no parsed event, no resolved issuer or no
successful detection therefore remain enumerable.

A protocol freezes an entirely future window. It supports a census or a
deterministic hash-modulus selection with a prespecified seed and buckets.
The enumerator includes only native revisions first observed within that window;
older observations remain explicit exclusions. For real protocols, synthetic,
historical and unbound source clocks cannot establish first observation.

Each exact source/native record has a stable origin across its revisions.
Reviewed `same_release` and `republishes` correspondence can merge copies,
including transitive copies, using Workstream 1's exact source support.
Independent reporting remains separate. Headlines, similar values and common
issuers do not prove that two documents are copies. Source-supported bundled
events need an exact reviewed mapping before closure. Mapping approvals are
rechecked for scope, revision set, versions, expiry and revocation on every
report. One detection cannot be credited to two units.

Closure requires the elapsed window, all frozen strata, complete coverage,
retained native membership and an actual supplied closure review. A capped or
partial attempt stays in history. A later complete matching frame may resolve
that gap only if it retains every previously observed native record; recovery
cannot discard the difficult examples. The full inventory's origins, including
unsampled units, cannot be reused across closed development, calibration,
prospective and untouched partitions.

Every selected unit must receive supplied human adjudication. Unknown issuers,
negative outcomes, non-event controls, detections and misses are separate fields.
Known issuers require exact identities available in the frozen registry era.
A missing adjudication is pending and is never turned into an invented miss.
A claimed detection must match the prespecified family, current event revision, input hash, exact
source/native/revision origin and lifecycle state. Superseded, quarantined or
changed evidence invalidates detection credit while preserving its history.

### Proposed-publication precision

The precision stream is a separate census of every scoped emitted classifier
event unit in the fixed window. It reads immutable `event_packets`, including
earlier packet revisions and proposals withheld by human review. It does not
filter to approved proposals or the sampled recall units. The caller's
`proposed_publication` flag cannot choose this denominator.

One unit retains every emitted packet revision and every material claim. A
human score binds the complete proposal-set hash; an earlier incorrect proposal
cannot disappear when a corrected head arrives. A material error makes the
whole publication unit incorrect under the supplied scoring rubric. Missing
scores keep the gate pending. Unknown source-class, ambiguous multi-origin
packets and unresolved bundle correspondence also remain pending.

The exact historical packet inspection command resolves event ID, semantic
revision and packet hash together, avoiding accidental display of the current
head in place of an earlier proposal. If current review-display permissions are
absent, it withholds both source text and event claims containing source text.
Untouched packet inspection requires an explicit access log.

## Coverage and clocks

Coverage imports re-resolve the actual source cycles and native observations,
their import/acquisition clocks, membership hashes, complete counts, query,
route/family, current attestations and frozen versions. An acquired-index set
does not establish real-time source availability or correction surveillance.
Real feed coverage additionally requires the source-cycle traversal proof and
exact raw page hash/native-ID correspondence. A caller's assertion that traversal
was contiguous is insufficient.

Polling alone cannot prove that no record appeared and disappeared between
polls. Real operational feed intervals therefore require a separately reviewed
source retention/change-log contract and a typed interval verification. Those
receipts bind the exact source/query/window, raw observations, cycle input and
traversal hashes, minimum retention duration and actual maximum observation gap.
The observed gap must fit both the source's supplied retention contract and the
frozen polling cadence plus bounded clock uncertainty. The result always reports
`continuous_source: false`; its narrower claim is bounded polling coverage under
that source-specific contract. A missing, expired or revoked contract leaves
coverage pending.

Operational latency uses three separately referenced proofs:

1. Independent source availability from a dissemination log or availability API.
2. Collector acquisition start/completion bound to the exact source cycle.
3. Candidate completion bound to the exact immutable emitted event packet.

Publication time, acquisition time, first observation, candidate completion,
human review time and receipt import time remain distinct. Source publication
alone and the first successful poll do not prove availability. First observation
does not prove candidate completion. Each clock carries precision, timezone
basis and uncertainty. Date-only, absent-zone, missing-clock and uncertain-order
cases remain descriptive, with reason codes and uncertainty intervals.

The reported operational duration is the conservative upper bound from source
availability to exact candidate completion. Caller-supplied latency scalars or
`continuous_source` flags are rejected. Every required source/route/family has
its own expected denominator, excluded/missing units, minimum sample and p95;
one populated source cannot conceal an absent stratum. Real timing needs p95 at
most twice the frozen polling interval. Synthetic timing receives zero real
gate observations even when the synthetic proof chain validates.

Reports apply an as-of import cutoff before choosing correction heads. An
adjudication reviewed earlier but imported later cannot receive retroactive
credit. Coverage, timing, interval, publication and custody evidence obey the
same cutoff. Known future records are made explicit as pending reasons. Current
event heads and lifecycle state remain current safety checks; an earlier report
cannot reconstruct a false historical approval after evidence has changed.

Generic receipt import is a durable transport, not an eligibility decision.
Readers reconstruct enumerations and closed inventories from retained native
source membership; imported rows cannot omit unknown or difficult records.
Run receipts are normalized again against their actual source cycles, and gold
adjudications are normalized again against exact event correspondence, source
units, reviewer clocks, frozen identities and correction predecessors. An exact
attestation on an internally inconsistent payload does not substitute for those
checks. Invalid records remain in the ledger, stay visible as pending reasons,
and receive no valid descriptive or real gate counts.

## Untouched partition lifecycle

A protocol declares `partition: untouched` and its custodians before collection.
Both source inventory and complete publication census close before sealing.
No prior adjudications or access history may exist. A supplied custodian approval
binds both inventories and the exact implementation/policy/registry/lock era.
Actual `sealed_at` records when the successful operation happened, after any
request and approval; it is not a prefilled proposed approval clock.

Blind source and publication adjudication automatically record access. Other
inspection paths require an explicit actor, purpose and time. Controlled access
binds the full exact attested subject, the correct partition inventory and the
frozen custodian. Unapproved access, a different actor, tuning/development use,
external exposure, a stale/revoked review or an implementation change contaminates
or invalidates the partition. Ordinary status reports do not expose full access
subjects containing labels.

Controlled evaluation requires all source and publication labels, then records
the current metrics, exact label/access sets, actual consumption time and
performance reasons. A failed evaluation still consumes the test. Replaying the
same evaluation receipt is idempotent; a different evaluation requires a new
prespecified partition. A real gate checks the receipt again for changed labels,
changed data, permissions, access approvals, critical errors and required
performance. Synthetic custody tests cannot supply a real untouched-test receipt.

This is a consistency and access-history mechanism. A seal is not encryption;
hashes do not authenticate people, establish source rights or prove that an
external person never viewed data. Independent custody and reviewer authenticity
remain an explicit supplied-evidence boundary, printed in reports.

## Public command workflow

All commands are available through the standalone local entry point:

```sh
python -m omni.prospective_cli --db PATH --sources POLICY_JSON --coverage COVERAGE_JSON COMMAND
```

`--coverage` is the captured existing coverage snapshot, not permission to add
members. Without it the current configured coverage is read. The parent OMNI CLI
can expose the same commands via `register_commands(subparsers)`, `COMMANDS`, and
`dispatch(args, store, registry, policies, coverage, now=...)`. The CLI uses its
actual UTC clock; there is no command-line clock override. Tests inject a clock
through the Python entry point only.

| Stage | Commands and inputs |
| --- | --- |
| Request and freeze | `prospective-request SPEC_JSON`; preserve its normalized JSON, obtain the actual typed `prospective_protocol` review, add `review_receipt_id`, then `prospective-freeze REVIEWED_REQUEST_JSON` |
| Coverage | `prospective-coverage PROTOCOL SOURCE_COVERAGE_RECEIPT`; optionally `prospective-retention-request/import PROTOCOL JSON` and `prospective-interval-request/import PROTOCOL JSON` for real feed proof |
| Enumerate | `prospective-enumerate PROTOCOL JSON`, whose `coverage_receipt_ids` names the exact acquired frames |
| Bundle mapping | `prospective-map PROTOCOL JSON`, with reviewed exact origin/native revisions and bounded unit keys/scopes |
| Close source sample | `prospective-closure-request PROTOCOL`; review that exact subject; `prospective-close PROTOCOL --review-receipt RECEIPT` |
| Source gold | `prospective-adjudication-request PROTOCOL JSON`; add the actual review receipt; `prospective-adjudicate PROTOCOL JSON` |
| Publication census | `prospective-publication-closure-request PROTOCOL`; then `prospective-publication-close PROTOCOL REVIEW_RECEIPT` |
| Publication scoring | `prospective-publication-packet PROTOCOL JSON`; `prospective-publication-adjudication-request PROTOCOL JSON`; then `prospective-publication-adjudicate PROTOCOL JSON` |
| Runs | `prospective-run-request PROTOCOL JSON`; then `prospective-run PROTOCOL JSON` with actual scheduler/invocation evidence, complete scoped cycles and typed review |
| Clocks | `prospective-clock-request/import PROTOCOL availability\|acquisition\|candidate JSON`, then `prospective-timing PROTOCOL JSON` referencing all proofs and coverage |
| Custody | `prospective-seal-request PROTOCOL`; `prospective-seal PROTOCOL REVIEW_RECEIPT`; explicit `prospective-access` or `prospective-inventory-access PROTOCOL JSON` |
| Controlled test | `prospective-consumption-request PROTOCOL`; `prospective-consume PROTOCOL JSON`; `prospective-untouched-status PROTOCOL` |
| Report | `prospective-report PROTOCOL [--untouched-receipt RECEIPT]` |
| Durable report | `prospective-record-evaluation PROTOCOL [--untouched-receipt RECEIPT]`; `prospective-verify-evaluation RECEIPT` |
| Legacy migration | `prospective-migrate-legacy` |

Request, approval and recording may occur at different times. The normalized
protocol request retains `requested_at`; successful freeze records its actual
`frozen_at` and must still precede the future window. A seal/evaluation request
does not claim a future recording time. Changed inputs between request and
approval invalidate the exact subject hash. Reuse the normalized request rather
than generating a new request clock after human review.

Actual operator attestations enter through the shared receipt importer. Request
commands produce reviewable subjects; they do not manufacture approvals. The
typed attestation must reference that subject hash, purpose, exact version
bindings, actual reviewer/review time and external reference/hash. There are no
real attestations or credentials in the test suite or this document.

## Evaluation integration and unchanged thresholds

`record_evaluation(...)` writes kind `prospective_evaluation`. Its payload has
the full report, `report_hash`, exact `input_receipt_ids` and their set hash,
protocol/partition/evidence kind, optional untouched consumption receipt, exact
bindings and `automatic_activation: false`.

`verify_evaluation_receipt(store, receipt_id, registry, policies, now=..., require_real=True)`
recomputes the report and exact input set, rejecting changed inputs or current
invalidations. The default rejects synthetic, historical and nonprospective
evidence. It returns the envelope plus `current_report` and `verified_at`. A
consumer must inspect the verified current report; possession of a receipt ID
alone is insufficient.

The readiness fields use these exact values only when the corresponding proof
and thresholds are satisfied:

- `manual_pilot.status: eligible_for_review` requires ten distinct eligible
  scheduled sessions, 100 meaningful outside-cohort cases, zero critical leakage,
  complete verified coverage and all relevant current evidence.
- `recall.status: eligible_for_review` requires at least 100 prospective positive
  gold events, overall and unknown-issuer recall at least 90%, the frozen unknown
  and negative-outcome sample requirements, and no unresolved adjudications.
- Overall publication precision needs at least 300 proposed units, an estimate
  of at least 98% and a 95% Wilson lower bound of at least 95%.
- `family_source_gates[].status: eligible_for_activation_review` additionally
  needs at least 100 proposed units in that family/source stratum, the overall
  and stratum precision bounds, verified recall/timing and a valid real untouched
  evaluation. Absent required strata remain visible.
- `timing.status: eligible_for_review` requires verified operational samples in
  every required timing stratum and the p95/cadence condition above.

The normal outcomes while evidence is absent are `pending` and `remain_manual`.
Default protocol minima are 30 timing observations per stratum, 30 unknown-issuer
gold events and at least one negative-outcome gold event; the reviewed protocol
can prespecify a stricter design. The poll interval is bounded at 900 seconds.
Synthetic fixtures use small explicit protocol minima to exercise mechanics,
but never enter any real denominator or relax the existing release thresholds.
Wilson intervals describe observed event units and do not correct remaining
issuer/program/session correlation; independent evaluation design still matters.

The old JSON evaluator remains useful for descriptive calculations. Truthy
receipt strings, fabricated scheduler flags or supplied latency numbers cannot
construct the internally verified receipt context. Downstream OMNI gates must
use the durable verifier rather than trusting exported report JSON.

## Schema, upgrade, rollback and history

There is no destructive schema migration in this workstream. New records use
the shared additive `omni_receipts` table and existing immutable event/source
tables. Existing `pilot_records` stay byte for byte intact. The explicit legacy
migrator appends `prospective_legacy_history` links with old record/pilot/kind/time,
canonical payload hash and exact original payload-bytes hash. Every link says
`partition: historical_audit` and `real_prospective_count: 0`.

The migrator is atomic and idempotent. Its interruption fixture raises after the
first attempted link and verifies that the transaction rolls back completely;
rerunning appends one link per legacy record without modifying the original.
No rollback deletes evidence or rewrites an old version. The shared additive
store migration and its backup/recovery procedure remain Workstream 1's domain.

## Verification map

`test_omni_prospective.py` executes actual temporary stores and public APIs. Its
CLI fixture traverses request → freeze → source enumerate → reviewed close →
gold adjudicate → publication close → report → durable evaluation, and proves
that synthetic verification cannot mature a real gate. It uses no live provider,
real scheduler, real human decision or source-use approval.

| Requirement / adversary | Executable test coverage |
| --- | --- |
| Future freeze, exact versions, delayed human review and idempotency | `test_freeze_requires_future_rules_review_and_not_classifier_ids`, `test_exact_freeze_replay_is_idempotent_and_policy_drift_rejected`, `test_request_approval_and_actual_freeze_can_have_distinct_clocks` |
| Classifier-independent unknown/missed source events | `test_source_first_inventory_includes_unknown_miss_without_any_classifier_output`, `test_end_to_end_exact_hit_and_miss_remain_synthetic`, `test_missing_prospective_inventory_stays_pending_without_invented_misses` |
| Coverage gaps, cap recovery, absent strata and frozen query | `test_capped_observation_and_unclosed_interval_leave_inventory_pending`, `test_missing_source_stratum_cannot_close_and_query_changes_are_explicit`, `test_complete_later_frame_resolves_cap_without_deleting_history`, `test_recovery_cannot_drop_previously_observed_native_id` |
| Exact units, syndicated copies, revision lineage and partition reuse | `test_duplicate_enumeration_does_not_multiply_source_events`, `test_reviewed_transitive_copies_count_one_assertion_origin`, `test_independent_reporting_does_not_merge_origins_and_late_copy_invalidates`, `test_revised_same_native_disclosure_keeps_one_origin_and_all_proposals`, `test_reused_origins_across_development_and_untouched_do_not_close` |
| Another family from the same source cannot stand in for the target event | `test_another_family_from_same_native_disclosure_is_not_a_gold_detection` |
| Bundles, one-to-one detection, revoked mappings and post-hoc sampling | `test_bundled_event_mapping_is_reviewed_before_sampling_and_cannot_reuse_detection`, `test_adjudication_hash_native_mapping_and_one_to_one_constraints`, `test_posthoc_enumeration_and_mapping_are_rejected` |
| Complete publication denominator and correction history | `test_publication_census_is_independent_of_recall_sample_and_approval`, `test_unadjudicated_proposals_keep_precision_gate_pending`, `test_publication_replay_correction_and_revocation_preserve_denominator`, `test_non_event_false_proposal_stays_in_complete_publication_denominator` |
| Timing proof, uncertain clocks, source traversal and retention | `test_timing_uses_referenced_clocks_and_synthetic_counts_stay_zero`, `test_date_only_publication_and_unknown_timezone_never_get_latency_credit`, `test_unknown_acquisition_start_cannot_be_hidden_by_precise_completion`, `test_real_feed_gate_requires_actual_traversal_and_retention_proof`, `test_retention_interval_has_exact_scope_clocks_and_revocable_review`, `test_short_retention_or_missed_poll_cadence_cannot_certify_interval` |
| Future imports and corrections cannot alter prior counts | `test_delayed_imports_cannot_backdate_gold_or_scheduled_run_counts`, `test_future_corrections_do_not_supersede_prior_as_of_heads`, `test_future_timing_proofs_and_measurements_cannot_enter_earlier_report`, `test_future_source_closure_and_coverage_are_not_visible_at_earlier_clock` |
| Generic imports cannot assert nonexistent cycles, drop source records, or invent detection correspondence | `test_generic_run_receipt_with_attested_but_nonexistent_cycles_is_excluded`, `test_generic_enumeration_cannot_drop_unknown_source_records`, `test_generic_inventory_closure_cannot_omit_unknown_source_records`, `test_generic_adjudication_cannot_claim_detection_without_correspondence` |
| Custody, contamination, actual recording times and one-use test | `test_untouched_seal_adjudication_controlled_consumption_is_single_use`, `test_custody_request_review_and_actual_recording_clocks_can_differ`, `test_untouched_uncontrolled_access_and_missing_adjudications_block_consumption`, `test_untouched_access_requires_exact_subject_and_rechecks_expiry`, `test_untouched_access_cannot_reuse_attestation_for_another_partition`, `test_untouched_future_access_is_excluded_and_remains_explicitly_pending` |
| Bare JSON cannot mature real gates; report receipts must remain current | `test_raw_evaluation_flags_cannot_mature_timing_or_release`, `test_evaluation_receipt_rechecks_exact_inputs_and_rejects_synthetic_gate` |
| Migration preserves history and rolls back on interruption | `test_legacy_migration_is_atomic_idempotent_and_preserves_original_bytes` |
| Public command path | `test_public_command_path_freeze_enumerate_close_adjudicate_report` |

The focused supported-runtime command is:

```sh
python -m unittest -v test_omni_prospective test_omni_evaluation test_omni_pilot
```

Logs in the accompanying `verification/workstream-3-*` files identify the actual
runtime, dependency-lock hashes, implementation hash, command and result. The
focused suite passed **72 tests on Python 3.11 and 72 on Python 3.12**. Both
locked environments passed `pip check`, and the new modules compiled on both.
The parent integration performs the full repository checks required by `AGENTS.md`
after combining the shared lifecycle, clinical, vertical and downstream-gate
workstreams; these focused logs do not claim that combined verification.

## Real evidence still required

This code does not complete the real ten-session/300-publication pilot. Actual
source-purpose approvals, retention contracts, independent source inventories,
genuine scheduler and three-clock evidence, current human gold/publication
adjudications, independent custody, and an unconsumed real test partition are
still required. No historical research case or synthetic test is promoted into
that evidence. Rights, customer/user-value evidence, operating costs and any
explicit activation decision belong to their separate typed gates and remain
pending until actual evidence is supplied.
