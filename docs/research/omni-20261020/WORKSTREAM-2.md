# Workstream 2 — clinical scope and scoped amounts

Implementation date: 2026-10-20. This stage starts from corrected candidate
`1a0ae6d2a277642e9466495323c11469f43f911a`, tree
`0208cc4400358c05956f63ddeb1b84f4ca00dcc3`, directly on public base
`d35f667aebd3c2590cabbbe80e51076b3a1fa788`.

This is offline engineering and synthetic verification. All nine real source
cases in `docs/research/omni-20261019/source-cases.json` remain unchanged AI drafts.
No human pilot, source-purpose approval, membership change or activation is
created. The two supplied numeric-parser repairs and their nine negative
controls remain in `test_omni_clinical_numerics.py`.

## Implemented contracts

`omni.clinical_scope` adds optional `omni-clinical-scope-catalog-1` records. Each
trial version binds a native source ID, exact archived document revisions, source
policy snapshots, literal version dates, trial identity and phase, endpoint
labels/definitions/ranks, arm roles and aliases, populations and denominator
kinds, and cohort definitions/aliases. Every material value has an exact support
span. A row needs a matching version hash and an exact-input review receipt;
pending, malformed, stale-policy, overlapping and unsupported records cannot
supply a mapping. A competing pending version blocks reuse of an older mapping.
The software validates receipt structure and hashes; it does not authenticate a
human, establish source authenticity or grant legal permission.

Selection uses source disclosure precision and version availability, independently
of local observation time. A date-only source cannot prove an earlier time on
that day. A version must cover the whole disclosed time interval. Later aliases
and future source versions cannot be silently projected into historical results.
Canonical result identity includes the version and canonical endpoint, population,
arm and cohort IDs. Approved aliases cannot hide contradictory estimates under
different spellings. Endpoint rank and arm direction are checked, and an observed
phase is checked against the disclosure-time version rather than the current
registry's phase alone. Missing rank, population, time or arms is never filled
from a catalog.

The optional catalog is supplied by `EntityRegistry(data, clinical_catalog=...)`
or `load_registry(clinical_catalog_path=...)`; it is included in the registry
fingerprint. An identity JSON file may also contain `clinical_catalog`. No catalog
is installed by default. The existing literal v1 parser still records source
scope without asserting a canonical endpoint definition or treatment equivalence.
A requested catalog that cannot establish a mapping causes abstention; it does
not fall back to an unreviewed mapping.

`omni-clinical-numerics-2` adds exact risk-ratio and odds-ratio estimates, risk
differences explicitly reported in percentage points (including signed bounds),
CI sidedness, named Holm/Bonferroni adjustment in the supported prefix grammar,
explicit `multiplicity: no alpha allocated` or `multiplicity: not adjusted for
multiplicity`, cohort scope, catalog bindings and table proof. No statistic implies
significance, endpoint success, survival benefit or approval. The v1 schema remains
readable and rejects v2-only fields falsely labeled as v1.

`omni.clinical_tables.parse_tables` accepts only complete plain-text pipe tables.
Every row repeats trial, endpoint, population, treatment, comparator, timepoint,
analysis type and a self-describing statistics cell. Optional phase, rank, cohort
and multiplicity columns are supported. Each cell and header is revision-bound;
all rows and the full block are retained as proof. Blank/merged cells, duplicate or
unknown headers, missing scope, footnotes, markup and malformed rows refuse the
whole table. The validator regenerates the complete original table, so cropping a
proof cannot erase a competing row or footnote. A table/prose proof-form change
alone does not create a new numeric semantic identity.

`omni-scoped-amount-1` records source currency, an exact decimal string, operator,
role, optional contract/financing ID, period, contingency, additivity and its
source target, option status, obligation kind, and exact evidence revision. Values
remain separate atoms: ceiling, option, obligation, financing authority, gross
proceeds and net proceeds. Included and explicitly incremental options keep their
relationship without a total. Current obligations, transaction changes and
reported deobligations have separate kinds. A positive reported deobligation is
not silently converted into a negative cash number. Condition text cannot turn
conditional exercise into an exercised option or assign a period to the amount.

Supported operators are equal, `<`, `<=`, `>`, `>=`, approximate and range. Bounds
and ranges have null exact value and retain lower/upper fields. Approximate values
retain the approximate operator. Bare dollar or pound signs keep currency unknown;
missing period or identity stays null. ISO currency tokens and `US$` are supported,
with decimal and explicit thousand/million/billion scales. Per-day/week/month/year,
explicit duration and `through` year/month/date preserve their own precision.
The parser never aggregates, converts currency, annualizes or infers cash received,
revenue or runway. Those inferred fields are always null.

The exact-USD legacy control remains, with tighter refusals for bounds and
unestablished realization. In particular, the old path no longer turns `at least`,
`at most` or `up to` into an exact scalar. The unambiguous exact USD ceiling,
option and obligation controls still pass. Expected/projected/planned realization,
unconsumed money/qualifiers, negation and unsupported arithmetic remain leads.

## Integration and dependency hooks

The classifier/validator versions advance to `omni-classifier-5` and
`omni-validator-5`. The identity registry's existing v1 format remains compatible;
the optional catalog changes its fingerprint. No database migration is required
by this stage. Archives and old review receipts are not rewritten. Re-analysis
uses the new parser/validator versions and exact current inputs; it is not an
approval migration. Rolling back code does not rewrite a newer archived event.

| Hook | Contract for root integration |
| --- | --- |
| `amounts.parse_amount_sentence(doc,start,end)` | Returns `atoms` and explicit `abstentions` from a complete native sentence. |
| `amounts.amount_projection(claims)` | Deterministic unique `amount_atom` payloads for predicate `scoped_amount`; no arithmetic. |
| `classifier.extract` | Adds `amount_atom` claims and `event.scoped_amounts`; numeric v1/v2 results share the existing numeric projection. |
| `validator.validate` | Re-parses complete source sentences/tables, checks every payload/projection, separates amount scopes and verifies current definition-source policies. |
| `clinical_scope.binding_documents(binding,registry)` | Resolves exact archived definition revisions, separately from result publication/recency. |
| `clinical_scope.referenced_documents(event,registry)` | Reads `event.claims[*].numeric_result.scope_binding`; deduplicates definition documents by evidence ID and revision. |
| `scope_binding.evidence_sources` | Native ID, URL, evidence ID/revision and policy ID/fingerprint for each definition source; this is attribution, not a rights grant. |

Root-owned runner/store/review/CLI files are deliberately not edited in this
branch. Integration must recompute `event.scoped_amounts` after merged/resumed
exhibits, expose the scoped atoms on cards, ingest definition documents as separate
dependencies, include them in correction/quarantine lineage, and enforce their
own field/display rights. Definition documents must not be subjected to the
current-result recency cutoff. An optional CLI catalog argument should pass its
path to every relevant registry load. These hooks are supplied for that integration;
the isolated WS2 branch does not claim the root-owned wiring is already complete.

## Executable acceptance map

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Versioned endpoint/arm/cohort contracts and disclosure-time versions | `clinical_scope.checked_versions`, `bind_scope`, `binding_identity`; `identities.EntityRegistry` | Pending/stale/tampered/overlapping versions; late availability; changed definition; unknown aliases; swapped arms; rank/phase mismatch; canonical-alias identity. |
| Source-policy and definition provenance | `binding_documents`, `referenced_documents`; validator definition checks | Missing/revoked definition policy, separate dependencies and historical definition recency. |
| Table row/column/footnote boundaries | `clinical_tables.parse_tables` | Whole-block refusal, duplicate/missing headers, blank/incorrect cells, footnotes, repeated/conflicting rows, cropped or tampered proofs. |
| Multiplicity, disposition, safety and non-HR effects | `clinical_numerics.parse_sentence` | Named prefix adjustment, no-alpha statement, CI side, signed difference units; explicit refusals for unknown methods, censoring, unevaluable populations and severity/seriousness. |
| Primary/secondary, phase, population, history and negation | Existing and extended classifier/validator paths | Existing A controls retained; nine supplied repaired examples; new scope limits/negated statistic and adjacent disposition caveats. |
| Complex amount atoms | `amounts.parse_amount_sentence`, `amount_semantic` | All 12 original amount scenarios have declared new outcomes; 28 additional fixture cases; exact support/schema tests. |
| Bounds, roles and unknowns | Amount contract and independent validator reparse | Exact/approximate/range/bounds, current/change/reduction obligations, included/incremental options, gross/net/authority, missing currency/period/identity. |
| Projection and revision consistency | Classifier/validator + pure projections | Payload/display tampering, cropped sentence, stale revision, duplicate projection, same-scope conflict, separate-role outcomes. Root-owned resumed amount/card wiring remains an integration hook. |

The machine-readable map is `WORKSTREAM-2-requirements.json`. The new clinical
fixture corpus has 22 scope/form rules; the amount fixtures have 12 retained
legacy comparisons plus 28 additional cases. Synthetic approvals exist only in
test builders and cannot be used for a non-synthetic result document. No real
source case is promoted into gold data.

## Deliberately unsupported forms and missing contracts

- General natural-language resolution, HTML/PDF table reconstruction, row/column
  spans, inherited headers/populations, footnotes, and numeric values with units
  split across cells. These require a separately evidenced structural adapter.
- Censoring, unevaluable/missing assessments, changing risk sets and recurrent
  event rates. These need disposition denominators, their units, assessment time
  and a prespecified analysis rule; the parser does not fill them from enrollment.
- Severity grades, seriousness, causality, SAE/TEAE counts and dose-cohort safety
  transfers. These need separate safety definitions and denominator/cohort scope.
- Arbitrary effect names/abbreviations, model-adjusted effects, transformed or
  approximate statistics, median survival, log-rank/Wald/Cox/Fisher tests, and
  unimplemented multiplicity procedures. Only the declared grammar is admitted.
- Postposed or contradictory p qualifiers and compound/negated analysis labels
  remain explicit refusals. Neighboring disposition/no-alpha caveats within the
  paragraph also block a cropped standalone numeric sentence.
- Money tables, shared-currency range shorthand, `m/bn/k` scales, conversions,
  inferred totals, non-time unit rates, unknown conditional associations and
  unsupported realization wording. Labels with multiple contract IDs in one
  sentence abstain rather than distribute amounts across identities.
- The source-backed real endpoint dictionary, actual operator review identities,
  source-purpose approvals and real pilot/adjudication evidence remain absent.
  The catalog and validators are implemented; their real approved inputs are not.

## Verification receipts

Receipts under `receipts/ws2-*` record exact commands, Python/platform versions,
stdout, exit codes and the code/fixture/schema hashes tested. Both available locked
Linux Python environments passed the 84 focused tests: Python 3.11.17 (4.855 s)
and Python 3.12.14 (6.787 s). These focused WS2 checks are separate from
the root's final combined-candidate release gates. The old unavailable runtime's
exit 127 is descriptive environment history and supplies no passing evidence.
The previous 73-script Windows runs predated the two supplied repairs and do not
verify this new candidate.

Exact test-input and stdout hashes are stored in `receipts/ws2-tested-inputs.sha256`
and `receipts/ws2-result-artifacts.sha256`, with public Git IDs in
`receipts/ws2-public-git-objects.txt`. The initial secret scan correctly ran but
flagged these public hashes when serialized as bare JSON strings. Its failed
receipt is preserved. Moving hashes to explicit checksum manifests changes no
scanner rule, source approval, runtime input or test result.

The final static receipt records successful export-schema validation, JavaScript
syntax validation, staged whitespace checks and the unchanged secret scanner.
It also verifies all 43 tested input hashes and all three result/source-ledger
hashes. `receipts/ws2-changed-files.sha256` covers every returned changed file
except that manifest itself; Git binds the manifest in the local commit.
