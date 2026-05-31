# Stock Market Intelligence Bot

A local-first market intelligence pipeline that gathers public market, filings, social, options, and catalyst data into a daily brief for human review and LLM-assisted analysis.

This is not an auto-trading bot. It is a research workflow for building a structured daily market picture from noisy public sources.

## What It Produces

Running the daily exporter writes two ignored local files:

- `gemini_daily_brief.json` - structured data for LLMs, dashboards, and scripts
- `gemini_daily_brief.txt` - readable daily brief

The JSON includes a top-level `health` block that reports source freshness, retries, fallbacks, and missing sections so a reviewer can tell whether the dump is complete or degraded.

## Signals Covered

- Market headlines from Google News RSS
- Watchlist prices and technical indicators via yfinance
- SEC EDGAR 8-K and Form 4 scans with retry/failure health metadata
- OpenInsider HTML table scraping with pandas/BeautifulSoup fallback and staleness checks
- X/Twitter-style narrative feeds through Nitter with Google News fallback
- Reddit, Hacker News, RSS, and Substack social whisper feeds
- Options flow, put/call ratios, and short-dated gamma sweep detection
- Earnings calendar dates, timing, and EPS estimates
- Biotech PDUFA/clinical-trial catalysts and cash-runway alerts
- Sector rotation and physical-versus-paper commodity divergence checks

## Quickstart

Requirements:

- Python 3.11
- Network access to public data sources
- A real SEC EDGAR user agent string

```powershell
py -3.11 -m venv venv
.\venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and set at least `SEC_USER_AGENT`.

Run the daily export:

```powershell
python export_for_gemini.py
```

Serve the latest dump on your local network:

```powershell
python serve_dump.py
```

## Configuration

Primary watchlists and thresholds live in `config.py`. Secrets and optional service credentials belong in `.env`, which is ignored by git.

Important environment variables:

- `SEC_USER_AGENT` - required for respectful SEC EDGAR usage
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` - optional PRAW credentials; recommended because public Reddit JSON may return 403/429
- `DISCORD_TOKEN`, `DISCORD_GUILD_ID`, `REPORT_CHANNEL_ID` - optional Discord bot/runtime settings

## Health Metadata

The export health block is intentionally first-class. It currently reports:

- OpenInsider parser path, row counts, latest filing date, and stale status
- SEC request counts, retries, status-code counts, and failed calls
- Twitter/Nitter source path and fallback status
- Reddit/RSS social failures and status counts
- Earnings/option coverage and empty-section warnings

See `docs/EXPORT_SCHEMA.md` for the JSON shape.

## Tests

These are lightweight smoke/regression scripts, not a full pytest suite.

```powershell
python test_earnings_calendar.py
python test_pdufa.py
python test_dip_features.py
```

For syntax checks:

```powershell
python -m compileall export_for_gemini.py openinsider_agent.py agents
```

## Data And Risk Notes

- This project uses public sources that can rate-limit, block, or change format without notice.
- Generated dumps can include time-sensitive market information and should stay out of git.
- The project is for research only and is not financial advice.
- Before publishing publicly, review `docs/PUBLISHING_CHECKLIST.md`.
