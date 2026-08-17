# Stock Market Intelligence Bot

A local-first data collection and observability pipeline for public market information. It gathers public headlines, filings, price/volume measurements, social context, options statistics, earnings dates, and clinical/regulatory events into a versioned daily brief for human or software review.

This repository is tooling, not a market-advisory service. It does not issue buy/sell/hold recommendations, personalize output, manage a portfolio, connect to a broker, or place orders. Labels such as `RISK_ON`, threshold tags, and legacy JSON keys containing `signal` or `alert` are deterministic data classifications retained for schema compatibility—not investment recommendations or claims about intent, causality, or future performance.

## Outputs

A successful export writes:

- `daily_brief.json` — typed schema 2.6 data for scripts, dashboards, and LLM-assisted review
- `daily_brief.txt` — human-readable rendering of the same run
- `briefs/YYYY-MM-DD/` — point-in-time snapshots
- `archive/briefs/` — append-only canonical JSON copies

An optional independent archive mirror can be configured with `BRIEF_ARCHIVE_MIRROR_DIR`. Generated artifacts, state, logs, databases, and credentials are ignored by Git.

The JSON includes source health, run context, timestamps, coverage gaps, retries, fallbacks, and data-quality exclusions. Review `health` before relying on any section.

## Current pipeline

The public twin tracks the current v2.6 collection pipeline (`schema_version=2.6`, `pipeline_version=2.6.1`):

- publisher-diverse headlines from official SEC, Federal Reserve, FDA, and Nasdaq feeds; optional FMP and Alpha Vantage adapters; bounded GDELT discovery; capped Google News fill; and explicit universe, macro, and impact-gated discovery lanes
- watchlist prices and completed-session technical measurements via yfinance
- SEC EDGAR 8-K and Form 4 collection with retry/failure metadata
- OpenInsider enrichment with parser fallback and staleness checks
- Reddit RSS, ApeWisdom, Hacker News, RSS, Substack, Nitter, and Google News narrative inputs
- completed-session put/call statistics and short-dated contract volume/open-interest anomalies
- earnings dates, ClinicalTrials.gov changes, FDA event extraction, and cash-runway measurements
- sector-relative returns, VIX term structure, and configured instrument return spreads
- exact 48-hour cross-family confluence, transactional state, atomic output, immutable archives, and failure-closed top-level stages
- a separate outcome ledger for measuring +1/+5/+20 completed-session behavior without changing production collection rules

Acquisition providers and underlying publishers are separated. Aggregators and social sources are discovery/context inputs; they do not establish filings, regulatory outcomes, trade direction, or issuer facts.

## Quickstart

Requirements:

- Python 3.11 or 3.12
- network access to the configured public sources
- a real SEC EDGAR contact string

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\activate
python -m pip install --require-hashes -r requirements.lock
copy .env.example .env
```

Edit `.env` and set `SEC_USER_AGENT`. Optional API keys and source switches are documented in `.env.example`.

Run an export:

```powershell
python export_for_gemini.py
```

Serve the local dashboard:

```powershell
python serve_dump.py
```

## Configuration

`config.py` contains a sample U.S.-market universe, aliases, source lists, and descriptive thresholds. Forks should replace the sample universe with their own neutral configuration. Do not commit private watchlists, sizing, holdings, broker data, or credentials.

Key environment variables:

- `SEC_USER_AGENT` — required SEC EDGAR identification
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — optional PRAW credentials
- `FMP_API_KEY` / `ALPHA_VANTAGE_API_KEY` — optional headline providers
- `HEADLINE_GDELT_ENABLED` / `HEADLINE_GOOGLE_FALLBACK_ENABLED` — headline source controls
- `BRIEF_ARCHIVE_DIR` — canonical append-only archive
- `BRIEF_ARCHIVE_MIRROR_DIR` — optional independent mirror; blank disables it
- Discord variables — optional local bot surface

## Timestamp and failure policy

The exporter creates one immutable UTC/NYSE `run_context`. Daily measurements use the latest completed market session; missing source time remains null rather than being replaced with run time. `as_of`, `observed_at`, scheduled event time, and effective session are distinct.

Unexpected stage, archive, or mutable-state failures produce a diagnostic `ERROR` artifact, roll state back, and return non-zero. Expected provider degradation remains visible as `WARN` with partial usable data where possible.

See [docs/EXPORT_SCHEMA.md](docs/EXPORT_SCHEMA.md) for the contract and [docs/ROADMAP.md](docs/ROADMAP.md) for public development priorities.

## Scheduler and outcome ledger

Windows users can install the UTC-gated current-user task:

```powershell
.\install_scheduler.ps1
.\run_scheduled.ps1 -ValidateOnly
```

The launcher runs the exporter and matures the separate outcome ledger. Ledger commands are research diagnostics, not performance promises or execution logic:

```powershell
python -m ledger ingest
python -m ledger update
python -m ledger rebuild
python -m ledger import-legacy
```

## Tests

Run the same deterministic, network-free suite used by CI:

```powershell
python scripts/run_offline_checks.py
python scripts/validate_export_schema.py
```

Syntax check:

```powershell
python -m compileall -q export_for_gemini.py openinsider_agent.py signals.py timeutil.py stateutil.py agents ledger scripts
```

CI repeats those checks on Python 3.11 and 3.12, installs only from hash-locked dependency files, audits runtime dependencies, and scans tracked files for secrets. `requirements.txt` and `requirements-ci.txt` remain the readable dependency inputs.

## Responsible use

- Treat all output as fallible source aggregation and deterministic measurements.
- Check licensing, terms, timestamps, and source health before redistributing data.
- Do not infer option trade direction from CALL/PUT type or estimated notional.
- Do not treat a headline count, social mention, technical label, or outcome sample as a recommendation.
- No automated order placement is implemented or authorized by this project.
- Users remain responsible for professional advice and laws or regulations applicable to their own activities.

## License

MIT License. See [LICENSE](LICENSE).
