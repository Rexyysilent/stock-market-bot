# Public Market Data & Observability Bot

An open-source, local-first data collection and observability pipeline for public market information. It gathers public headlines, filings, price/volume measurements, social context, options statistics, earnings dates, and clinical/regulatory events into a versioned daily brief for human or software review.

## Project scope

This repository is public data tooling, not a market-advisory, research-call, or execution service. It does not provide paid or free trading signals, buy/sell/hold recommendations, security rankings, target prices, personalized suitability assessments, position sizing, portfolio management, brokerage connectivity, or order placement.

Labels such as `RISK_ON`, threshold tags, and legacy JSON keys containing `signal` or `alert` are deterministic measurement classifications retained for schema compatibility. They do not express a recommendation, expected return, trading intent, inferred causality, or view about what any person should do.

The neutral scope is a product-design constraint, not legal advice or a representation that a particular operator, deployment, or downstream use is registered, exempt, or compliant in any jurisdiction. Anyone publishing reports or opinions about securities should obtain advice appropriate to their own activities and location.

## Open-source and provider policy

- The code, schemas, deterministic fixtures, and offline verification workflow are published under the MIT License.
- Prefer official filings, regulator or exchange publications, public RSS/HTML sources, keyless endpoints, and auditable open-source libraries for the default pipeline.
- Optional commercial data providers remain replaceable adapters; missing credentials must not disable the core export or offline tests.
- Keep collection, provenance, health, timestamp, and exclusion logic inspectable. Do not introduce opaque scoring sold as a signal service.
- Generated data remains local by default. The repository does not operate a hosted recommendation feed or subscriber tier.

## Outputs

A successful export writes:

- `daily_brief.json` : typed schema 2.6 data for scripts, dashboards, and LLM-assisted review
- `daily_brief.txt` : human-readable rendering of the same run
- `briefs/YYYY-MM-DD/` : point-in-time snapshots
- `archive/briefs/` : append-only canonical JSON copies

An optional independent archive mirror can be configured with `BRIEF_ARCHIVE_MIRROR_DIR`. Generated artifacts, state, logs, databases, and credentials are ignored by Git.

The JSON includes source health, run context, timestamps, coverage gaps, retries, fallbacks, and data-quality exclusions. Review `health` before relying on any section.

## Current pipeline

This repository tracks the current v2.6 collection pipeline (`schema_version=2.6`, `pipeline_version=2.6.3`):

- publisher-diverse headlines from official SEC, Federal Reserve, FDA, and Nasdaq feeds; optional FMP and Alpha Vantage adapters; bounded GDELT discovery; capped Google News fill; and explicit universe, macro, and impact-gated discovery lanes
- watchlist prices and completed-session technical measurements via yfinance
- SEC EDGAR 8-K and Form 4 collection with retry/failure metadata
- one run-scoped OpenInsider `ALL` acquisition (30 days, at most 500 rows)
  shared by narrative and cluster consumers, with a shared session, bounded
  connection/timeout retries, a source rate gate, typed failure health, and a
  last-known-good narrative-only cache
- Reddit RSS, ApeWisdom, Hacker News, RSS, Substack, Nitter, and Google News narrative inputs
- completed-session put/call statistics and short-dated contract volume/open-interest anomalies
- earnings dates, ClinicalTrials.gov changes, FDA event extraction, and cash-runway measurements
- sector-relative returns, VIX term structure, and configured instrument return spreads
- exact 48-hour cross-family confluence, transactional state, atomic output, immutable archives, and failure-closed top-level stages
- a separate outcome ledger for measuring +1/+5/+20 completed-session behavior without changing production collection rules

Acquisition providers and underlying publishers are separated. Aggregators and social sources are discovery/context inputs; they do not establish filings, regulatory outcomes, trade direction, or issuer facts.

OpenInsider live rows must have a parseable, non-future filing date inside the
cluster lookback before they can become cluster evidence. Cached rows are
explicitly stale and may supply narrative context only; they never enter
insider clusters or confluence. When live OpenInsider rows cannot produce a
cluster, SEC EDGAR Form 4 remains the cluster fallback.

## Try the synthetic demo first

```sh
python marketbot.py demo
```

Open the loopback address printed by the command. This path needs only Python,
uses fictional fixture values, and does not contact market-data providers. The
viewer accepts a saved brief through the file chooser or drag-and-drop; the file
is read inside the browser, not uploaded. Health, missing measurements, source
timestamps, and baseline maturity remain visible.

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

## Optional isolated universe profiles

The existing `config.py` path still works. A new profile starts a separate local
workspace rather than changing the sample configuration or mixing its history.

```sh
python marketbot.py plan --universe profiles/us-core.example.json
python marketbot.py run --universe profiles/us-core.example.json --workspace workspaces/core
python marketbot.py ledger update --universe profiles/us-core.example.json --workspace workspaces/core
python serve_dump.py --data-dir workspaces/core
```

`plan` makes no network requests. The 45-symbol example is an illustrative input,
not a verified coverage list or a selection recommendation. Profiles are capped
at 64 active symbols for this pilot; this is not a measured throughput guarantee.
Live runs still require the locked dependencies and a real `SEC_USER_AGENT`.

Inspect or compare local briefs without loading provider libraries:

```sh
python marketbot.py inspect workspaces/core/daily_brief.json
python marketbot.py diff previous.json current.json
```

Different pipeline/schema/universe identifiers are flagged as non-comparable.
A disappearing warning is not proof that its source recovered. These commands
are read-only summaries, not schema or market-validity certificates.

See [the workspace guide](docs/WORKSPACES.md) for migration, immutable profile
fingerprints, scheduler boundaries, and explicit trusted-LAN viewing.

## Configuration

`config.py` contains a sample U.S.-market universe, aliases, source lists, and descriptive thresholds. Forks should replace the sample universe with their own neutral configuration. Do not commit private watchlists, sizing, holdings, broker data, or credentials.

Key environment variables:

- `SEC_USER_AGENT` : required SEC EDGAR identification
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` : optional PRAW credentials
- `FMP_API_KEY` / `ALPHA_VANTAGE_API_KEY` : optional headline providers
- `HEADLINE_GDELT_ENABLED` / `HEADLINE_GOOGLE_FALLBACK_ENABLED` : headline source controls
- `BRIEF_ARCHIVE_DIR` : canonical append-only archive
- `BRIEF_ARCHIVE_MIRROR_DIR` : optional independent mirror; blank disables it
- Discord variables : optional local bot surface

## Timestamp and failure policy

The exporter creates one immutable UTC/NYSE `run_context`. Daily measurements use the latest completed market session; missing source time remains null rather than being replaced with run time. `as_of`, `observed_at`, scheduled event time, and effective session are distinct.

Unexpected stage, archive, or mutable-state failures produce a diagnostic `ERROR` artifact, roll state back, and return non-zero. Expected provider degradation remains visible as `WARN` with partial usable data where possible.

See [docs/EXPORT_SCHEMA.md](docs/EXPORT_SCHEMA.md) for the contract and [docs/ROADMAP.md](docs/ROADMAP.md) for public development priorities.

## Scheduler and outcome ledger

The existing Windows scheduler is for the legacy `config.py` path, not the new
profile launcher. Do not point a legacy ledger command at a profile workspace;
use `marketbot.py ledger` with the same profile instead.

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
python scripts/run_offline_checks.py --report review-results/offline.json
python scripts/validate_export_schema.py
```

Syntax check:

```powershell
python -m compileall -q export_for_gemini.py openinsider_agent.py signals.py timeutil.py stateutil.py marketbot.py universe_profile.py brief_tools.py session_returns.py serve_dump.py agents ledger scripts
node --check dashboard/app.js
```

CI repeats those checks on Python 3.11 and 3.12, installs only from hash-locked dependency files, audits runtime dependencies, and scans tracked files for secrets. `requirements.txt` and `requirements-ci.txt` remain the readable dependency inputs.

## Review and release notes

[The 2.6.3 review](docs/HARDENING_REVIEW.md) maps repaired behavior to tests and
states the remaining limitations. [The research register](docs/RESEARCH_REGISTER.md)
links primary sources to concrete implementation decisions. The
[roadmap](docs/ROADMAP.md) separates release gates from unimplemented proposals.
The changes improve observability and reproducibility; they do not establish
predictive performance or certify a public production service.

## Responsible use

- Treat all output as fallible source aggregation and deterministic measurements.
- Check licensing, terms, timestamps, and source health before redistributing data.
- Do not infer option trade direction from CALL/PUT type or estimated notional.
- Do not treat a headline count, social mention, technical label, or outcome sample as a recommendation.
- No automated order placement is implemented or authorized by this project.
- Do not market forks or generated artifacts as guaranteed, actionable, or regulator-approved signals.
- Users remain responsible for professional advice and laws or regulations applicable to their own activities.

## License

MIT License. See [LICENSE](LICENSE).
