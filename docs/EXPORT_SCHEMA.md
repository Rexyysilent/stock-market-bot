# Export Schema

`export_for_gemini.py` writes `daily_brief.json` for local research consumers and
dashboards. Model processing and redistribution require separate source-purpose
approval; availability in an export is not a grant of those rights.

This is a descriptive data contract. Legacy field names containing `signal` or
`alert` are compatibility identifiers for deterministic classifications; they do
not represent recommendations, trade direction, or inferred intent.
Top-level fields:

- `generated_at` - ISO timestamp for the export
- `schema_version` - public JSON contract (`2.8`)
- `pipeline_version` - upstream logic era (`2.6.3`); ledger statistics never pool eras
- `run_context` - immutable UTC/NYSE clock shared by all stages, including
  market state and the latest completed/settled session
- `pipeline_time_seconds` - total runtime
- `health` - source quality and failure metadata
- `summary` - high-level counts
- `sections` - structured data payloads
- `data_quality` - audit metadata, including additive headline and editorial-
  focus sub-contract discriminators described below

## Tiered universe contract (schema 2.8)

The export deliberately separates editorial coverage from instrumentation and
signal eligibility:

- `universe.tickers` is the ordered 42-name editorial coverage set.
- `universe.instrumented_tickers` is the existing ordered 29-name acquisition
  and signal-eligible set.
- `universe.editorial_only_tickers` is the ordered 13-name expansion:
  `MRNA`, `VRTX`, `BEAM`, `RARE`, `CEG`, `LEU`, `BWXT`, `GEV`, `MP`, `NVDA`,
  `RKLB`, `HOOD`, and appended `MRK`.
- `universe.focus_eligible_tickers` is the 29-name set in `off` and `shadow`,
  and the 42-name coverage set only in `active`.
- `universe.version` and `universe.instrumented_version` are the first eight
  hexadecimal characters of SHA-256 over the exact comma-joined ordered sets.
  For this rollout they are `a6277806` and `df0f60af`, respectively.

The three ordered sets must be duplicate-free, the 29 and 13 partitions must
be disjoint, and the 42-name list must equal their concatenation. The legacy
`ALL_TICKERS` symbol remains an alias for the 29-name set; it does not mean
editorial coverage.

Every selected or dropped headline carries:

- `coverage_tier`: `instrumented`, `editorial_only`, `mixed`, or null;
- `signal_eligible`: a boolean; and
- `signal_eligibility_reason`: `instrumented_universe`,
  `editorial_only_coverage`, `mixed_coverage_requires_ticker_filter`, or
  `no_mapped_ticker`.

Mixed records fail closed. In `off`/`shadow`, a selected mixed story exposes
only its core `universe_tickers` projection and instrumented metadata; raw
provider `tickers` remain provenance and cannot establish eligibility.
Editorial-only focus records always carry `signal_eligible=false` even in
active mode. Focus tier/eligibility metadata is repeated in `universe` so
consumers need not infer it from the narrative object.

`EDITORIAL_COVERAGE_MODE` accepts `off`, `shadow`, or `active` and defaults to
`shadow`. Shadow exports add this bounded typed structure:

```json
{
  "mode": "shadow",
  "candidate_count": 1,
  "fresh_candidate_count": 1,
  "records": [{
    "source_record_id": "provider:record",
    "tickers": ["MRNA"],
    "title": "Material development",
    "link": "https://issuer.example/development",
    "as_of": "2026-08-20T10:00:00Z",
    "lane": "universe",
    "score_components": {
      "issuer_relevance": 2,
      "macro_relevance": 0,
      "vertical_relevance": 1,
      "authority": 3,
      "novelty": 1,
      "impact": 3
    },
    "disposition": "suppressed",
    "reason": "editorial_coverage_shadow_mode"
  }]
}
```

`records` is capped at 10. Its ticker arrays are sorted, nonempty, unique, and
editorial-only. A `suppressed` record must resolve by source ID, title, link,
and time to an `editorial_shadow_suppressed` dropped headline with editorial
mapping. A `core_projection` record uses
`reason=editorial_mapping_shadowed` and must resolve to a selected or dropped
core-only terminal headline. Source IDs cannot repeat. Outside shadow mode the
counts are zero and `records` is empty.

Schema 2.8 also allows `legacy_projection` with `editorial_mapping_shadowed`:
the terminal retains the off-mode interpretation with no new universe mapping.
Thus an already-admissible outside-universe discovery story survives shadow.
Production selection is recomputed against the core interpretation of identical
raw inputs; expanded interpretation feeds diagnostics only. Ambiguous repeated
source-ID examples are omitted from the bounded audit sample, not from candidate
counts or terminal provenance rows.

`universe.coverage_policy` carries cohort `editorial-42-v2`, registry
`issuer-capabilities-1`, mapping `issuer-mapping-2`, source policy
`source-purpose-1`, their fingerprints, manual activation, zero approved cloud
spend, unapproved uses and actual collector targets. It is checked against the
versioned policy, not trusted as a self-declared eligibility override.
Capabilities distinguish configured target from verified availability. Funds
are not blanket-excluded from SEC lookup; unverified capabilities remain unknown.

A current export carries these exact markers:

- `universe_contract_version=2.8-tiered-universe-1`
- `headline_contract_version=2.7-headline-lanes-1`
- `focus_contract_version=2.7-editorial-focus-1`
- `signal_eligibility_audit_version=2.7-signal-eligibility-1`

Before publication, a defense-in-depth audit rejects outside-core tickers in
signal, confluence, state-derived alert, ledger-facing, and instrumented
measurement structures. Raw discovery/context remains permitted: non-universe
social-attention rows, unalerted clinical/FDA research rows, FDA meeting and
cash-runway context, and non-signal CEO.ca context are not reclassified as
signals merely because they name another issuer. Once any such row carries its
signal/alert/eligibility marker, the 29-name allowlist applies.

Historical exports are not rewritten. Validation dispatches on the document's
`schema_version`: `schemas/daily_brief.schema.json` is the current 2.8
contract; `schemas/daily_brief.2.7.schema.json` preserves the exact 41-name schema,
and `schemas/daily_brief.2.6.schema.json` remains immutable. The current-2.7
fixture is also preserved. Unknown versions fail validation.
`fixtures/exports/daily_brief.current-2.6.json` remains a marked
2.6.2 compatibility artifact. The 2.6.3 era starts clean version-suffixed
social-history and ClinicalTrials.gov snapshot state; earlier unversioned state
is retained for rollback, and ledger history is not backfilled.

OMNI-01 does not reset any 2.6.3 state, backfill the ledger or change signal
eligibility. Off/shadow FMP payloads are identical (27 symbols); active uses 40.
The 402 fallback remains a separately counted, narrower second request.
Rights-unknown metadata never authorizes new model or redistribution purposes.

Activation remains manual and unproven: require at least ten eligible scheduled
runs plus meaningful labeled issuer cases, zero critical mapping/eligibility
leakage, schema-valid byte-identical copies and measured runtime no more than
15% above the prior successful baseline. Optional-source degradation is reported
separately; mandatory-source gaps pause affected gates. This release does not
implement a watcher, automatic counter or auto-promotion.

Duplicate-evidence correction: a discarded identical occurrence may share the
selected representative's source ID only when its duplicate reason, kept-source
ID, title, canonical URL, provider, publisher, source class, time kind and source
time agree. Any rejected-only, stale or conflicting occurrence still blocks focus.
This semantic bug fix does not rewrite archived documents or waive freshness.

## Health

The health object is designed to answer: "Can I trust today's dump?"

```json
{
  "status": "OK | WARN | ERROR",
  "generated_at": "2026-07-27T21:30:00Z",
  "pipeline_time_seconds": 42.1,
  "warnings": [],
  "errors": [],
  "sources": {
    "social": {},
    "social_agent": {},
    "openinsider": {},
    "sec_edgar": {},
    "twitter": {},
    "options_flow": {},
    "earnings_calendar": {},
    "pipeline_stages": {
      "failed": []
    }
  }
}
```

Common `WARN` reasons:

- A configured headline provider failed, lane-scoped publisher/provider
  diversity selection underfilled, the raw pool was empty/stale, or Google
  News required its targeted alternate query
- OpenInsider live acquisition failed, or only stale/invalid rows were available
- SEC EDGAR had retries or failed calls after retries
- Reddit/RSS social fetches returned 403/429/5xx
- Twitter fell back to slower Google News proxy data or only a subset of
  configured Nitter accounts returned usable feeds
- Options or earnings coverage is incomplete

### OpenInsider acquisition and evidence policy

Each export owns one run-scoped OpenInsider acquisition: transaction filter
`ALL`, a 30-calendar-day lookback, and a maximum of 500 rows. The social
renderer takes at most five recent rows from that normalized result, while the
SEC stage derives watchlist insider clusters from the same in-memory rows. The
pipeline does not issue a second OpenInsider request for either consumer.

The acquisition reuses one `requests.Session` and one source-level rate gate.
Only connection failures and timeouts receive up to three retries, with
exponential delays and jitter (approximately 2, 5, and 10 seconds). Health
classifies failures rather than flattening them into an empty result; expected
reasons include `connection_refused`, `connection_error`, `timeout`,
`http_403`, `http_429`, other `http_<status>`, `parser_failed`,
`untrusted_response_url`, and `unexpected_error`.
After bounded HTTPS retries end in `connection_refused`, the source may make
exactly one request to `http://openinsider.com/screener` on port 80. This is an
availability-only degradation: a successful plaintext parse has
`status=WARN`, `run_origin=insecure_http`, `transport_scheme=http`, and
`transport_secure=false`. It is not a live/trusted acquisition.

The public collector disables automatic redirects for both transports. A 3xx
response is a source failure (`http_<status>`, or `http_fallback_http_<status>`),
not permission to contact the `Location` destination. SEC EDGAR fallback and
stale-cache exclusion still apply.

The single acquisition record is exported under `health.sources.openinsider`.
Its transport/run fields include `failure_reason`, `attempts`, `retries`,
`retry_delays_seconds`, `transport_scheme`, `transport_secure`,
`https_failure_reason`, `https_error`, `http_fallback_attempted`,
`http_fallback_used`, `http_fallback_attempts`, `live`, `run_origin`, and
`scope`. `undated_rows`
counts missing source dates without deleting those canonical rows. In pipeline
2.6.2, `future_rows_dropped` and `out_of_window_rows_dropped` remain zero:
the acquisition deliberately preserves dated rows so the SEC consumer can
apply and audit cluster eligibility in one place. They are reserved
source-normalization counters, not the cluster-exclusion totals.

Cache provenance is reported independently as `cache_used`, `cache_status`,
`cache_path`, `cache_fetched_at`, `cache_age_days`, and `cache_error`. The
daily `ALL`/30-day/500-row scope uses `state/openinsider_last_good.json`;
nondefault scopes derive separate scope-safe filenames so a deeper or
ticker-specific acquisition cannot overwrite the daily cache. A fallback run
has `run_origin=stale_cache`. SEC health exposes
`openinsider_cluster_rows_considered`, `openinsider_cluster_rows_eligible`,
`openinsider_cluster_rows_dropped` (including `stale`,
`insecure_transport`, `undated`, `future`, and `out_of_window`; these are the
authoritative eligibility exclusions),
`insider_cluster_source`,
`insider_cluster_fallback_used`, and `insider_cluster_fallback_reason`. This
reports cluster eligibility and fallback without maintaining a contradictory
second OpenInsider acquisition status. The fallback reason is one of
`insecure_http_disallowed`, `stale_cache_disallowed`, `source_unavailable`,
`no_temporally_eligible_rows`, `no_watchlist_rows`, or
`no_qualifying_clusters` when fallback is used.

A successful live parse replaces the atomic last-known-good row cache. If the
live acquisition fails, `state/openinsider_last_good.json` may supply cached
rows. They are marked stale and may be rendered only as narrative context.
A successful plaintext HTTP parse is never written to that trusted cache. Its
rows carry `source_transport_scheme=http`, `source_transport_secure=false`,
and `signal_eligible=false`, and render as `OpenInsider/HTTP-INSECURE`.
Cached, plaintext, undated, future-dated, and out-of-window rows are ineligible
for OpenInsider cluster constituents and therefore cannot enter signals,
state, the ledger, or confluence. The SEC EDGAR Form 4 scan remains the cluster
fallback; the health
output distinguishes OpenInsider failure/eligibility loss from EDGAR fallback
success.

An unexpected top-level stage exception produces `ERROR`, appears under
`health.errors` and `health.sources.pipeline_stages.failed`, and causes a
non-zero exit after the diagnostic artifact is archived. Mutable signal state
is rolled back so a failed run cannot consume an episode or baseline slot.

## Sections

Important section keys:

- `confluence` — FIRST section on purpose: emitted only when >=2 distinct
  alert families (social / options / insider / technical / catalyst) hit the
  same universe ticker inside an exact trailing 48-hour UTC window
  (`state/alert_history.json`). Events require a source or honest observation
  timestamp and are de-duplicated by ticker/family/tag/event time; a rerun
  cannot refresh stale evidence. Fields: ticker, families, alerts
  (family/tag/detail/event_at), confluence_score, first_seen, and as_of.
  Intentionally sparse; narrative whispers are excluded by design
- `headlines` — up to 5, relevance-filtered against universe tickers/aliases,
  macro themes, and strong broad-market anchors (S&P 500, Nasdaq, Dow,
  Russell 2000, Wall Street, futures, oil). Bare "stock market" remains score
  zero so utility filler is excluded. Bare corporate `treasury` usage is
  also zero; only U.S. Treasury, Treasury Department, or bond/yield/auction
  context scores as macro evidence. Each row retains:
  - `provider`: acquisition path (Official Feeds, FMP, Alpha Vantage News,
    GDELT, or Google News)
  - `publisher` / `publisher_domain`: underlying newsroom used for lane-scoped
    concentration caps; acquisition-provider caps are lane-scoped too
  - `source_class`, `source_record_id`, and `canonical_url`
  - `published`, `observed_at`, and `source_time_kind`. The adapter-internal
    `provider_seen_at` is mapped differently by evidence type: for sources
    with a publisher/event time, that time becomes public `as_of` and the
    acquisition time becomes `observed_at`; for GDELT, `seendate` becomes
    public `as_of` with `source_time_kind=provider_seen` and `observed_at`
    remains null, so an index time is never claimed as publisher time
  - `duplicate_providers` / `duplicate_publishers` when one selected row
    collapsed syndicated copies
  - `lane`: `universe`, `macro`, or `discovery`
  - `tickers`: raw symbols supplied by the acquisition provider. A raw tag may
    inform resolved universe mapping only when `ticker_metadata_kind=subject`,
    or when an older record omits the qualifier and follows the legacy default.
    A `related` tag never establishes issuer identity and cannot by itself make
    a story eligible for the `universe` lane
  - optional `ticker_metadata_kind`: the provider's classification of those raw
    tags as the story's `subject` or merely `related`. Older/provider-native
    records that do not supply this distinction omit the field
  - `universe_tickers`: configured tickers mapped by explicit ticker,
    eligible provider metadata, or issuer alias. This resolved list, not raw
    `tickers`, is the authoritative universe mapping used by editorial selection
  - `score_components`: separate nonnegative `issuer_relevance`,
    `macro_relevance`, `vertical_relevance`, `authority`, `novelty`, and
    `impact` measurements. `novelty` measures uniqueness within the current
    fetched pool; it is not longitudinal and does not claim historical newness
  `source` remains a compatibility alias for `provider`.
  Headline `record_id` remains based on legacy `text`; additive provenance
  does not rewrite the identity contract for prior consumers.
- `prices`
- `technicals`
- `options_flow.<ticker>.option_contract_volume_oi_anomaly` - contracts whose
  actual `lastTradeDate` belongs to `run_context.latest_completed_session` and
  crosses volume/OI, DTE, and estimated-notional thresholds. `premium` remains
  a compatibility alias for `lastPrice * completed-session volume * 100`; it
  is not paid premium, aggressor-side flow, or evidence that a CALL is bullish
  or a PUT is bearish
- `instrument_relative_return_spread` - configured pair return spreads with a sterile `threshold_crossed` flag
- `retail_contrarian`
- `sec_filings`
- `pdufa_catalysts`
- `cash_runway_alerts`
- `insider_clusters` — OpenInsider constituents require a parseable source
  filing date no later than the run clock and inside the exact trailing
  30-calendar-day window. Undated, future-dated, out-of-window, and cached
  rows are excluded before clustering. If no eligible live OpenInsider cluster
  remains, the SEC EDGAR Form 4 path runs as the fallback. Eligibility/drop
  counts and the fallback reason remain visible in source health.
- `options_flow`
- `sector_rotation`
- `earnings_calendar`
- `ceo_ca_signals` (CEO.ca provenance and content strength are separate:
  `source_record_verified` means a direct API row carried both a post ID and
  source timestamp; `threshold_qualified` means the text crossed the
  deterministic geology threshold; neither verifies the truth of a user post.
  `verified` remains a compatibility alias for `threshold_qualified` only.
  Fresh but irrelevant channel banter is excluded and listed under
  `data_quality.ceo_ca_quality_dropped`)
- `clinical_trials`
- `social_whispers`
- `social_attention` (structured ApeWisdom ticker heat: mentions, upvotes,
  mention_velocity_24h, rank_delta_24h, attention_score, is_low_volume.
  Universe-first: one row per universe ticker whatever its leaderboard rank —
  `universe_member`/`in_leaderboard` flags, mentions=0 when absent from the
  scanned depth (~500 rows), plus `burst_ratio` = mentions today /
  trailing-20-run median, null until 5 prior runs. Market-color top-N rows
  kept with `universe_member: false`)
- `social_alerts` (SOCIAL_BURST: burst_ratio >= 3.0 and mentions >= 10;
  ATTENTION_BIRTH: entered ApeWisdom top-200 after >=5 runs absent, mcap <
  $2000M; empty until state/social_history_2.6.3.json warms up over 5 runs)
- `fda_catalysts` (keyword-mined FDA regulatory events — PDUFA_DATE, ADCOM,
  CRL, ACCEPTANCE, PRIORITY_REVIEW, DESIGNATION — from SEC filing metadata,
  biotech_news titles, CEO.ca posts, and the FDA AdCom calendar when its page
  parses; keyword+regex only, no LLM. event_date null when no explicit date
  sits within 200 chars of the keyword; deduped on ticker+event_type+
  event_date; alert=FDA_CATALYST_NEAR when 0-90 days out. Often empty —
  precision over recall. `adcom_calendar: parse_failed` health warning means
  the calendar half is degraded, not broken. The server-rendered FDA Advisory
  Committees landing page is used because the calendar table is client-rendered)
- `fda_advisory_meetings` (raw official FDA meeting evidence enriched from
  each detail-page agenda with application, sponsor, product, and a ticker only
  when the deterministic sponsor map resolves it)
- `registry_changes` (day-over-day CT.gov diffs vs the pipeline-versioned
  `state/ctgov_snapshot_2.6.3.json`; legacy unversioned state is untouched;
  same-UTC-date reruns replay changes already emitted that day:
  STATUS_FLIP — severity high for TERMINATED/SUSPENDED/WITHDRAWN — DATE_SLIP
  when primary completion moves >14 days (delta_days, positive = slip),
  ENROLLMENT_CHANGE when >10% (delta_pct). Diffed against the unfiltered
  trial merge so terminations are seen even though the catalyst list drops
  them. as_of = run time (observation time IS the event time for a diff);
  empty on quiet days and on the first/seeding run)
- `clinical_catalysts` / `fda_catalysts` each carry `ticker` (sponsor-map
  attribution, null when unknown), `primary_completion_date`, `enrollment`
  (clinical only), and a `fragility` object: {runway_risk, market_cap_musd,
  fragility_flag = runway RED/YELLOW and mcap < $2000M}; `fragile_alert:
  FRAGILE_CATALYST` when flagged and the event is 0-90 days out
- `twitter_signals`
- `deep_dive` — a compatibility field name containing the optional editorial
  focus object. It is selected from fresh, mapped headline evidence and is not
  a signal, ranking of expected returns, recommendation, or trade instruction

Generated exports are ignored by git because they are time-sensitive and may contain local research context.

### Editorial-focus sub-contract

Newly emitted pipeline-2.6.3 briefs set
`data_quality.focus_contract_version` to `2.7-editorial-focus-1`. Schemas 2.7 and 2.8
requires this marker together with the headline, universe, and signal-
eligibility markers because focus evidence is a projection of the selected
headline pool, not a new acquisition path. Stored schema-2.6 artifacts retain
their legacy focus shape under the immutable 2.6 validator.

The runtime environment variable `FOCUS_TICKER` is the optional editorial
override; unset or blank means dynamic selection. Its normalized public value is
`universe.configured_focus_ticker`. A configured instrumented ticker must pass
the same mapped, fresh, usable-evidence gate as a dynamic candidate. An
editorial-only request is rejected with `editorial_coverage_not_active` outside
active mode. A valid-format symbol outside the current 42-name coverage set is rejected
with `unmapped_or_outside_universe`. A rejected request falls back to the
deterministic dynamic selection and can never bypass evidence/freshness gates.
No configured request defaults to dynamic selection. If no candidate passes,
the JSON contains `status=no_focus`,
`universe.focus_ticker=null`, and `focus_selection_mode=none`; the text brief
omits the focus card instead of emitting an empty ticker shell.

A selected focus exposes separate integer components rather than a weighted
score: `impact` (editorial materiality, not expected return), `confidence`,
`novelty`, `source_authority`, `audience_relevance`, `timeliness`,
`independent_corroboration`, and `unresolved_contradiction_penalty`. Ranking is
lexicographic and deterministic: higher impact, confidence, source authority,
timeliness, independent corroboration, audience relevance, and novelty; then a
lower unresolved-contradiction penalty; then newer exact source `as_of`; then
ticker and primary evidence ID as stable tie-breakers. Each candidate represents
one selected story. Separate headlines for one issuer cannot lend each other
component maxima or corroboration. Source class contributes to confidence only; it
does not state licensing, redistribution, or commercial-use rights.

Each `what_changed` and `why_it_matters` claim carries one or more
`evidence_ids`. For a marked export, the semantic validator requires every ID
to resolve inside `deep_dive.evidence`, every evidence `source_record_id` to
resolve to a selected `sections.headlines` row mapped to the selected ticker,
and the copied title, link, canonical URL, provider/publisher provenance, and
timestamps/source-time kind to match that row. Optional duplicate-provider and
duplicate-publisher lineage must also match when emitted. The validator
recomputes evidence `impact`, `novelty`, `source_authority`, and
`audience_relevance` from that selected headline, `confidence` from its source
class, and `timeliness` from the evidence `as_of` relative to `generated_at`.
The top-level focus `score_components` must exactly match the primary evidence
row identified by the focus `headline` and `as_of`; its contradiction penalty
is the capped primary-evidence contradiction count. `evidence_grade` is likewise
derived from the primary evidence's authority and same-story independent
corroboration, so jointly rewriting the evidence and focus-level fields cannot
bypass lineage validation. A row present in `headlines_dropped` cannot supply
focus evidence. Evidence must also remain inside the selector's trailing
three-day window. These checks prevent stale, dropped, unmapped, invalid, or
rewritten rows from entering the editorial focus.
`independent_corroboration` counts distinct duplicate publisher identities for
the same selected story, capped at three; duplicate acquisition providers do not
increase it.

`market_reaction` and `next_checkpoint` remain null in this PR because their
timestamp/window and provenance contracts belong to later roadmap work.
`contradictions_assessed=false` avoids claiming that a contradiction search was
performed. The compatibility context arrays may summarize existing canonical
sections, but they do not become focus evidence or claims.

This selection is presentation-only. It does not change ticker membership,
signal eligibility, confluence construction, ledger rows, baselines, mutable
state, archive rules, schema version, or pipeline version. The standalone
single-ticker export remains a separate operator-requested workflow.

## Timestamp and session policy

- `generated_at` is the export artifact time.
- `as_of` is when the value was true at its source.
- `observed_at` is when this pipeline fetched or first observed the value; it
  never silently replaces an available source timestamp.
- Daily technicals, volume baselines, sector/regime calculations, and option
  volume use only the latest NYSE session whose close is at least 60 minutes
  old. A manual pre-open or intraday run cannot consume a partial daily bar.
- Price rows may show a current/partial bar for operator context, but expose
  `source_session` and `session_complete`; partial prices are not signal inputs.

## v2.5 hygiene

- Volume baseline alerts are `HIGH_VOLUME_SURPRISE` or `LOW_PARTICIPATION`.
- Baselines are isolated by `pipeline_version`, require 15 prior runs, carry
  `baseline_immature` through 19, and cap immature z-scores at ±8.
- CEO.ca and Twitter rows older than seven days, or missing source time, are
  excluded and listed in `data_quality.ceo_ca_dropped` / `twitter_dropped`.
- CEO.ca quality gating happens after freshness and before mining/rendering;
  context-only rows require a uranium/mining/nuclear domain anchor and are
  capped per source, while threshold-qualified rows are always retained.
- Machine-readable alert fields report measurements and threshold crossings;
  they do not claim intent or causality.

## v2.6 integrity

- Signal histories and caches write atomically; corrupt existing history fails
  closed instead of silently resetting.
- A process-level export lock prevents overlapping manual/scheduled runs.
- Baseline, social, registry, and confluence state commit transactionally with
  a healthy, verified archive/mirror run.
- Local snapshots, canonical archives, and recovery mirrors are verbatim copies
  of the same atomically written JSON bytes.
- Signal Ledger option anomalies retain CALL/PUT as subtype but use
  `direction: none`; social fallback rows must pass the same 3x/10-mention
  production burst gate.
- `data_quality.headline_pool` records raw fetched/dated/fresh counts and the
  newest source timestamp before relevance scoring, plus fresh score-zero
  examples. It also records every attempted query and whether the targeted
  fallback supplied the selected pool. This distinguishes a stale/empty source
  query from a scorer miss.
- `data_quality.registry_diff.records_compared` makes an empty registry-change
  section auditable instead of merely silent.

## Headline source diversity

- Provider precedence is official SEC/Federal Reserve/FDA/Nasdaq feeds,
  optional FMP and Alpha Vantage News, keyless rate-aware GDELT discovery
  batches, then a lazy Google News fill. This expansion does not change the
  existing GDELT query-list hash or request count; editorial coverage enlarges
  the single FMP primary-request payload without adding per-ticker requests.
  The existing HTTP-402 fallback described below may make a second request.
- Missing FMP/Alpha Vantage keys skip those adapters without warning.
- A Basic FMP account that receives HTTP 402 from ticker Stock News uses the
  narrower FMP Articles feed and reports `fallback_used: true`,
  `coverage: "fmp_articles"`, and the original status in
  `primary_status_code`.
- A configured provider failure is isolated, retained under
  `health.sources.news.providers`, and does not erase usable rows.
- Relevance and the three-day gate run before cross-provider exact/near-title
  deduplication. Selection is deterministic: at most one row per publisher in
  each lane, and no more than two rows per acquisition provider in each lane
  or three overall. The existing press-release and Google-provider limits also
  apply; the brief may remain underfilled rather than relax any cap.
- `data_quality.headline_pool` exposes provider/publisher counts, selected
  concentration, Google-fill activation, dedupe counts, cap-drop counts, and
  per-provider health. `data_quality.headlines_dropped` identifies the
  provider and publisher for stale, duplicate, and cap-excluded rows.
- The current tiered coverage contract is schema `2.8`. Pipeline `2.6.3`
  introduces the central 29-name signal allowlist because it closes the prior
  route through which outside-core social names could enter signals/state and
  therefore changes possible ledger observations.


## Legacy schema-2.6 headline compatibility

Historical schema-2.6 briefs set
`data_quality.headline_contract_version` to `2.6-headline-lanes-1`. The public
schema uses that additive discriminator to enforce the current contract without
retroactively invalidating stored schema-2.6 artifacts. For an unmarked brief:

The current validator selects the immutable 2.6 schema for these documents by
their own `schema_version`; passing the current schema path does not reinterpret
or mutate them as 2.7.

- stored `pipeline_version` 2.6.0 and 2.6.1 artifacts remain valid; the schema
  also recognizes 2.6.2, while newly emitted 2.6.2 briefs always carry the
  headline contract marker;
- an early `2.6.0` brief may omit `data_quality.headline_pool`, and an early
  pool requires only `fetched_count` and `fresh_before_relevance`;
- a selected headline may omit provider, publisher/domain, source-class, and
  source-record identity provenance; and
- `lane`, `universe_tickers`, and `score_components` remain an optional group,
  while any declared fields still receive their normal type and integrity
  validation. The accounting pair is likewise validated as a group when used.

For a marked brief, `pipeline_version` may be `2.6.1` or `2.6.2`; this preserves
stored marked 2.6.1 output while admitting the new pipeline era. The pool and
all selected-count, lane-histogram, and candidate-accounting fields are
required. Every selected row must contain the complete normalized shape emitted
by `build_headline_export_records`: canonical URL, nullable publication and
observation values, source-time kind, non-empty provider/source-class/source
identity, nullable publisher/domain, raw tickers, nullable summary, duplicate
provider/publisher lineage, the `source` compatibility alias, and lane/component
fields. The semantic validator also requires `source == provider`.
Current selected and dropped rows may additionally carry
`ticker_metadata_kind=subject|related` when that raw-provider qualifier exists;
it remains optional so missing legacy/provider-native metadata does not fabricate
a classification. A `related` qualifier never establishes issuer identity; the
resolved `universe_tickers` list remains authoritative downstream.

Current `link` and `canonical_url` strings must be absolute HTTP(S) web URLs
with a non-empty, syntactically valid DNS-style host. Their path, query, and
fragment tails admit only ASCII RFC-3986 reserved/unreserved characters and
valid `%HH` escapes; controls, backslashes, and raw unsafe characters are
rejected. Current `as_of` and `observed_at` strings must be timezone-qualified
RFC-3339-shaped date-times. These marker-only patterns remain effective when
optional `jsonschema` format checker dependencies are absent; `published`
intentionally remains nullable raw provider text. The marked semantic validator
also rejects unsafe URL characters, malformed percent escapes, impossible
calendar dates, and invalid or out-of-range URL ports.
Each marked `data_quality.headlines_dropped` row is a typed machine-audit
record. It requires a non-empty `reason` and `source_record_id` and retains
text/title, link and canonical URL, publication/source/observation times,
provider, publisher, source class, raw and mapped tickers, summary, component
scores, and duplicate lineage. A null lane is valid if and only if
`reason=no_approved_lane`; it must have no universe mapping, zero issuer/macro
scores, and must not satisfy the discovery-lane predicate.

`scripts/validate_export_schema.py` supplements JSON Schema with marked-only
cross-field checks: `selected_count` equals the selected-row count, the declared
lane histogram exactly matches those rows, `accounted_candidate_count` equals
`fetched_count`, and selected plus dropped rows equals the accounted total. The
exporter overwrites those derived diagnostics from its selected and dropped
records and raises a clear error if that terminal total contradicts
`fetched_count`. Fail-soft news artifacts derive explicit zero counts and still
satisfy the marked contract.

## Optional OMNI-02 intake contract

`data_quality.headline_pool.evidence_intake` has its own discriminator,
`version=omni02-metadata-1`, validated by `scripts/validate_export_schema.py`.
Daily schema remains 2.8; documents without the optional field, including historical
2.6/2.7 artifacts, are unchanged. Pipeline and ledger remain 2.6.3.

Successful manifests contain provider gap diagnostics, bounded metadata records,
terminal decisions/loss counts, selector and policy fingerprints, and a manifest
hash. Records are always unreviewed and signal-ineligible. Failed capture emits a
small typed error diagnostic and an export warning without altering selection.

Limits are 2,000 records, 2,000,000 bytes of retained metadata (not total manifest
size), and 4,096 characters per text field. Omitted, sanitized, or unresolved
records make replay incomplete. Body, summary, raw-response pointers, model-use
permission, and redistribution permission are not retained/granted by this layer.

`evaluation_at` freezes selection time; `captured_at` describes this snapshot.
`first_observed_at`, source intervals, and watermarks remain null: cross-run first
observation and complete upstream coverage are not established. Publication dates
stay date-only when appropriate; unknown source time is not replaced by run time.
Origin IDs group identical canonical references only, not verified independent
events or cross-URL syndication. Google fallback retains its conditional gate.

The existing archive writer copies the full manifest verbatim; no new sidecar or
mutable state file is introduced. Replay verifies selected identity/order, timing,
lanes, and scores, not unavailable source bodies or all dropped-only shadow labels.

## Editorial headline lanes

- Exchange listing metadata such as `(NASDAQ: DUOT)`, `NYSE: XYZ`, and
  `AMEX: XYZ` is removed before macro scoring. An exchange name counts only
  when it is the subject of genuine market-structure coverage.
- The `universe` lane requires a configured issuer or instrument mapping.
  The `macro` lane requires a true macro subject. The `discovery` lane is
  reserved for explicit high-impact developments backed by a covered vertical
  or official authority; a broad company word such as `earnings` is not enough.
- Records that do not qualify are retained in
  `data_quality.headlines_dropped` with `reason=no_approved_lane`, component
  scores, mapping output, provider, and publisher. Pool diagnostics expose
  lane counts and a candidate-accounting total.
- Selection continues to apply source time, syndication dedupe, publisher and
  press-release caps, and deterministic ordering. The frozen August 17 fixture
  makes the NXE/DUOT regression reproducible without network or user state.
- The original editorial ranking did not itself change a baseline, confluence
  population, or signal identity in schema 2.6. Pipeline era `2.6.2` started
  separately because OpenInsider excludes
  cached, undated, future-dated, and out-of-window cluster constituents; that
  changes possible confluence and ledger membership and must not be pooled with
  2.6.1 statistics.
