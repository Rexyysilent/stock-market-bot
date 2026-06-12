# Export Schema (2.0)

`export_for_gemini.py` writes `gemini_daily_brief.json` for LLM and dashboard consumption,
plus a point-in-time snapshot under `briefs/YYYY-MM-DD/`. Snapshots are never overwritten:
the first run of a day owns `gemini_daily_brief.json` in that folder; later runs the same day
get timestamped siblings. This makes `briefs/` a lookahead-free dataset for backtesting.

The JSON is a pure data layer; the TXT file is the render layer. Numeric fields stay numeric
in the JSON (no preformatted strings, no emoji).

Top-level fields:

- `schema_version` - currently `"2.0"`
- `generated_at` - ISO timestamp for the export
- `pipeline_time_seconds` - total runtime
- `universe` - `name`, `version` (sha256[:8] of the ticker list), `tickers`, `focus_ticker`
- `conventions` - self-describing notes on units, null policy, record ids, and z-scores
- `health` - source quality and failure metadata (no content counts; those live in `summary`)
- `summary` - high-level counts and regime flags
- `sections` - structured data payloads
- `data_quality` - `nan_fields_nulled`: JSON paths where NaN/inf values were emitted as null

Records inside sections carry `record_id` (sha256[:16] of section + key fields, stable across
days for the same underlying record) and `as_of` (when the data was true; falls back to
`generated_at` when the source has no event time).

## Health

The health object is designed to answer: "Can I trust today's dump?"

```json
{
  "status": "OK | WARN | ERROR",
  "generated_at": "...",
  "pipeline_time_seconds": 75.0,
  "warnings": [],
  "errors": [],
  "sources": {
    "social": {},
    "social_agent": {},
    "openinsider": {},
    "sec_edgar": {},
    "twitter": {},
    "options_flow": {},
    "earnings_calendar": {}
  }
}
```

Common `WARN` reasons:

- OpenInsider rows are stale or missing
- SEC EDGAR had retries or failed calls after retries
- Reddit/RSS social fetches returned 403/429/5xx
- Twitter fell back to slower Google News proxy data
- CEO.ca items are all unverified (Google News proxy)
- Options or earnings coverage is incomplete

## Sections

Important section keys:

- `headlines` - relevance-ranked against the watchlist universe and macro themes
- `prices` - numeric `price` and `change_pct` per ticker
- `technicals` - RSI, trend, SMAs, 52-week distances, volume ratio plus `volume_ratio_z`
- `backwardation` - physical vs paper commodity divergence
- `retail_contrarian` - subreddit euphoria ratios and elevated-sentiment flags
- `sec_filings` - includes `accession_number`, `filed_at`, and a direct `primary_doc_url`
- `clinical_catalysts` - dated, active-status trial catalysts (replaces `pdufa_catalysts`)
- `biotech_news` - undated FDA/PDUFA headlines routed out of the catalyst list
- `cash_runway` / `cash_runway_alerts` - structured cash, burn, runway and risk records
- `insider_clusters`
- `options_flow` - per-ticker P/C ratios with `put_call_vol_z`; short-dated high Vol/OI
  records live in `options_flow[ticker].gamma_sweeps` (no separate top-level section)
- `sector_rotation`
- `regime` - VIX term structure (spot vs 3-month) with contango/backwardation flag
- `earnings_calendar`
- `ceo_ca_signals`
- `social_whispers` - entity-linked to universe tickers with a `relevance` score
  (1.0 cashtag, 0.8 bare symbol, 0.6 company-name alias)
- `social_whispers_unanchored` - whispers with no ticker anchor (quarantined, not deleted)
- `baseline_alerts` - metrics deviating >= 2 sigma from the ticker's own trailing
  20-run baseline (z-scores are null until 5 prior runs exist in `state/`)
- `twitter_signals`
- `deep_dive` - focus-ticker filing references and ticker-filtered text; numeric data for
  the focus ticker lives in the main sections keyed by ticker

Generated exports, `briefs/` snapshots, and `state/` baselines are ignored by git because
they are time-sensitive and may contain local research context.
