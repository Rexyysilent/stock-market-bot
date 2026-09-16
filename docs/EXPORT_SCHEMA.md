# Export Schema

`export_for_gemini.py` writes `daily_brief.json` for LLM and dashboard consumption.

This is a descriptive data contract. Legacy field names containing `signal` or
`alert` are compatibility identifiers for deterministic classifications; they do
not represent recommendations, trade direction, or inferred intent.
Top-level fields:

- `generated_at` - ISO timestamp for the export
- `schema_version` - public JSON contract (`2.6`)
- `pipeline_version` - upstream logic era (`2.6.3`); ledger statistics never pool eras
- `run_context` - immutable UTC/NYSE clock shared by all stages, including
  market state and the latest completed/settled session
- `pipeline_time_seconds` - total runtime
- `health` - source quality and failure metadata
- `summary` - high-level counts
- `sections` - structured data payloads
- `data_quality` - audit metadata, including the additive headline sub-contract
  discriminator described below

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

The single acquisition record is exported under `health.sources.openinsider`.
Its transport/run fields include `failure_reason`, `attempts`, `retries`,
`retry_delays_seconds`, `live`, `run_origin`, and `scope`. `undated_rows`
counts missing source dates without deleting those canonical rows. Since pipeline
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
`openinsider_cluster_rows_dropped` (including `stale`, `undated`, `future`,
and `out_of_window`; these are the authoritative eligibility exclusions),
`insider_cluster_source`,
`insider_cluster_fallback_used`, and `insider_cluster_fallback_reason`. This
reports cluster eligibility and fallback without maintaining a contradictory
second OpenInsider acquisition status. The fallback reason is one of
`stale_cache_disallowed`, `source_unavailable`,
`no_temporally_eligible_rows`, `no_watchlist_rows`, or
`no_qualifying_clusters` when fallback is used.

A successful live parse replaces the atomic last-known-good row cache. If the
live acquisition fails, `state/openinsider_last_good.json` may supply cached
rows. They are marked stale and may be rendered only as narrative context.
Cached, undated, future-dated, and out-of-window rows are ineligible for
OpenInsider cluster constituents and therefore cannot enter
confluence. The SEC EDGAR Form 4 scan remains the cluster fallback; the health
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
  - `universe_tickers`: configured tickers mapped by explicit ticker,
    provider metadata, or issuer alias
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
  $2000M; empty until state/social_history.json warms up over 5 runs)
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
- `registry_changes` (day-over-day CT.gov diffs vs state/ctgov_snapshot.json;
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
- `roku_deep_dive`

Generated exports are ignored by git because they are time-sensitive and may contain local research context.

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
  batches covering every configured primary ticker alias, then a lazy
  Google News fill.
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
- These are narrative-context and additive-provenance changes. Schema remains
  `2.6`. The headline work itself did not require an era change; the current
  OpenInsider change introduced pipeline `2.6.2` because cluster-constituent
  eligibility affects confluence and ledger populations. Pipeline `2.6.3`
  separately changes session-return definitions, as described below.


## Headline sub-contract compatibility

Newly emitted schema-2.6 briefs set
`data_quality.headline_contract_version` to `2.6-headline-lanes-1`. The public
schema uses that additive discriminator to enforce the current contract without
retroactively invalidating stored schema-2.6 artifacts. For an unmarked brief:

- stored `pipeline_version` 2.6.0 and 2.6.1 artifacts remain valid; the schema
  also recognizes 2.6.2 and 2.6.3, while newly emitted briefs carry the
  headline contract marker;
- an early `2.6.0` brief may omit `data_quality.headline_pool`, and an early
  pool requires only `fetched_count` and `fresh_before_relevance`;
- a selected headline may omit provider, publisher/domain, source-class, and
  source-record identity provenance; and
- `lane`, `universe_tickers`, and `score_components` remain an optional group,
  while any declared fields still receive their normal type and integrity
  validation. The accounting pair is likewise validated as a group when used.

For a marked brief, `pipeline_version` may be `2.6.1`, `2.6.2`, or `2.6.3`; this
preserves stored marked output while admitting the current pipeline era. The pool and
all selected-count, lane-histogram, and candidate-accounting fields are
required. Every selected row must contain the complete normalized shape emitted
by `build_headline_export_records`: canonical URL, nullable publication and
observation values, source-time kind, non-empty provider/source-class/source
identity, nullable publisher/domain, raw tickers, nullable summary, duplicate
provider/publisher lineage, the `source` compatibility alias, and lane/component
fields. The semantic validator also requires `source == provider`.
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
- This additive editorial ranking did not itself change a baseline, confluence
  population, signal identity, or schema. Schema therefore remains `2.6`.
  Pipeline era `2.6.2` starts separately because OpenInsider now excludes
  cached, undated, future-dated, and out-of-window cluster constituents; that
  changes possible confluence and ledger membership and must not be pooled with
  2.6.1 statistics.


## Pipeline 2.6.3 additive contracts

The JSON schema remains 2.6. Pipeline 2.6.3 is a separate measurement era because
session-return definitions changed. Do not pool its outcomes with 2.6.2.

`sector_rotation` now requires the exact latest completed NYSE comparison date
and all intervening bars for each configured member. Five sessions means six
closes; twenty sessions means twenty-one closes. A fixed basket is not
renormalized around missing members. Missing coverage produces null returns and
`signal: "UNAVAILABLE"`, with per-horizon `coverage` and exclusion reasons.
Spreads are differences in percentage returns, measured in percentage points.
The comparison is not synchronized cross-asset pricing or a native futures
calendar. Empty configured baskets are unavailable, not a zero return.

`instrument_relative_return_spread` uses matching five-session endpoints for both
legs. Eligible pair records include start/end dates and horizon. Missing legs
move to `excluded_pairs` instead of becoming zero or a differently dated return.

A profile-launched brief encodes the complete validated profile fingerprint in
`universe.name`. The corresponding immutable configuration is stored separately
as `workspace/universe.json`. The old default-config path retains its existing
universe identifier. Changing a profile requires a new workspace.

The synthetic viewer fixture has `demo: true`; this is presentation context,
not a claim that any provider produced its values. The viewer and inspect/diff
commands do not rewrite saved briefs or certify the underlying observations.
