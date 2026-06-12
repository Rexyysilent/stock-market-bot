# Roadmap

This roadmap summarizes the project's current state, known limitations, and practical next steps.

The project is a local-first market intelligence pipeline. Its job is to gather public market data, normalize it into structured exports, and make source quality visible so a human or LLM can analyze the daily brief without guessing which feeds failed.

## Current Strengths

- Daily JSON and text exports for LLM-assisted analysis.
- Source health metadata for SEC EDGAR, OpenInsider, Twitter/Nitter, Reddit/RSS, options, and earnings coverage.
- Multi-agent collection across news, market data, SEC filings, social/RSS, biotech catalysts, options flow, and technicals.
- OpenInsider table scraper with browser-like user agent, pandas parser, BeautifulSoup fallback, and stale-row warning.
- SEC EDGAR retry/backoff handling with failed-call reporting.
- Earnings calendar parsing through `yfinance.get_earnings_dates()` with calendar fallback.
- Mobile dashboard and Discord bot surfaces for local use.
- Generated dumps, local databases, secrets, and private artifacts are ignored by git.

## Known Limitations

### Reddit Access

Reddit public JSON can return `403` or `429` responses, especially without authenticated PRAW credentials. When this happens, the social whisper count drops sharply even though the rest of the export can still complete.

Current mitigation:

- Reddit failures are recorded under `health.sources.social_agent`.
- The export adds a warning when Reddit fetch failures degrade coverage.
- RSS, Hacker News, OpenInsider, and Substack feeds continue to populate social context.

Planned improvement:

- Make PRAW credentials the recommended default setup path.
- Add a short source-quality score for the social section.
- Add non-Reddit community fallbacks where useful.

### X/Twitter And Nitter

Nitter instances are unreliable. Some accounts may return `404`, `429`, empty XML, or disappear entirely. Google News RSS fallback is slower and less direct than native X/Twitter data.

Current mitigation:

- The Twitter agent records which Nitter instance worked.
- The export warns when Twitter falls back to Google News or returns no indicators.
- The design treats X/Twitter as narrative velocity, not a hard dependency.

Planned improvement:

- Add a provider interface so users can plug in a paid or self-hosted Twitter/X source later.
- Add manual intel slots for pasting external X/Grok findings into the daily context.
- Add source freshness labels for narrative feeds.

### Public Source Fragility

Many sources are unofficial, rate-limited, or HTML/RSS based. They can change structure without notice.

Examples:

- OpenInsider's RSS endpoint can serve malformed or HTML-like responses.
- yfinance can return missing price data for some tickers or futures symbols.
- SEC EDGAR can return transient HTTP 500s.
- RSS feeds can be temporarily unavailable or malformed.

Current mitigation:

- Health metadata reports retries, stale data, missing rows, parser path, and failed calls.
- The export continues with partial data instead of failing the whole run.

Planned improvement:

- Add source adapters with consistent `data`, `health`, and `warnings` return shapes.
- Add a daily comparison report against the previous export.
- Add "degraded but usable" versus "insufficient" export status levels.

## Near-Term Improvements

1. **Authenticated Reddit setup**
   - Document PRAW app setup.
   - Add a startup warning when Reddit is running in public JSON mode.
   - Show Reddit auth mode in README and export health.

2. **Cleaner configuration**
   - Move watchlists and thresholds from `config.py` into optional YAML or JSON files.
   - Keep a sample config for public use.
   - Allow local private watchlists without editing tracked files.

3. **CI and deterministic tests**
   - Add GitHub Actions for `compileall` and non-network regression tests.
   - Keep live network smoke tests manual.
   - Add parser tests for OpenInsider and export health.

4. **Sample exports**
   - Add a sanitized example JSON export under `examples/`.
   - Use fake or delayed data to avoid leaking personal research context.
   - Document how consumers should read `health` before trusting sections.

5. **Dashboard polish**
   - Show health status and warnings prominently.
   - Add source freshness badges.
   - Add previous-export comparison deltas.

6. **Provider interfaces**
   - Standardize source adapters for social, filings, market data, and narrative feeds.
   - Make it easier to swap Nitter, Reddit, or market-data backends.
   - Keep each adapter's failure mode visible in export health.

## Longer-Term Direction

- Treat the bot as an intelligence operating system, not an execution engine.
