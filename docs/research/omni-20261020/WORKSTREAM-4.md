# Workstream 4: disabled vertical source adapters

Prepared 2026-10-20 against corrected local candidate `1a0ae6d2a277642e9466495323c11469f43f911a`, based on public revision `d35f667aebd3c2590cabbbe80e51076b3a1fa788`.

## Implemented outcome and limits

NRC APS metadata, pinned EIA API observations, final 2025 EIA-860 workbooks, and USAspending contract awards and transactions now have offline parsers, bounded explicit collector functions, exact source identity, draft revision handling, and executable adversarial fixtures. Every adapter remains disabled by default. Source-purpose approvals and all extracted labels remain pending human review. Nothing changes membership, exports, signals, schedules, or the existing `SourceClient.get` implementation.

The source-contract ledger distinguishes documentation checks from collection. Public official documentation and the EIA-860 distribution were inspected for schema. No NRC/EIA API key or subscription was obtained, no API collector was executed, and no real source cases became gold or prospective pilot evidence. Synthetic field names in the EIA tests are deliberately labelled synthetic; they are not assertions about the real nuclear-outages route.

## Requirement, implementation and test map

| Requirement | Files and entry points | Executable coverage |
|---|---|---|
| Narrow approved read-only requests | `omni/transport.py`: `ReadOnlyJSONClient.read_json`, bounded request validators | D-V18, D-V19; route/method/body/query, typed attestation, revocation/expiry, DNS, redirect, byte cap, retry/backoff and document-budget tests |
| NRC source identity, dates and licensing stages | `omni/verticals/nrc.py`: `parse_nrc_metadata`, `licensing_stages`, `licensing_paragraphs`, `hydration_status` | D-V01–04; separate stages/negation, metadata body removal, storage-purpose rejection |
| NRC bounded pagination and durable continuation intent | `collect_nrc_aps` | Repeated page, cap, checkpoint query mismatch; default collector makes no requests |
| EIA pinned metadata, exact decimals and revised observations | `omni/verticals/eia.py`: `pin_metadata`, `parse_eia_observations`, `collect_eia` | D-V06, D-V12–14; metadata/hash/version/unit/native identity mismatch, explicit identity sorting, caps and budget continuation |
| EIA-860 native plant/generator/operator/owner identity and vintage | `omni/verticals/eia860.py`: `parse_eia860_bundle` | D-V07–09; full source versus truncated/missing owner file, exact Excel percentage format, invalid percentages/formulas/vintage |
| Dated identity and lineage proofs | `omni/verticals/common.py`: `validate_crosswalk`, `mapping_visibility`, `origin_count`, `checked_stakes` | D-V05, D-V09–11; unknown registered entity, forged pointer, truthy lineage proof, exact reviewed correspondence |
| Delayed procurement, separate signed obligation/aggregate | `omni/verticals/procurement.py`: `parse_awards`, `parse_transactions`, `collect_procurement`, `recipient_identity` | D-V10, D-V15, D-V20; per-award page continuation, incomplete correction coverage, unknown currency/parent |
| Financing authority remains separate from cash | Workstream 2 `omni.amounts.parse_amount_sentence`, consumed by the vertical integration test | D-V16; bounded financing authority with unknown cash/runway |
| Backlog and immutable draft receipts | `common.deferred_lead`, `common.persist_batch` | D-V17; D-V14 replay/idempotent append and old revision retrieval |
| Read-only operator workflow | `omni/verticals/cli.py`: `register_commands`, `dispatch`, `adapter_plan`; standalone `omni.verticals` | Disabled plan and parser functions; local CLI smoke receipt |

The test suite is `test_omni_verticals.py`. Its 20 named D-V tests consume the existing proposal fixture manifest and construct synthetic official-shape payloads or OOXML archives. Additional tests target contract boundaries. Test executions, exact commands, platform and exit status are recorded in `vertical-test-receipt.json`; captured output is adjacent. These results do not estimate real-provider recall, latency, availability, cost or user value.

Verification correction recorded during Workstream 5: the original exit-zero
secret scan ran before the new Workstream 4 files were git-tracked and therefore
did not cover those then-untracked files. The later staged scan found only exact
public/file hashes, the execution path and an explicit synthetic redaction canary.
Its failure output is preserved. Audit hashes now use adjacent plain-text
manifests referenced by the JSON ledgers, and public contract pins remain in full
official source URLs. The final staged scan and repeated vertical/GET controls
are recorded in `expansion-test-receipt.json`. No scanner rule or baseline changed.

## Transport and approval contract

Each request requires an exact source route and `acquire` purpose decision, followed by one exact `read_only_requests` grant:

```json
{
  "contract_id": "usa-transactions",
  "contract_hash": "digest of the current shipped transport contract",
  "method": "POST",
  "url": "https://api.usaspending.gov/api/v2/transactions/",
  "approval_ref": "an existing immutable operator_attestation receipt"
}
```

The referenced receipt is validated with purpose `vertical_transport_contract`, subject hash equal to the grant without `approval_ref`, and bindings `source_policy_id` plus `source_policy_fingerprint`. For this approval binding only, the fingerprint covers the policy excluding `read_only_requests`, avoiding a circular receipt hash. The request/candidate provenance separately fingerprints the full policy. Any changed policy purpose, route, version, current contract, expired/revoked approval, missing reference or mismatched subject rejects before DNS or quota use. Production rejects synthetic attestations; tests opt into them explicitly.

Credentials enter only through an injected lookup of an opaque local handle. NRC uses the documented subscription header; EIA uses the documented query key. Caller-supplied credential query fields are rejected. Returned payloads and receipts redact named credential fields, exact values and URL-encoded echoes. Error paths suppress request exceptions containing secrets. No credential value is persisted. A key reference is not a source approval.

Search and observation requests consume the existing metadata and attempt budgets. NRC document retrieval consumes the document budget and additionally requires `store_text`. Redirects are refused, DNS destinations must be public, retries are bounded, throttle state is durable, and response bytes are capped. NRC search removes indexed text before returning the metadata envelope. JSON POST is supported only for the verified NRC and USAspending search contracts; EIA uses documented GET routes.

## Extraction decisions

### NRC

The [APS API guide, version 1.0](https://adams-search.nrc.gov/assets/APS-API-Guide.pdf) verifies the search and document methods, exact metadata properties, filter syntax and subscription header. Document date, addition date and local observation time stay separate. The addition timestamp example has no timezone; its raw value is retained and cannot produce a precise availability or latency claim. Search counts change during the day and the guide warns about truncation. A bounded scan therefore retains continuation and coverage debt without declaring a stable complete interval.

Metadata establishes an accession and docket lead, not project identity or permission to operate. Stage mentions have exact local text spans and separate intent, pre-application, submission, acceptance, technical/environmental review, hearing and decision categories. A stage mention never auto-approves a license decision. Document-body unavailability leaves a hydration backlog.

### EIA API and EIA-860

[EIA API documentation](https://www.eia.gov/opendata/documentation.php) verifies metadata-driven fields/units, explicit sorting and offsets, the 5,000-row JSON limit, request echo and numeric strings. The [official OpenAPI distribution](https://www.eia.gov/opendata/eia-api-swagger.zip) verifies the facility nuclear metadata/data routes and GET support. The actual nuclear facet identifiers and units still require an operator-supplied route metadata snapshot; this work does not guess them. API version, metadata hash, source period and native facets bind each candidate. Revised periods append new revisions; missing/nonfinite numbers remain unknown and row units cannot override metadata.

The [EIA-860 source description](https://www.eia.gov/electricity/data/eia860/) documents separate generator and owner tables and the owner-file absence rule. The linked final [2025 distribution](https://www.eia.gov/electricity/data/eia860/xls/eia8602025.zip) was inspected to verify exact workbook names, sheet names, headers and cell units. `Percent Owned` is a numeric Excel ratio with a percentage display format; the parser requires and preserves that format before multiplying by 100. It does not infer units from the header alone.

The reader accepts only the verified final-2025 layout, rejects unsupported XML/archive/formula structures, and limits compressed bytes, expanded bytes and rows. An absent owner can imply a **draft 100% stake at the dataset vintage only** when the entire correctly headed matching bundle was read, generator/operator identity matches, and no relevant identity conflict remains. A caller's boolean `complete` flag is not sufficient. Explicit owner shares and operator identity remain separate. Annual vintage does not prove the precise effective date of a sale, current ownership or listed-company parentage.

### USAspending

The official [award search contract at the inspected revision](https://github.com/fedspendingtransparency/usaspending-api/blob/a31240757e5987b994692322c317ae55668c6983/usaspending_api/api_contracts/contracts/v2/search/spending_by_award.md) and [transaction contract](https://github.com/fedspendingtransparency/usaspending-api/blob/277763cd72dc9e5838f52ec4176edbfef79fd5e6/usaspending_api/api_contracts/contracts/v2/transactions.md) verify both read-only POST endpoints. The implementation accepts only contract award types A–D and bounded explicit queries. Award-family ID, transaction ID and modification are preserved. Award aggregates never manufacture a new transaction. Signed obligation changes never establish revenue, proceeds or option exercise; unknown currency remains explicit.

The [official data guide](https://www.usaspending.gov/data/about-the-data-download.pdf), pages 11 and 15, describes reporting/publication delays and the 90-day DOD/USACE delay. These facts establish that the feed is delayed; they do not establish a particular record's public arrival time. Action dates, source modification dates and local observations therefore remain separate. Update/deletion coverage is unresolved even after every reported page in one query has been acquired.

## Integration and operator next steps

The shared OMNI CLI can call `register_commands(subparsers)` and route `COMMANDS` to `dispatch(args, store=store)`. Standalone examples:

```sh
python -m omni.verticals vertical-plan
python -m omni.verticals vertical-parse nrc-aps local.json --observed-at 2026-10-20T12:00:00Z --policy-fingerprint locally-recorded-policy-hash
python -m omni.verticals --db local-drafts.db vertical-parse eia860 local-2025.zip --observed-at 2026-10-20T12:00:00Z --policy-fingerprint locally-recorded-policy-hash --persist
```

Local parsing produces unverified local input by default. It cannot label data prospective. `persist_batch` appends `vertical_candidate` and `vertical_batch` receipts through the shared immutable store. These are draft review inputs, not validated editorial events. The common origin helper requires exact source-backed unit/period/revision proofs and a typed `vertical_origin_correspondence` attestation before collapsing duplicate hosts; it never declares independence.

An operator must still approve source purposes and exact transport contracts, provide credential handles where needed, capture real metadata/bundle provenance, review identity and origin correspondence, and collect genuinely prospective evidence before activation or pilot claims. Any new vintage, currency assumption, route, field grammar, automatic scheduling or membership expansion requires its own reviewed contract and evidence. No such approval is represented by this implementation.
