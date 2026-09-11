# Stock Market Intelligence Bot

A local-first data collection and observability pipeline for public market information. It gathers public headlines, filings, price/volume measurements, social context, options statistics, earnings dates, and clinical/regulatory events into a versioned daily brief for human or software review.

This repository is tooling, not a market-advisory service. It does not issue buy/sell/hold recommendations, personalize output, manage a portfolio, connect to a broker, or place orders. Labels such as `RISK_ON`, threshold tags, and legacy JSON keys containing `signal` or `alert` are deterministic data classifications retained for schema compatibility—not investment recommendations or claims about intent, causality, or future performance.

## Outputs

A successful export writes:

- `daily_brief.json` — typed schema 2.8 data for scripts and dashboards; additional uses require source-purpose approval
- `daily_brief.txt` — human-readable rendering of the same run
- `briefs/YYYY-MM-DD/` — point-in-time snapshots
- `archive/briefs/` — append-only canonical JSON copies

An optional independent archive mirror can be configured with `BRIEF_ARCHIVE_MIRROR_DIR`. Generated artifacts, state, logs, databases, and credentials are ignored by Git.

The JSON includes source health, run context, timestamps, coverage gaps, retries, fallbacks, and data-quality exclusions. Review `health` before relying on any section.

## Current pipeline

This public research-tooling checkout implements the current collection pipeline (`schema_version=2.8`,
`pipeline_version=2.6.3`):

- publisher-diverse headlines from official SEC, Federal Reserve, FDA, and Nasdaq feeds; optional FMP and Alpha Vantage adapters; bounded GDELT discovery; capped Google News fill; and explicit universe, macro, and impact-gated discovery lanes
- separate ordered universes: 29 instrumented/signal-eligible tickers and 13
  editorial-only tickers, producing 42-name headline coverage without expanding
  price, technical, options, earnings, per-ticker SEC/social, or signal collection
- watchlist prices and completed-session technical measurements via yfinance
- SEC EDGAR 8-K and Form 4 collection with retry/failure metadata
- one run-scoped OpenInsider `ALL` acquisition (30 days, at most 500 rows)
  shared by narrative and cluster consumers, with a shared session, bounded
  connection/timeout retries, a source rate gate, typed failure health, and a
  last-known-good narrative-only cache; if HTTPS is specifically refused, one
  port-80 HTTP attempt may supply visibly unauthenticated narrative only
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
insider clusters or confluence. Plaintext HTTP fallback rows are likewise
marked `signal_eligible=false`, never written to the trusted cache, and blocked
from clusters, signals, state, confluence, and the ledger. When secure live
OpenInsider rows cannot produce a cluster, SEC EDGAR Form 4 remains the cluster
fallback.

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

The optional dashboard listens on all IPv4 interfaces for phone/LAN access and
serves the current export without authentication. Use only on a trusted network;
do not expose it directly to the internet or serve private research through it.

## Configuration

`config.py` contains a sample U.S.-market universe, aliases, source lists, and descriptive thresholds. Forks should replace the sample universe with their own neutral configuration. Do not commit private watchlists, sizing, holdings, broker data, or credentials.

Key environment variables:

- `SEC_USER_AGENT` — required SEC EDGAR identification
- `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` — optional PRAW credentials
- `FMP_API_KEY` / `ALPHA_VANTAGE_API_KEY` — optional headline providers
- `HEADLINE_GDELT_ENABLED` / `HEADLINE_GOOGLE_FALLBACK_ENABLED` — headline source controls
- `EDITORIAL_COVERAGE_MODE=off|shadow|active` — defaults to `shadow`; activation is manual
- `FOCUS_TICKER` — optional evidence-gated editorial focus request
- `BRIEF_ARCHIVE_DIR` — canonical append-only archive
- `BRIEF_ARCHIVE_MIRROR_DIR` — optional independent mirror; blank disables it
- Discord variables — optional local bot surface

## Editorial coverage rollout

`SIGNAL_ELIGIBLE_TICKERS` is the existing ordered 29-name instrumented set.
`EDITORIAL_ONLY_TICKERS` contains `MRNA`, `VRTX`, `BEAM`, `RARE`, `CEG`,
`LEU`, `BWXT`, `GEV`, `MP`, `NVDA`, `RKLB`, `HOOD`, and `MRK`.
`EDITORIAL_COVERAGE_TICKERS` is their ordered 42-name concatenation. The
deprecated `ALL_TICKERS` compatibility alias remains the original 29.

The earlier 41-name cohort is preserved as a legacy contract. MRK is appended,
not substituted for HOOD. This public sample configuration does not imply holdings.

Coverage mode controls mapping/focus and the explicitly declared FMP payload:

- `off`: only core names establish universe identity or editorial focus;
  pre-existing outside-universe discovery remains available.
- `shadow` (default): the 13 are mapped and scored in bounded diagnostics,
  while selection/focus use the same legacy interpretation as off mode.
  Existing discovery stories are not erased by shadow mapping.
- `active`: fresh, valid evidence for the 13 may enter the universe headline
  lane and PR3 editorial focus. It still carries `signal_eligible=false` and
  can never enter alerts, confluence, baseline state, or the outcome ledger.

GDELT stays at the same three query strings. FMP requests 27 symbols in off/shadow
and 40 in active. It makes one primary request and may make a second on a 402
entitlement fallback; that narrower FMP-articles corpus is labeled explicitly.
Watcher/SEC targets remain 29; options/earnings target 17. ApeWisdom still uses
five all-stocks pages plus one 4chan page. Targets do not guarantee data availability.

`coverage_policy.py` records versioned capabilities and source-purpose rules.
Unknown rights do not authorize new raw-body storage, model processing or
redistribution. Existing local metadata collection is retained, not certified as
a license grant. Registry/policy fingerprints and exact targets are exported.

Keep shadow mode until manual approval. Ten eligible scheduled exports are a
minimum observation period, not ten independent semantic tests. Also require
meaningfully exercised issuer cases, reviewer evidence and all of the following:

- zero false issuer mappings;
- zero signal, state, confluence, or ledger leakage;
- identical off/shadow acquisition payloads and no unapproved request growth;
- schema-valid, byte-identical canonical and archive JSON copies;
- no new hard source errors; and
- median runtime no more than 15% above the preceding successful baseline.

Policy leakage or defective implementation invalidates the relevant evaluation.
Mandatory-source gaps pause the affected gate; optional-source degradation is
reported separately. No automatic counter or activation is implemented here.
Neutral wording is a scope constraint, not a legal exemption.

## OMNI-02 metadata intake (shadow)

`EVIDENCE_INTAKE_MODE=shadow|off` defaults to `shadow`. Existing headline
acquisitions retain bounded, purpose-approved metadata before selection, including
candidates excluded by the five-headline cap. The optional audit appears at
`data_quality.headline_pool.evidence_intake`; it cannot promote a candidate into
headlines, focus, signals, state, or the ledger. Disabling intake restores the
previous output surface; additive provider gap counters remain available.

Replay an export containing that field with:

```powershell
python scripts/replay_evidence_intake.py path/to/daily_brief.json
```

Replay checks retained selection metadata against the matching selector and coverage
policy, without fetching sources. It is not raw-response/parser replay. No bodies
or summaries are newly stored, no rights to model use or redistribution are granted,
and no independent polling schedule or additional vendor requests are introduced.
Incomplete captures explicitly refuse exact replay. See the OMNI-02 handoff in
`docs/PUBLIC_PARITY.md` for acceptance limits and intentional public boundaries.

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

The separate dashboard transport check, `python test_dashboard_security.py`,
uses only a loopback listener with synthetic temporary files. CI runs it as a
separate bounded step; it is excluded from the network-free suite.

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
