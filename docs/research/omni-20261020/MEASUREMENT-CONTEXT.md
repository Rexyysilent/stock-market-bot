# Frozen expansion measurement context

This followup adds the read-only collector integration hook after the finite
Workstream 5 implementation. It creates no measurements, protocol approvals,
coverage members, source permissions or activated routes. Synthetic verification
remains separate from actual operating evidence; all AI labels remain pending
human review.

## Callable contract

```python
measurement_context(
    store, proposal_id, arm, registry, policies, current_coverage,
    source_id, route_id, now,
    *, family=None, instruments=None, allow_synthetic=False,
)
```

The implementation is `omni.expansion.measurement_context`. `arm` is exactly
`baseline` or `expanded`. The default loads the current instrument registry when
none is supplied and accepts only actual operator evidence from a prospective
protocol. An explicit Boolean synthetic opt-in is available for offline fixtures.

The function verifies the current protocol and re-creates the proposal from its
frozen candidate, coverage and identity inputs. It also re-creates the normalized
protocol request and verifies the exact typed protocol approval, current version
bindings, approval expiry and revocation, immutable reference list, and recording
clocks. The protocol and proposal must precede collection. The measurement clock
must be in the half-open interval `[window_start, window_end)`.

Current coverage, instrument identities, implementation, dependency locks,
entity registry and policies must match the frozen proposal. Unknown candidate
identities and an empty candidate set difference cannot produce a comparison
context. This is a consistency check on supplied references; it does not
authenticate a person or establish source rights.

## Collector integration

The return object is suitable for merging into source-cycle health metrics:

| Field | Meaning |
| --- | --- |
| `expansion_scope_hash` | Frozen baseline or expanded comparison scope |
| `expansion_proposal_id` | Exact immutable proposal receipt |
| `expansion_arm` | Explicit baseline or expanded arm |
| `sampling_family` | Exact prespecified source/route/family stratum |
| `query_hash` | Existing query-plan hash frozen in that protocol stratum |
| `expansion_measurement_context` | Versioned source, route, window, evidence kind, coverage/identity/version bindings and current measurement clock |

`family` may be omitted only when exactly one frozen stratum matches the source
and route. With two possible families, the caller must supply one explicitly.
The target family is sampling metadata selected before acquisition. Native source
rows must remain in the denominator even when the classifier emits no matching
event, fails, abstains, or maps an unknown issuer.

Before the collector requests a page, its actual existing query plan must match
the returned frozen `query_hash`. The helper has no query-body input and cannot
prove that comparison itself. Target-family metadata must not alter the query
hash or select source rows using classifier output. An unmatched query remains
unmeasurable under that protocol; a fresh protocol is needed for changed scope.

The helper returns no request totals. The collector adds its independently
counted actual request attempts under `request_counts.attempt`; request logs and
the closed census still have to reconcile with the source cycle. The baseline
and expanded arms share the same prespecified source inventory and query plan.
No per-candidate request fanout or filtering is introduced.

Source-purpose checks, disabled-route defaults, rate limits, budgets, URL/DNS
checks and transport authorization remain the collector's independent gates.
This context cannot approve any of them. The root integration owns watch-command
options and source-cycle telemetry wiring; this followup changes only the pure
context API, its offline checks and this contract note.

## Focused verification map

The eight new methods in `test_omni_expansion.py` exercise:

| Test suffix after `test_measurement_context_` | Verified behavior |
| --- | --- |
| `is_read_only_and_keeps_frozen_query_and_source_frame` | No DB or membership changes; distinct arm scopes share the same query and source frame; no invented counters or source permissions |
| `defaults_to_actual_and_rejects_nonprospective` | Synthetic requires explicit Boolean test opt-in; development partitions cannot become prospective measurements; default instrument loading |
| `requires_bounded_arm_window_and_exact_source_route` | Explicit arm, start inclusive/end exclusive window, exact source and route |
| `infers_family_only_for_a_unique_frozen_stratum` | Ambiguous families require explicit selection; each selected stratum preserves its query hash |
| `rechecks_current_coverage_identities_and_all_versions` | Coverage, instrument and every current protocol binding are checked again |
| `rechecks_protocol_approval_expiry_and_revocation` | An old approved protocol cannot bypass current expiry or revocation |
| `reproduces_proposal_instead_of_trusting_imported_scope_hashes` | Imported altered scope hashes and future recording clocks are rejected |
| `does_not_measure_unresolved_or_empty_candidate_membership` | Unknown identity or empty additional membership yields no measurement binding |

The accompanying `measurement-context-test-receipt.json`, text hash manifest and
Python 3.11/3.12 logs record this followup's tested snapshot. Earlier Workstream 5
logs remain historical evidence for their original implementation snapshot; they
are not relabeled as tests of later changes. Final full-repository verification
belongs to the root's combined candidate.
