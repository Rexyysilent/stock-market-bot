# Work Package C — prospective protocol and evaluator repairs

Prepared 2026-10-19. Exact baseline:
`d35f667aebd3c2590cabbbe80e51076b3a1fa788`. AI draft pending human review.
No actual pilot freeze, scheduler execution, human review or source attestation
was created. The [prospective protocol](PROSPECTIVE-PILOT-PROTOCOL.md) and
[eight blank templates](pilot-templates.json) are concrete preparation artifacts.

## 1. Existing behavior confirmed

`pilot.implementation_hash` binds OMNI implementation files and integration
helpers. `pilot_plan` is a draft; `freeze_pilot` requires a nonempty, reviewed,
prespecified corpus and records implementation, policy, registry and coverage
fingerprints. `record_case` checks exact event/evidence input hash, prospective
evidence kind, freeze-relative observation/adjudication time and event uniqueness.
`record_receipt` verifies referenced source cycles exist and are complete before
accepting run/coverage records, while scheduler identity remains operator-supplied.
`pilot_report` blocks current-version readiness after implementation/policy/registry
changes and retains old records.

`evaluation.evaluate` reports actual included precision and recall populations,
exclusions, Wilson intervals and distinct session strings. The original code
already rejected synthetic precision cases, deduplicated event-case IDs, preserved
explicit gold misses and blocked outside-cohort publications without review.
`measurements` preserves missing values, era/cohort separation and time-ordering
requirements; it explicitly withholds statistical inference and effective n.

These protections do not by themselves authenticate external people, prove blind
adjudication, verify an untouched test, establish source completeness, or make
correlated events independent. The original 16 focused tests passed while the
three counterexamples below still succeeded.

## 2. Demonstrated defects, ordered by impact

| Case | Minimal baseline counterexample | Observed false intermediate readiness |
| --- | --- | --- |
| C-G01 | Freeze 100 historical gold IDs; map all to one stored synthetic event through the public gold-receipt API. | Prospective recall appeared eligible with n=100 and 100% detected, including unknown-issuer n=100. |
| C-R01 | Reuse one complete source cycle and the same actual interval in ten run records, changing run IDs and arbitrary session labels. | Ten eligible scheduled sessions were counted. |
| C-G02 | Freeze 200 gold IDs; adjudicate only 100 distinct matching replay events as hits. | Recall appeared eligible from n=100; the other 100 were absent from pending/completeness reporting. |

All inputs in these proofs were explicitly simulated in temporary stores. They
were not evidence of a real operational incident or actual pilot progress.
`automatic_activation` remained false even in the baseline counterexamples.

## 3. Evidence ledger and limits

These are code-derived cases, with no external medical/financial outcome labels.
The immutable public implementations are:

- [pilot.py at the handoff revision](https://github.com/Rexyysilent/stock-market-bot/blob/d35f667aebd3c2590cabbbe80e51076b3a1fa788/omni/pilot.py): `record_receipt` gold/run branches and `pilot_report`.
- [evaluation.py at the handoff revision](https://github.com/Rexyysilent/stock-market-bot/blob/d35f667aebd3c2590cabbbe80e51076b3a1fa788/omni/evaluation.py): run deduplication, valid-gold filtering and recall gates.
- [measurement implementation](https://github.com/Rexyysilent/stock-market-bot/blob/d35f667aebd3c2590cabbbe80e51076b3a1fa788/omni/measurements.py): denominator, population and information-sequence controls.

The local source was inspected against that exact SHA. An independent automated
reviewer reproduced each defect through public APIs using in-memory synthetic
inputs. The source-case/permission research in A/B does not authorize pilot body
collection. Every human identity, source-use approval, billing record and
scheduler receipt remains an actual external/operator input.

## 4. Applied changes and concrete protocol

### Gold correspondence and tracks

`omni-pilot-2` gold imports reject synthetic or unverified detection packets.
Positive matches require the exact adjudicated event/documents hash and a matching
frozen source URL; an available native identifier must also match. Observation
must precede adjudication. A detected event cannot be reused for a second gold
parent unit. Copy-equivalence or bundled mappings require a future explicit
reviewed correspondence contract rather than weakening this rule.

All gold imported from the prelisted frozen corpus is labelled
`frozen_corpus_audit`. `pilot_report` treats legacy gold receipts as that same
track even if they lack a marker. Audit hit/miss counts remain visible and
contribute zero prospective recall observations. This is deliberate: the
existing prelisted-corpus importer does not establish a future blinded sample.
A proper source-first prospective sampler and closed inventory need the protocol
extension described in the companion document.

`omni-evaluation-2` requires explicit prospective track, prospective evidence,
matching implementation/corpus/scope and ordered observation/review times before
including externally supplied prospective gold. It reports excluded gold. These
checks validate a supplied contract; they do not authenticate its custodian.

### Session and source-cycle binding

The pilot plan now captures `session_timezone=America/New_York` and
`session_rule=local_run_start_date`. The run importer checks the session against
its actual run-start date in that timezone, checks review after completion, and
rejects reuse of a source cycle as a new run. It binds all implementation/corpus/
scope hashes. The evaluator independently checks session/time consistency,
required receipt fields, version hashes and disjoint cycle identities. Repeated
valid runs within one date still count one session. This defines a calendar-date
unit; an exchange holiday calendar remains an operator/protocol input.

### Complete expected gold inventory

`pilot_report` passes the frozen event-ID set to the evaluator. Reports include
`n_expected`, `n_adjudicated`, `n_unadjudicated` and missing IDs. Missing required
adjudications block recall readiness and appear in `pending`. They remain
unknown; they are not silently marked as misses. A standalone evaluation without
a valid prespecified inventory cannot mature the recall gate. Unknown or invalid
gold provenance also keeps it pending.

### Prospective protocol deliverables

The companion protocol provides source scope/preconditions, a frozen version and
sampling plan, event units, unknown-issuer sampling, blind gold custody, a human
rubric, disagreement handling, an untouched partition, coverage and clock receipts,
active review/cost logs, stop rules and measurable decision criteria. Its bounded
request envelope is arithmetic planning, not observed cost or installed scheduling.
The templates deliberately contain null identities/times/decisions and a
`template_only` marker. Extra design fields are not represented as already
supported importer features.

## 5. Acceptance, actual checks and remaining gaps

**22 focused tests passed** on Python 3.12.14 after the patch across evaluation,
pilot and measurements. The six added test methods cover synthetic detection,
exact hash/source correspondence, one-to-one matching, audit/prospective
separation, missing gold, relabelled sessions and cycle reuse. Positive control
checks show that a complete explicitly prospective external evaluation contract
can still calculate recall readiness; it never automatically activates anything.
Final dual-version/offline receipts accompany the delivery.

Acceptance means the three original proofs fail to inflate prospective recall or
sessions; historical audit results stay visible separately; missing gold is
reported without invented labels; version drift stays blocked; and templates
produce no eligible evidence. Human authenticity is not claimed by any test.

Remaining implementation/operating requirements are explicit:

- A frozen **future sampling protocol**, independent source-first enumerator and
  closed prospective event inventory; the current frozen-corpus gold importer
  is an audit interface.
- Source/native/revision correspondence for issuer/filing/wire equivalents and
  bundled-event mappings, with reviewed proof rather than URL similarity.
- Adequate per-family/source timing denominators, exact availability proof,
  interval coverage and a dedicated timing importer. The evaluator's existing
  caller-supplied p95 calculation is descriptive; a single latency and a truthy
  `continuous_source` field do not establish operational timing evidence.
- A real untouched-test receipt lifecycle and custody/access log. The current
  evaluator tests a supplied manifest receipt field; the stored pilot API has
  no dedicated untouched-test importer. No receipt was invented to bridge it.
- Scoped coverage attestation covering every prespecified route; a complete
  source cycle alone is not proof of comprehensive source coverage or later
  correction surveillance.
- Genuine prospective execution, reviewer decisions, calibration adjudication,
  source permissions, useful-brief observations and cost receipts.

The protocol is therefore ready for implementation/operator review, while
activation and genuine pilot progress remain pending. Passing offline tests is
not a substitute for any of these evidence requirements.

## 6. Next bounded task

Package D drafts NRC/EIA adapter/entity contracts, a measured expansion method,
field-level rights and correction practices, interview questions, operational
runbook and economics assumptions. It creates no source route, wider cohort,
customer claim, cost measurement or commercial approval.
