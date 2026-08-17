# Export Schema

`export_for_gemini.py` writes `daily_brief.json` for LLM and dashboard consumption.

This is a descriptive data contract. Legacy field names containing `signal` or
`alert` are compatibility identifiers for deterministic classifications; they do
not represent recommendations, trade direction, or inferred intent.
Top-level fields:

- `generated_at` - ISO timestamp for the export
- `schema_version` - public JSON contract (`2.6`)
- `pipeline_version` - upstream logic era (`2.6.1`); ledger statistics never pool eras
- `run_context` - immutable UTC/NYSE clock shared by all stages, including
  market state and the latest completed/settled session
- `pipeline_time_seconds` - total runtime
- `health` - source quality and failure metadata
- `summary` - high-level counts
- `sections` - structured data payloads

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

- A configured headline provider failed, the publisher-diverse section
  underfilled, the raw pool was empty/stale, or Google News required its
  targeted alternate query
- OpenInsider rows are stale or missing
- SEC EDGAR had retries or failed calls after retries
- Reddit/RSS social fetches returned 403/429/5xx
- Twitter fell back to slower Google News proxy data or only a subset of
  configured Nitter accounts returned usable feeds
- Options or earnings coverage is incomplete

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
  - `publisher` / `publisher_domain`: underlying newsroom used for
    concentration caps
  - `source_class`, `source_record_id`, and `canonical_url`
  - `published`, `observed_at`, and `source_time_kind`. The adapter-internal
    `provider_seen_at` is mapped differently by evidence type: for sources
    with a publisher/event time, that time becomes public `as_of` and the
    acquisition time becomes `observed_at`; for GDELT, `seendate` becomes
    public `as_of` with `source_time_kind=provider_seen` and `observed_at`
    remains null, so an index time is never claimed as publisher time
  - `duplicate_providers` / `duplicate_publishers` when one selected row
    collapsed syndicated copies
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
- `insider_clusters`
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

## v2.6.2 headline source diversity

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
  deduplication. Selection is deterministic, capped at one row per underlying
  publisher, one press-release wire, and two Google-provider rows. The brief
  may contain fewer than five rows rather than relax those caps.
- `data_quality.headline_pool` exposes provider/publisher counts, selected
  concentration, Google-fill activation, dedupe counts, cap-drop counts, and
  per-provider health. `data_quality.headlines_dropped` identifies the
  provider and publisher for stale, duplicate, and cap-excluded rows.
- These are narrative-context and additive-provenance changes. Schema remains
  `2.6`; the Tier-1 Signal Ledger and statistical segment remain `2.6.1`.
