# Prospective OMNI pilot protocol — draft for human review

Prepared 2026-10-19 against the exact public handoff revision and this proposed
patch. **Not a frozen pilot, installed schedule, human decision or operating
receipt.** Every label in the research corpus remains pending. The existing
source-policy flags, coverage membership and automatic activation remain unchanged.
The eight [blank receipt templates](pilot-templates.json) have null required
fields and are not importable evidence.

## Objective and unit of observation

Measure whether permitted source observations become correct, attributable,
time-faithful editorial event candidates with useful abstentions and manageable
human review effort. Do not measure investment value, returns or predictive
performance. Do not treat recommendation generation as an outcome.

The sampling unit is one underlying disclosure event/milestone with its issuer,
trial/program/project, analysis cutoff and origin lineage. Multiple publishers,
filing exhibits, repeated polls and corrected versions do not create independent
cases. Endpoint subclaims remain separately scored within their parent event;
one wrong material subclaim makes a proposed publication incorrect. Report the
number of unique event units, issuers, trial/program clusters, origins and
sessions. Wilson intervals describe the observed binomial denominator; they do
not correct correlation or establish an effective sample size.

A meaningful outside-cohort case must exercise acquisition plus attribution,
claim extraction or justified abstention against a resolved issuer outside the
frozen coverage set. Mention-only tags and duplicated boilerplate do not qualify.
Unknown-issuer discovery failures belong in the independent gold/recall sample,
including cases the pipeline never produced. Preserve unknown identity without
silently changing the frozen registry. A later registry update creates a new
implementation/identity era, with old receipts retained separately.

## Preconditions and freeze

An operator first reviews source-purpose permissions, including any body
retention, local analysis and review display needed for the selected routes.
Public availability and this research register are not approvals. Start with
only explicitly reviewed routes. Clinical primary-source case adjudication,
source-origin equivalence decisions, contact User-Agent, resource budgets and
on-call responsibility must be concrete before scheduling.

Freeze implementation hash, classifier/validator/evaluation contracts, the two
dependency locks, source-policy hash, registry fingerprint, coverage snapshot,
calendar/timezone, route/query list, family/source strata, cadence, sampling
seed/method, scoring rubric, exclusion rules and correction handling. Identify
an independent gold custodian and an untouched test partition before observations.
Record the freeze time after all inputs exist. A changed implementation, source
policy or identity graph starts a new era; do not pool old evidence into a new
gate. Human approvals must refer to actual input hashes.

Keep two corpora separate:

1. **Historical calibration/audit corpus:** primary-source cases selected before
   freeze, including the A ledger once humans adjudicate it. It checks scope,
   extraction and abstention, but contributes zero prospective sessions and
   zero prospective recall observations.
2. **Prospective gold observations:** future in-scope events selected by a frozen
   source-first sampling rule, recorded independently of model candidates. The
   custodian records exact source identity/revision, availability precision and
   scope before matching against the frozen system's output. Known event IDs
   cannot be prelisted for events that have not happened.

The current `pilot-freeze` interface requires a nonempty reviewed corpus and the
current gold importer only accepts IDs from that corpus. A prespecified future
sampling protocol plus later blinded observations therefore needs a separate
manifest/import extension. Do not claim a historical frozen corpus supplies a
prospective recall test. This protocol records that bridge as implementation work;
it does not fabricate future event rows to satisfy the interface.

## Proposed operating envelope

A feasible initial design is a 15-minute interval in a frozen eight-hour weekday
window, with one SEC collector capped at three feed pages per poll, one closed-day
index check, two bounded registry snapshots and one FDA oncology metadata check
per day. Each additional route must have reviewed purpose rights and consume the
same budget ledger. This is a proposed envelope, not an installed schedule.

For an inclusive window with 33 SEC polls, the planned normal metadata budget is
99 SEC feed requests + 1 daily index + 6 registry version/page requests + 1 FDA
index = **107 requests/day**. This assumes each registry snapshot fits one page
and uses before/after version checks. The existing document cap is 80/day; the
worst three-attempt total for 107 metadata plus 80 documents is 561 attempts,
below the existing 750/day attempt cap. Actual failures, additional pages,
correction checks and other processes can consume the margin. Recalculate from
real route behavior before approval, and record every deferral as a coverage gap.
These are arithmetic planning assumptions, not measured costs or observed load.

Collect for at least ten distinct eligible scheduled sessions and continue until
100 meaningful outside-cohort event units exist. Ten elapsed sessions do not
promise 100 cases. Prespecify a maximum review budget and an extension/stop rule;
never broaden the universe or change sampling after seeing low yield. Records
outside the chosen observation window may be useful research but cannot receive
unobserved continuous-coverage or latency credit.

A run is eligible only with an actual scheduler receipt, genuine start/completion
clocks, the correct frozen implementation, observed source cycles in its interval,
and the captured calendar session. Repeated invocations on one session count once.
A manual run, missed schedule, replay or repair is recorded with its actual mode.
No tool in this work has installed or invoked that schedule.

## Sampling and independent gold

Use two prespecified streams. The precision stream includes every proposed
publication in the fixed scope; do not select only easy or approved examples.
The recall stream starts from independent source observations, including pipeline
misses, unresolved issuers, rejected candidates and events outside the display
cohort. Keep the source-first enumerator blind to system labels and confidence.
Use the same event-unit deduplication across both streams, with a match table
that retains one-to-many subclaims and one-to-one parent units.

Stratify by event family and underlying reporting source. Preserve source origin
lineage so an issuer, wire and filing copy do not inflate a stratum. Prespecify an
unknown-issuer sample and report its numerator/denominator separately. The code's
unknown-issuer recall minimum is small; a human evaluation plan should require a
useful sample and uncertainty interval before drawing conclusions. Do not change
that minimum informally after seeing results. The draft target is at least 30
unknown-issuer gold units within at least 100 total gold units, subject to review.

Reserve an untouched temporal or issuer/program partition with an independent
custodian and a recorded access log. Freeze the partition rule before collection,
keep labels inaccessible during tuning, and open it once for the pinned candidate
implementation. Any tuning based on those results consumes the test: record the
failure and obtain a new untouched partition for the next version. A nonempty
string called an untouched-test receipt is not evidence that this happened.

## Human rubric and disagreements

Two reviewers independently score a sample selected before looking at model
outputs; one reviews all proposed publications and all critical/ambiguous cases.
The gold reviewer uses only permitted primary evidence and the frozen rubric.
Reviewers record actual identity, times and source/evidence hashes. AI draft
labels remain visible as drafts where used operationally and are hidden during
independent gold construction.

Score these parallel dimensions:

- **Identity and role:** assertion subject versus partner, comparator, tag,
  registry submitter or document host; unknown identities remain unknown.
- **Clinical scope:** trial/phase, indication, population, arms, endpoint/rank,
  analysis type, cutoff, assessment time, exact numerical values and units.
- **Milestone/stage:** planned versus occurred; pivotal versus phase; preapplication
  versus submitted/accepted application or granted licence; authority/ceiling
  versus obligation/proceeds.
- **Support and time:** each value has the correct immutable source span; current
  results do not inherit historical numbers; source and observation precision
  remain distinct.
- **Publication decision:** supported, contested, incomplete, unknown or withheld;
  favorable p-values do not imply endpoint success; source permissions apply.

Critical leakage includes a wrong issuer/trial/phase attribution, false regulatory
approval, invented source or human approval, retrospective timing credit, or
publication that bypasses a hard source/review gate. Prespecify any additional
critical classes. An incorrect material claim makes the event incorrect; a
correctly scoped abstention may be operationally correct while remaining a miss
for a prespecified detection objective. Report those dimensions separately.

Disagreements go to a third reviewer with both independent labels preserved.
Record the final reason and exact evidence. Unresolved cases remain unknown and
are disclosed; do not silently exclude them from the proposed-publication count
or convert them to correct outcomes. Label corrections append history and do not
increase event-unit n. Authenticity of external reviewer identities remains an
operator responsibility; hashing a receipt does not authenticate its signer.

## Coverage and timing

Each route needs a receipt identifying its requested window, policy/query hash,
page and continuation chain, source cycles, raw metadata hashes, counts,
source-time precision and all outages/caps. A receipt for one complete source
cannot certify another route. Daily-index observations support record recovery;
they cannot prove historical instant availability or later removal surveillance.
Keep body completeness separate from metadata-interval completeness.

Record publication time, independent source-availability time, first observation,
candidate-ready time, review-ready time and human completion separately. Compute
availability-to-candidate latency only when the two clocks are verified and in
the covered interval. A date-only publication or unknown availability is an
explicit exclusion, not midnight and not zero latency. Report exclusions and
coverage debt alongside median/p95 and route/family denominators. Replays and
backfills receive no prospective timing credit.

The current evaluator can consume caller-supplied latency scalars, while the
stored case importer does not create authenticated availability/timing receipts.
A future timing importer must bind the source-availability proof, exact event
revision, coverage receipt, fixed poll interval and clock ordering. Descriptive
latency from one case must not establish readiness for unobserved families or
routes. Record a planned minimum timing sample by eligible family/source before
collection; do not accept a large caller-selected polling interval as a shortcut.

## Review effort, cost and stop rules

Log active review seconds and interruption time separately; count candidates,
publications, corrections and unresolved cases. Record actual requests, bytes,
model tokens if separately approved, provider charges, storage and reviewer time
with billing/allocation evidence. Missing monetary values stay null, not zero.
Report cost per observed event unit and per useful reviewed brief, including
rejected candidates and outage repair. These costs cannot be calculated from
synthetic tests or metadata request counts alone.

Pause public/companion display immediately on critical leakage, lost permissions,
source integrity failure or unreviewed outside-cohort publication. Preserve all
receipts and failed examples. Triage a bounded patch, create a new version era,
repeat the affected regressions and restart only with actual operator decisions.
Continue recording outages rather than deleting unsuccessful sessions. This is
an editorial pilot; automatic production activation remains false throughout.

## Decision table and operator checklist

| Gate | Existing minimum / requirement | Evidence still needed |
| --- | --- | --- |
| Manual pilot review | 10 distinct eligible scheduled sessions; 100 meaningful outside-cohort cases; zero critical leakage; reviewed outside-cohort publications; source-coverage receipt | Genuine scheduled runs, complete scoped cycles, reviewed cases and coverage attestation |
| Overall precision | At least 300 proposed publications; estimate at least 98%; 95% Wilson lower bound at least 95% | Complete proposed-publication denominator and authentic adjudications |
| Family/source review | At least 100 proposed publications in the eligible family/source stratum plus overall criteria | Separate stratum counts and correlation review |
| Recall | At least 100 prespecified in-scope gold events; overall and unknown-issuer estimates at least 90%; unknown-issuer n greater than zero in baseline code | Blinded prospective source-first sample, misses and exact detection matches; protocol bridge |
| Timing | Baseline code uses p95 at most twice the frozen polling interval | Verified availability/observation clocks, adequate strata and exclusions; importer/gate improvements |
| Untouched test | Actual receipt required | Sealed partition, custody/access records and one-time evaluation |
| Activation | Always false automatically | A later explicit operator review after all criteria; outside this task |

Before any real freeze, confirm permissions, canonical routes, budget arithmetic,
source-first enumeration, blinded custody, exact event matching, session/calendar
binding, correction history, latency evidence, untouched test handling and cost
logging. The implementation review in `WORK-PACKAGE-C.md` distinguishes fixes
actually applied from these remaining requirements.
