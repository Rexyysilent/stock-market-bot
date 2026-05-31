# Export Schema

`export_for_gemini.py` writes `gemini_daily_brief.json` for LLM and dashboard consumption.

Top-level fields:

- `generated_at` - ISO timestamp for the export
- `pipeline_time_seconds` - total runtime
- `health` - source quality and failure metadata
- `summary` - high-level counts
- `sections` - structured data payloads

## Health

The health object is designed to answer: "Can I trust today's dump?"

```json
{
  "status": "OK | WARN | ERROR",
  "warnings": [],
  "errors": [],
  "section_counts": {
    "headlines": 5,
    "social_whispers": 100,
    "twitter_signals": 6,
    "sec_filings": 35,
    "earnings_calendar": 17
  },
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
- Options or earnings coverage is incomplete

## Sections

Important section keys:

- `headlines`
- `prices`
- `technicals`
- `gamma_sweeps`
- `backwardation`
- `retail_contrarian`
- `sec_filings`
- `pdufa_catalysts`
- `cash_runway_alerts`
- `insider_clusters`
- `options_flow`
- `sector_rotation`
- `earnings_calendar`
- `ceo_ca_signals`
- `clinical_trials`
- `social_whispers`
- `twitter_signals`
- `roku_deep_dive`

Generated exports are ignored by git because they are time-sensitive and may contain local research context.
