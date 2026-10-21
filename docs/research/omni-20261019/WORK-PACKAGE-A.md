# Work Package A — clinical numerics and source case corpus

Prepared 2026-10-19 against public revision
`d35f667aebd3c2590cabbbe80e51076b3a1fa788`.
All case labels and domain interpretations are **AI drafts pending human review**.
The research ledger is not human gold, a rights approval, or prospective pilot evidence.

## 1. Existing behavior confirmed

The exact public [PR #11](https://github.com/Rexyysilent/stock-market-bot/pull/11)
was open at the specified revision. Both its
[PR workflow](https://github.com/Rexyysilent/stock-market-bot/actions/runs/39217695137)
and [push workflow](https://github.com/Rexyysilent/stock-market-bot/actions/runs/39217677992)
were checked through their job records: all ten jobs succeeded. These are baseline
receipts, not CI receipts for this unpushed patch. `baseline.json` records the
observed public state and links `baseline-files.sha256`, whose thirteen file
hashes were recomputed from the exact public Git revision.

At that revision `classifier.extract` emits sentence-supported qualitative claims,
`evidence.span` binds exact offsets to an evidence revision, and `validator.validate`
rejects non-contract numeric fields. `clinical_context.numerical_results` is null.
`runner.analyze_documents` combines current filing exhibits. `review.record_review`
requires the exact input hash and revalidates mandatory gates. `clinical.py` stores
version-pinned registry changes; it does not establish a trial outcome from a posting.

The seed graph includes INTerpath-001 and KEYNOTE-942. It does not include PAVIA,
BEAM-302 or GM. This patch does not invent approved identity additions. A proposed
identity record needs dated primary evidence, alias scope and human review before
being added; identity membership would still be separate from acquisition coverage.

## 2. Practical gaps and corrections

1. Clinical results need a result-level scope, not just a trial ID. Population,
   endpoint, comparison, assessment time and analysis type distinguish estimates.
2. A valid exact prefix must not hide competing evidence later in its source
   sentence. Numeric validation reparses the original complete sentence.
3. Same-analysis conflicts must survive capitalization and plural time units.
   Raw spellings remain in proof spans; identity values normalize case/spacing and
   day/week/month/year morphology without equating medical synonyms.
4. A long decimal, unexpected unit, hidden statistic, duplicate scope label or
   explicit missing marker must produce abstention rather than a crash or claim.
5. Qualitative scope must not survive an intervening unrelated sentence or ignore
   an unknown NCT beside a known trial. Those leaks are closed.
6. A newly published five-year follow-up is not inherently a historical assertion.
   Explicit prior/previous/background wording still triggers historical handling.
   Reported Phase 2b can agree with registry PHASE2 while retaining the `b`.
7. The observed PAVIA wording contains “pre-specified” before “primary endpoint”.
   That bounded modifier is now recognized in both positive and negative grammar.

## 3. Verified source ledger

The complete machine-readable ledger is [source-cases.json](source-cases.json).
Each case contains the primary URL/native key, publication precision, retrieval
date, issuer/trial identifiers, section locator, paraphrase, proposed claims,
conflicts, uncertainty and `human_review_status=pending`. No source bodies are
bundled. Secondary copies are recorded as lineage, not independent votes.

| Case | Primary source and observed date | Required distinction |
|---|---|---|
| CLIN-MRNA-20260819 | [Moderna, 2026-08-19](https://news.modernatx.com/merck-and-moderna-announce-phase-3-interpath-001-trial-of-intismeran-plus-keytruda-met-endpoints-of-rfs-and-dmfs-in-melanoma) | Current Phase 3 RFS/DMFS topline; embedded HRs explicitly describe earlier Phase 2b; OS follow-up continues |
| CLIN-MRNA-20260601 | [Merck, 2026-06-01 08:00 EDT](https://www.merck.com/news/moderna-and-merck-present-5-year-data-for-intismeran-autogene-in-combination-with-keytruda-pembrolizumab-in-patients-with-high-risk-stage-iii-iv-melanoma-following-complete-resection-at-the-20/) | RFS, DMFS and exploratory OS have separate estimates; five-year descriptive analysis is not the original primary analysis |
| CLIN-EYPT-20240506 | [EyePoint, 2024-05-06](https://investors.eyepoint.bio/news-releases/news-release-details/eyepoint-pharmaceuticals-announces-topline-data-phase-2-pavia) | PAVIA primary miss coexists with favorable stability data; no verified LUGANO failure or invented post-hoc label |
| CLIN-BEAM-20260325 | [Beam, 2026-03-25 07:00 EDT](https://investors.beamtx.com/news-releases/news-release-details/beam-therapeutics-announces-compelling-updated-clinical-data/) | Pivotal expansion planned within Phase 1/2; 29 treated, 28 evaluable, 26 single-dose and 3 multidose are different populations |
| CLIN-BEAM-20260804 | [Beam, 2026-08-04 07:00 EDT](https://investors.beamtx.com/news-releases/news-release-details/beam-therapeutics-reports-second-quarter-2026-financial-results) | First pivotal-cohort dosing is reported for July, month precision; study remains Phase 1/2 |
| REG-BWXT-20260407 | [BWXT, 2026-04-07](https://investors.bwxt.com/news-releases/news-release-details/bwxt-begins-nrc-engagement-licensing-new-defense-nuclear-fuel) | Pre-application engagement; expected Q1 2027 application; no licence grant established |
| REG-BWXT-20220831-CANDIDATE | [NRC ADAMS ML22243A112](https://www.nrc.gov/docs/ML2224/ML22243A112.pdf), document dated 2022-08-31 | Different BANR project; an issuer engagement plan hosted by NRC is not an agency approval; posting date unknown |
| IDENT-GM-20230608 | [GM, 2023-06-08](https://investor.gm.com/news-releases/news-release-details/general-motors-doubles-down-commitment-unified-charging-standard) | GM announces, Tesla is a named counterpart; vendor tag payload is not available and cannot prove subject identity |
| CLIN-MRNA-HISTORICAL-P-CONTROL | Merck 2023-12-14 and 2024-06-03, linked in ledger | Nominal one-sided and two-sided p-values remain different; neither migrates into the five-year disclosure |

EyePoint's SEC accession `0000950170-24-053248` and Exhibit 99.1 confirm the
release lineage. The original [PAVIA presentation](https://eyepointpharma.com/wp-content/uploads/2024/05/Eyepoint-PAVIA-Topline_vFFF.pdf)
distinguishes baseline from nine-month evaluable denominators. The associated
[five-year KEYNOTE-942 report](https://ascopubs.org/doi/10.1200/JCO-26-00835)
provides descriptive/no-alpha and cutoff context. Registry pages support trial
identity, but unversioned current representations cannot certify historical state.

These sources are used as domain research and scoped facts. Their public
availability has not been converted into approval for body retention, review
display, model processing or downstream distribution. No permissions changed.

## 4. Concrete schema and implementation

The executable JSON schema is
`schemas/omni-clinical-numerics.schema.json`, discriminator
`omni-clinical-numerics-1`. `omni/clinical_numerics.py` contains the bounded parser.
Every retained field has `{value, support}`; support identifies the document,
revision, exact character offsets and source substring. Decimal values are strings,
so binary floating point does not alter the source number. Original precision
and spelling remain visible in the substring.

| Field group | Retained structure | Admission rule |
|---|---|---|
| Identity | trial ID, reported phase, optional indication | One explicit known trial in the same sentence; no registry-filled result values |
| Endpoint | endpoint definition/code, optional endpoint rank | One endpoint; direction remains part of a free-text definition; RFS/DMFS/OS never collapse |
| Analysis | population, treatment/comparison arms, analysis type, optional milestone | Explicit field labels or supported prose; unknown markers become null; conflicting evidence abstains |
| Time | assessment timepoint, optional median follow-up, optional date cutoff | Separate supported values; dosing time and publication time cannot fill assessment time |
| HR/CI | HR estimate, ratio unit, confidence level, lower/upper bounds | One positive finite HR; ordered interval; no default 95% interval; no HR-to-risk calculation |
| p-value | operator, decimal, nullable sidedness and adjustment | Preserve inequality and nearby clause qualifiers; no significance or endpoint-success inference |
| Counts | arm role, events/responders measure, count, denominator, denominator unit | Explicit bounded fraction and arm; no nearby-N join, rate conversion, baseline-to-evaluable substitution or percentage reconstruction |

Required result scope is trial, endpoint, population, assessment timepoint and
analysis type; HR and counts also require both comparison arms. Other absent
fields are null. Fully scoped results become attributed numeric claims with
`human_review_status=pending`. Incomplete or ambiguous candidates remain leads
with field-level missing requirements and reason codes. A successful numeric
parse does not supply the separately required primary-endpoint assertion.

The implementation deliberately accepts only auditable sentence-local grammar,
including semicolon-delimited context labels and a few explicit prose forms.
It does not yet join HTML tables, infer a label from another paragraph, resolve
all biomarker units, interpret median survival, derive absolute percentages or
handle several endpoints in a sentence. Real release prose will often abstain.
There is no claim that this patch automatically extracts every ledger case.

### Exact file/function changes

- `clinical_numerics.parse_sentence`: extract explicit scoped HR/CI/p/counts or
  retain typed abstentions. `semantic` and `result_projection` preserve result
  identity while separating evidence locations.
- `classifier.extract`: attach supported numeric claims and abstention leads;
  preserve the event's trial/origin identity; bind numeric values into revisions.
  `predicates` gains the observed prespecified-endpoint modifier.
- `validator.validate`: reparse a complete source sentence; reject changed values,
  cropped evidence, injected labels, incorrect units and changed display results;
  check same-analysis contradictions and keep mandatory human review.
- `runner.analyze_documents`: recompute the numeric projection after combining
  resumed filing exhibits, including distinct results and duplicate support paths.
- `test_omni_clinical_numerics.py`: source-inspired synthetic rules, tamper cases,
  corrections, merged exhibits and manual-review lifecycle.
- `fixtures/omni/clinical_numerics.json`: 41 explicitly synthetic rule cases linked
  to the ledger's case IDs; no fabricated gold or pilot observations.
- `scripts/run_offline_checks.py`: include the new suite in normal offline checks.

Classifier and validator discriminators advance to `omni-classifier-4` and
`omni-validator-4`. The numeric payload has its own version. Evidence/store/identity
contracts and the canonical daily schema 2.8 / pipeline 2.6.5 are preserved.
Existing pilot implementation hashes change naturally; old pilot receipts cannot
mature a gate under changed code. No schema/measurement era is silently pooled.

## 5. Acceptance and actual checks

The closing focused run passed **56 tests** across numeric, event, clinical and
acquisition modules on isolated Python 3.12.14 with both hash-locked dependency
files installed. This includes the reviewer-found case/spacing/unit conflicts,
prespecified wording and closing-bracket CI-unit counterexample. An earlier
invocation named a nonexistent `test_omni_hydration` module; it was corrected to
the existing acquisition suite and rerun successfully. Final full-suite receipts
are provided with the delivery; proposed tests are not counted as passed.
Python 3.11.17 is separately installed with the same two hash-locked requirement
sets. No live OMNI poller, model call or source body ingestion was enabled.

Acceptance criteria:

1. Every admitted scalar/qualifier resolves to its exact source revision/span;
   changing a scalar, scope, proof or display projection prevents eligibility.
2. Phase 2 numbers never appear in the Phase 3 numerical result projection.
3. Primary failure remains failure beside positive secondary/exploratory prose.
4. Ambiguous populations, endpoint ranks, arms, units, statistical scopes and
   malformed/long decimals yield a lead and never an exception or partial claim.
5. A source timestamp-only revision preserves numeric meaning; changed estimates
   require a new event revision and review. Resumed exhibits retain all results.
6. Synthetic examples and pending AI labels contribute zero human gold cases,
   zero prospective pilot observations and zero activation evidence.

Unresolved domain work: a reviewed endpoint-definition vocabulary; treatment and
cohort aliases; trial-version pinning at historical disclosure time; table row/
column/footnote evidence; p-value multiplicity methods; censored/unevaluable
population handling; safety seriousness versus severity; non-HR effect measures;
and human adjudication of each source case. The parser does not guess these.

## 6. Next bounded task

Work Package B addresses source coverage, verified common origins, correction
history, FDA route gaps and complex amount abstention. The numeric work does not
authorize broader polling. Real source-use approvals, case adjudication and
prospective observations remain operator inputs rather than generated artifacts.
