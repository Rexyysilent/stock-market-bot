"""
Daily Export for Gemini Analysis — WARLORD EDITION
ThreadPoolExecutor parallelized pipeline. All 12 steps fire concurrently.

Output: 
  - gemini_daily_brief.txt (human readable)
  - gemini_daily_brief.json (structured for LLM parsing)
"""
import sys
sys.path.insert(0, '.')

import io
import json
import re
import requests
import numpy as np
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed


class NumpySafeEncoder(json.JSONEncoder):
    """Handle numpy types that slip through from yfinance/pandas."""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

# Fix Windows console encoding (cp1252 can't handle emoji)
import sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from agents.news_agent import NewsAgent
from agents.social_agent import SocialAgent
from agents.watcher_agent import WatcherAgent
from agents.research_agent import ResearchAgent
from agents.twitter_agent import TwitterAgent
from agents.sec_agent import SECAgent
from config import ALL_TICKERS, WATCHLIST_STOCKS, WATCHLIST_VULTURE


# Extract source from strings like '[r/wallstreetbets] Post title (Score: 123)'
def parse_source_from_string(text):
    match = re.match(r'\[([^\]]+)\]', text)
    return match.group(1) if match else "unknown"


def _count_social_sources(whispers):
    counts = {}
    for item in whispers:
        source = parse_source_from_string(item)
        counts[source] = counts.get(source, 0) + 1
    return counts


def _copy_health(agent, attr_name="health"):
    if agent is None:
        return {}
    if hasattr(agent, "get_health"):
        return agent.get_health()
    return dict(getattr(agent, attr_name, {}) or {})


def _add_unique(items, value):
    if value and value not in items:
        items.append(value)


def build_export_health(
    generated_at,
    elapsed,
    headlines,
    whispers,
    prices,
    technicals,
    twitter_data,
    sec_filings,
    insider_clusters,
    options_flow,
    earnings_cal,
    pdufa_data,
    all_gamma_sweeps,
    backwardation,
    social_agent,
    twitter_agent,
    sec_agent,
):
    warnings = []
    errors = []
    social_source_counts = _count_social_sources(whispers)
    social_agent_health = _copy_health(social_agent)
    reddit_failure_count = (
        len(social_agent_health.get("reddit_failures", []))
        + social_agent_health.get("reddit_failures_omitted", 0)
    )
    rss_failure_count = (
        len(social_agent_health.get("rss_failures", []))
        + social_agent_health.get("rss_failures_omitted", 0)
    )
    if reddit_failure_count:
        _add_unique(
            warnings,
            f"Reddit social fetch had {reddit_failure_count} failures; whisper coverage may be degraded.",
        )
    if rss_failure_count:
        _add_unique(
            warnings,
            f"RSS/social feeds had {rss_failure_count} failures; external-feed coverage may be degraded.",
        )

    openinsider_agent = getattr(social_agent, "openinsider", None)
    openinsider_health = _copy_health(openinsider_agent)
    openinsider_warning_items = [
        item for item in whispers
        if parse_source_from_string(item) == "OpenInsider/WARN"
    ]
    openinsider_trade_items = [
        item for item in whispers
        if parse_source_from_string(item) == "OpenInsider"
    ]
    if openinsider_health.get("error"):
        _add_unique(warnings, f"OpenInsider error: {openinsider_health['error']}")
    if openinsider_health.get("warning") and not openinsider_warning_items:
        _add_unique(warnings, f"OpenInsider: {openinsider_health['warning']}")
    for item in openinsider_warning_items:
        _add_unique(warnings, item)
    if not openinsider_trade_items:
        _add_unique(warnings, "OpenInsider returned no trade rows in the social feed.")

    sec_health = _copy_health(sec_agent)
    sec_failed_count = (
        len(sec_health.get("failed_requests", []))
        + sec_health.get("failed_requests_omitted", 0)
    )
    if sec_failed_count:
        _add_unique(warnings, f"SEC EDGAR had {sec_failed_count} failed calls after retries.")
    elif sec_health.get("retries", 0):
        _add_unique(warnings, f"SEC EDGAR needed {sec_health['retries']} retries but recovered.")
    if not sec_filings:
        _add_unique(warnings, "SEC EDGAR returned zero filings for the watchlist.")
    if sec_health.get("insider_cluster_fallback_used"):
        _add_unique(warnings, "Insider cluster scan used SEC EDGAR fallback after OpenInsider produced no clusters.")

    twitter_health = _copy_health(twitter_agent)
    twitter_used_google = any(
        item.get("account") == "GoogleNews" for item in twitter_data
    ) or twitter_health.get("fallback_used")
    if not twitter_data:
        _add_unique(warnings, "Twitter/X signals are empty; narrative velocity is degraded.")
    elif twitter_used_google:
        _add_unique(warnings, "Twitter/X used Google News fallback; treat as a slower narrative proxy.")

    expected_options_tickers = len(list(dict.fromkeys(WATCHLIST_STOCKS + WATCHLIST_VULTURE)))
    if len(options_flow) < expected_options_tickers:
        _add_unique(
            warnings,
            f"Options flow covered {len(options_flow)}/{expected_options_tickers} expected tickers.",
        )
    if not earnings_cal:
        _add_unique(warnings, "Earnings calendar returned zero rows; yfinance calendar may be stale/unavailable.")

    missing_prices = sorted([
        ticker for ticker, value in prices.items()
        if value in (None, "N/A")
    ])
    if missing_prices:
        _add_unique(warnings, f"Missing price data for: {', '.join(missing_prices[:12])}")

    technical_errors = {
        ticker: data.get("error")
        for ticker, data in technicals.items()
        if data.get("error")
    }
    if technical_errors:
        _add_unique(warnings, f"Technical indicators had errors for {len(technical_errors)} tickers.")

    status = "ERROR" if errors else "WARN" if warnings else "OK"
    return {
        "status": status,
        "generated_at": generated_at,
        "pipeline_time_seconds": round(elapsed, 1),
        "warnings": warnings,
        "errors": errors,
        "section_counts": {
            "headlines": len(headlines),
            "social_whispers": len(whispers),
            "twitter_signals": len(twitter_data),
            "sec_filings": len(sec_filings),
            "insider_clusters": len(insider_clusters),
            "options_tickers": len(options_flow),
            "gamma_sweeps": len(all_gamma_sweeps),
            "earnings_calendar": len(earnings_cal),
            "pdufa_catalysts": len(pdufa_data.get("catalysts", [])),
            "cash_runway_alerts": len(pdufa_data.get("alerts", [])),
            "backwardation_alerts": len(backwardation.get("alerts", [])),
        },
        "sources": {
            "social": social_source_counts,
            "social_agent": social_agent_health,
            "openinsider": {
                **openinsider_health,
                "social_trade_items": len(openinsider_trade_items),
                "warning_items": len(openinsider_warning_items),
            },
            "sec_edgar": {
                **sec_health,
                "watchlist_tickers": len(ALL_TICKERS),
                "filings_returned": len(sec_filings),
            },
            "twitter": twitter_health,
            "options_flow": {
                "expected_tickers": expected_options_tickers,
                "covered_tickers": len(options_flow),
                "gamma_sweeps": len(all_gamma_sweeps),
            },
            "earnings_calendar": {
                "rows": len(earnings_cal),
            },
        },
    }


def generate_daily_brief():
    print("=" * 60)
    print("  WARLORD EDITION — Market Intelligence Daily Brief")
    print("  ThreadPoolExecutor Parallel Pipeline")
    print("=" * 60)
    
    news_agent = NewsAgent()
    social_agent = SocialAgent()
    watcher_agent = WatcherAgent()
    research_agent = ResearchAgent()
    twitter_agent = TwitterAgent()
    sec_agent = SECAgent()
    
    # ═══════════════════════════════════════════════════════════════════
    # PHASE 1: Fire ALL data fetches in parallel
    # ═══════════════════════════════════════════════════════════════════
    print("\n  [PHASE 1] Firing all agents in parallel...")
    start_time = datetime.now()

    with ThreadPoolExecutor(max_workers=8) as pool:
        # Core intelligence
        fut_news       = pool.submit(news_agent.get_global_headlines)
        fut_social     = pool.submit(social_agent.get_whisper)
        fut_prices     = pool.submit(watcher_agent.get_full_report)
        fut_techs      = pool.submit(watcher_agent.get_all_technicals)
        fut_ceo        = pool.submit(research_agent.get_ceo_ca_signals)
        fut_trials     = pool.submit(research_agent.get_clinical_trials)
        fut_twitter    = pool.submit(twitter_agent.get_twitter_intel, 5)
        fut_sec        = pool.submit(sec_agent.scan_all_watchlist, 7)
        fut_pdufa      = pool.submit(research_agent.get_pdufa_with_financials)
        fut_insider    = pool.submit(sec_agent.detect_insider_clusters, 30)
        fut_options    = pool.submit(watcher_agent.get_all_options_flow)
        fut_earnings   = pool.submit(watcher_agent.get_earnings_calendar)
        fut_rotation   = pool.submit(watcher_agent.get_sector_rotation)
        # Warlord Arsenal
        fut_backwrd    = pool.submit(watcher_agent.check_backwardation)
        fut_contrarian = pool.submit(social_agent.get_retail_contrarian_index)

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 2: Collect results
    # ═══════════════════════════════════════════════════════════════════
    print("  [PHASE 2] Collecting results...")

    headlines       = fut_news.result()
    whispers        = fut_social.result()
    prices          = fut_prices.result()
    technicals      = fut_techs.result()
    ceo_signals     = fut_ceo.result()
    clinical_trials = fut_trials.result()
    twitter_data    = fut_twitter.result()
    sec_filings     = fut_sec.result()
    pdufa_data      = fut_pdufa.result()
    insider_clusters = fut_insider.result()
    options_flow    = fut_options.result()
    earnings_cal    = fut_earnings.result()
    sector_rotation = fut_rotation.result()
    backwardation   = fut_backwrd.result()
    contrarian      = fut_contrarian.result()

    elapsed = (datetime.now() - start_time).total_seconds()
    print(f"\n  ⚡ Pipeline completed in {elapsed:.1f}s")

    # Collect all gamma sweeps across tickers
    all_gamma_sweeps = []
    for ticker, data in options_flow.items():
        all_gamma_sweeps.extend(data.get("gamma_sweeps", []))

    # Generate timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    iso_timestamp = datetime.now().isoformat()
    health = build_export_health(
        iso_timestamp,
        elapsed,
        headlines,
        whispers,
        prices,
        technicals,
        twitter_data,
        sec_filings,
        insider_clusters,
        options_flow,
        earnings_cal,
        pdufa_data,
        all_gamma_sweeps,
        backwardation,
        social_agent,
        twitter_agent,
        sec_agent,
    )
    
    # ========== Write TXT file (human readable) ==========
    filename_txt = "gemini_daily_brief.txt"
    with open(filename_txt, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write(f"MARKET INTELLIGENCE DAILY BRIEF — WARLORD EDITION\n")
        f.write(f"Generated: {timestamp}\n")
        f.write(f"Pipeline Time: {elapsed:.1f}s (ThreadPoolExecutor)\n")
        f.write("=" * 70 + "\n\n")
        f.write("## EXPORT HEALTH\n")
        f.write("-" * 40 + "\n")
        f.write(f"- Status: {health['status']}\n")
        if health["warnings"]:
            for warning in health["warnings"][:10]:
                f.write(f"- WARN: {warning}\n")
        else:
            f.write("- No source health warnings\n")
        f.write("\n")
        
        f.write("## HEADLINES (Source: Google News)\n")
        f.write("-" * 40 + "\n")
        for h in headlines:
            f.write(f"- {h}\n")
        f.write("\n")
        
        f.write("## ASSET PRICES (Source: yfinance)\n")
        f.write("-" * 40 + "\n")
        for ticker, price in prices.items():
            f.write(f"- {ticker}: {price}\n")
        f.write("\n")
        
        f.write("## TECHNICAL INDICATORS\n")
        f.write("-" * 40 + "\n")
        for ticker, tech in technicals.items():
            f.write(f"- {ticker}: RSI={tech.get('rsi', '?')} | Trend={tech.get('trend', '?')}")
            if tech.get('alerts'):
                f.write(f" | ALERTS: {', '.join(tech['alerts'])}")
            f.write("\n")
        f.write("\n")
        
        # === WARLORD ARSENAL ===
        f.write("=" * 70 + "\n")
        f.write("## WARLORD ARSENAL\n")
        f.write("=" * 70 + "\n\n")

        # Gamma Sweeps
        f.write("--- GAMMA SQUEEZE ATTEMPTS (Vol/OI > 5x, DTE ≤ 5, Premium > $500K) ---\n")
        if all_gamma_sweeps:
            for sweep in all_gamma_sweeps:
                f.write(
                    f"- 🎯 {sweep['ticker']} ${sweep['strike']}{sweep['type'][0]} "
                    f"DTE={sweep['dte']} Vol/OI={sweep['vol_oi_ratio']}x "
                    f"Premium={sweep['premium_fmt']} (Exp: {sweep['expiration']})\n"
                )
        else:
            f.write("- No gamma squeeze attempts detected\n")
        f.write("\n")

        # Backwardation
        f.write("--- PHYSICAL vs PAPER DIVERGENCE (Backwardation) ---\n")
        for pair in backwardation.get("pairs", []):
            status = "🚨 BACKWARDATION" if pair["backwardation"] else "✅ NORMAL"
            f.write(
                f"- [{status}] {pair['commodity']}: "
                f"Physical ({pair['physical_ticker']}) {pair['physical_5d_chg']:+.1f}% vs "
                f"Paper ({pair['paper_ticker']}) {pair['paper_5d_chg']:+.1f}% | "
                f"Spread: {pair['spread']:+.1f}%\n"
            )
        if backwardation.get("alerts"):
            for alert in backwardation["alerts"]:
                f.write(f"  {alert}\n")
        f.write("\n")

        # Retail Contrarian Index
        f.write("--- RETAIL CONTRARIAN INDEX (Inverse Sentiment) ---\n")
        for sub in contrarian.get("subreddits", []):
            status = "🚨 TOPPED" if sub["is_topped"] else "🟢 NORMAL"
            f.write(
                f"- [{status}] r/{sub['subreddit']}: "
                f"{sub['euphoria_ratio']:.0%} euphoria "
                f"({sub['euphoric_posts']}/{sub['total_posts']} posts)\n"
            )
        if contrarian.get("biz"):
            f.write(f"- /biz/ threads scanned: {len(contrarian['biz'])}\n")
        if contrarian.get("alerts"):
            for alert in contrarian["alerts"]:
                f.write(f"  {alert}\n")
        f.write("\n")

        # === END WARLORD ARSENAL ===
        
        f.write("## SEC EDGAR FILINGS (Source: SEC EDGAR)\n")
        f.write("-" * 40 + "\n")
        for filing in sec_filings:
            f.write(f"- [{filing['ticker']}/{filing['form_type']}] {filing['description']}\n")
            f.write(f"  Filed: {filing['date']} | Link: {filing['link']}\n")
        f.write("\n")

        # PDUFA Catalysts + Cash Runway (structured format matching reference)
        f.write("## FDA PDUFA CATALYSTS + CASH RUNWAY\n")
        f.write("Priority: CRSP, NTLA, RGNX | Window: 60 days\n")
        f.write("Risk: GREEN (>6Q) | YELLOW (4-6Q) | RED (<4Q cash runway)\n")
        f.write("-" * 40 + "\n")
        
        # Bankruptcy risk alerts first
        alerts = pdufa_data.get('alerts', [])
        if alerts:
            f.write("\n--- BANKRUPTCY RISK ALERTS ---\n")
            for alert in alerts:
                if isinstance(alert, dict):
                    f.write(f"- {alert['message']}\n")
                else:
                    f.write(f"- {alert}\n")
        
        # Cash runway summary per ticker
        financials = pdufa_data.get('financials', {})
        if financials:
            f.write("\n--- CASH RUNWAY SUMMARY ---\n")
            for ticker, data in sorted(financials.items()):
                risk_tag = {"GREEN": "[OK]", "YELLOW": "[CAUTION]", "RED": "[DANGER]"}.get(data.get('risk_level', ''), "[?]")
                runway = f"{data['runway_quarters']}Q" if data.get('runway_quarters', 999) < 999 else "CF+"
                f.write(
                    f"- {risk_tag} {ticker}: Cash {data.get('cash_formatted', 'N/A')} | "
                    f"Burn {data.get('burn_formatted', 'N/A')}/Q | Runway {runway} | "
                    f"Debt {data.get('debt_formatted', 'N/A')} | MCap {data.get('mcap_formatted', 'N/A')}\n"
                )
        
        # Upcoming catalysts with details
        catalysts = pdufa_data.get('catalysts', [])
        if catalysts:
            f.write("\n--- UPCOMING CATALYSTS ---\n")
            for cat in catalysts[:30]:  # Cap at 30
                priority_tag = " [WATCHLIST]" if cat.get('is_priority') else ""
                days_str = f" ({cat['days_until']}d)" if cat.get('days_until') is not None else ""
                f.write(f"- [{cat.get('source', 'PDUFA')}] {cat.get('title', cat.get('drug', 'Unknown'))}{priority_tag}{days_str}\n")
                if cat.get('sponsor'):
                    f.write(f"  Sponsor: {cat['sponsor']} | Status: {cat.get('status', '?')} | Phase: {cat.get('phase', '?')}\n")
        else:
            f.write("- No upcoming PDUFA catalysts found\n")
        f.write("\n")
        
        f.write("## CEO.CA / URANIUM INTELLIGENCE (Source: CEO.ca via Google News)\n")
        f.write("Tickers: UUUU, CCJ, NXE, DNN | Filter: ≥1% U3O8, >5m intercept\n")
        f.write("-" * 40 + "\n")
        for s in ceo_signals:
            f.write(f"- [Source: {s['source']}] {s['title']}\n")
            if s.get('grade_tag'):
                f.write(f"  GRADE: {s['grade_tag']}\n")
            f.write(f"  Link: {s['link']}\n")
        f.write("\n")
        
        f.write("## CLINICALTRIALS.GOV / CRISPR & GENE THERAPY (Source: ClinicalTrials.gov API)\n")
        f.write("Focus: Vertex, CRISPR Therapeutics, Gene Editing\n")
        f.write("-" * 40 + "\n")
        for t in clinical_trials:
            f.write(f"- [{t['nct_id']}] {t['title']}\n")
            f.write(f"  Status: {t['status']} | Phase: {t['phase']} | Sponsor: {t['sponsor']}\n")
            f.write(f"  Link: {t['link']}\n")
        f.write("\n")

        # Dip Anticipation Signals
        f.write("## DIP ANTICIPATION SIGNALS\n")
        f.write("-" * 40 + "\n")
        
        # RSI Divergences
        f.write("\n--- RSI DIVERGENCES ---\n")
        rsi_divs = {t: d for t, d in technicals.items() if d.get('rsi_divergence')}
        if rsi_divs:
            for ticker, data in rsi_divs.items():
                f.write(f"- {ticker}: {data['rsi_divergence'].upper()} DIVERGENCE (RSI={data.get('rsi', '?')})\n")
        else:
            f.write("- No RSI divergences detected\n")
        
        # Insider Selling Clusters
        f.write("\n--- INSIDER SELLING CLUSTERS ---\n")
        if insider_clusters:
            for cluster in insider_clusters:
                severity = cluster.get('alert_level', 'MEDIUM')
                f.write(f"- [{severity}] {cluster['ticker']}: {cluster['insider_count']} Form 4 filings in {cluster.get('period_days', 30)}d\n")
        else:
            f.write("- No insider selling clusters detected\n")
        
        # Options Flow
        f.write("\n--- OPTIONS FLOW (PUT/CALL SIGNALS) ---\n")
        for ticker, data in options_flow.items():
            if data.get('alerts') or data.get('put_call_vol_ratio'):
                f.write(f"- {ticker}: P/C Vol={data.get('put_call_vol_ratio', '?')} | P/C OI={data.get('put_call_oi_ratio', '?')} | Puts={data.get('total_put_volume', '?')} Calls={data.get('total_call_volume', '?')}\n")
                if data.get('alerts'):
                    for alert in data['alerts']:
                        f.write(f"  {alert}\n")
        
        # Sector Rotation
        f.write("\n--- SECTOR ROTATION ---\n")
        f.write(f"- Signal: {sector_rotation.get('signal', 'N/A')}\n")
        f.write(f"- Growth 5d: {sector_rotation.get('growth_5d', 'N/A')} | Defensive 5d: {sector_rotation.get('defensive_5d', 'N/A')}\n")
        spread_5d = sector_rotation.get('spread_5d', 'N/A')
        if isinstance(spread_5d, (int, float)):
            spread_5d = f"{spread_5d:+.1f}%"
        f.write(f"- Spread (Def-Growth): {spread_5d}\n")
        if sector_rotation.get('growth_20d'):
            growth_20d = sector_rotation.get('growth_20d', 'N/A')
            def_20d = sector_rotation.get('defensive_20d', 'N/A')
            if isinstance(growth_20d, (int, float)):
                growth_20d = f"{growth_20d:+.1f}%"
            if isinstance(def_20d, (int, float)):
                def_20d = f"{def_20d:+.1f}%"
            f.write(f"- Growth 20d: {growth_20d} | Defensive 20d: {def_20d}\n")
            spread_20d = sector_rotation.get('spread_20d', 0)
            if isinstance(spread_20d, (int, float)):
                leader = "defensives" if spread_20d > 0 else "growth"
                f.write(f"  📊 20-day trend confirms: {leader} {'+' if spread_20d > 0 else ''}{round(spread_20d, 1)}% vs {'growth' if spread_20d > 0 else 'defensives'} (sustained {'risk-off' if spread_20d > 0 else 'risk-on'})\n")
        f.write("\n")

        # Earnings Calendar
        if earnings_cal:
            f.write("## UPCOMING EARNINGS\n")
            f.write("-" * 40 + "\n")
            for earn in earnings_cal:
                timing = earn.get('timing') or '?'
                days = earn.get('days_until', '?')
                eps = earn.get('eps_estimate')
                eps_text = f" | EPS est: {eps}" if eps is not None else ""
                f.write(
                    f"- {earn.get('ticker', '?')}: {earn.get('earnings_date', '?')} "
                    f"({timing}, D+{days}){eps_text}\n"
                )
            f.write("\n")
        
        f.write("## SOCIAL WHISPERS (Sources: Reddit, HackerNews, RSS Feeds)\n")
        f.write("-" * 40 + "\n")
        for w in whispers:
            f.write(f"- {w}\n")
        f.write("\n")
        
        f.write("## X/TWITTER SIGNALS (Source: X/Twitter via Nitter/RSS)\n")
        f.write("-" * 40 + "\n")
        for tw in twitter_data:
            f.write(f"- [{tw.get('account', tw.get('query', 'X'))}] {tw['title']}\n")
            f.write(f"  Link: {tw['link']}\n")
        f.write("\n")
        
        # ===== ROKU DEEP DIVE =====
        f.write("=" * 70 + "\n")
        f.write("## ROKU DEEP DIVE\n")
        f.write("=" * 70 + "\n\n")
        
        roku_price = prices.get("ROKU", "N/A")
        f.write(f"--- PRICE ---\n")
        f.write(f"- ROKU: {roku_price}\n\n")
        
        roku_tech = technicals.get("ROKU", {})
        f.write(f"--- TECHNICALS ---\n")
        if roku_tech:
            f.write(f"- RSI: {roku_tech.get('rsi', '?')}\n")
            f.write(f"- Trend: {roku_tech.get('trend', '?')}\n")
            f.write(f"- Volume Ratio: {roku_tech.get('volume_ratio', '?')}\n")
            if roku_tech.get('rsi_divergence'):
                f.write(f"- RSI Divergence: {roku_tech['rsi_divergence'].upper()}\n")
            if roku_tech.get('alerts'):
                f.write(f"- ALERTS: {', '.join(roku_tech['alerts'])}\n")
            if roku_tech.get('sma_50'):
                f.write(f"- SMA-50: {roku_tech.get('sma_50', '?')} | SMA-200: {roku_tech.get('sma_200', '?')}\n")
        else:
            f.write("- No technical data available\n")
        f.write("\n")
        
        roku_options = options_flow.get("ROKU", {})
        f.write(f"--- OPTIONS FLOW ---\n")
        if roku_options and (roku_options.get('put_call_vol_ratio') or roku_options.get('alerts')):
            f.write(f"- P/C Volume Ratio: {roku_options.get('put_call_vol_ratio', '?')}\n")
            f.write(f"- P/C OI Ratio: {roku_options.get('put_call_oi_ratio', '?')}\n")
            f.write(f"- Total Put Volume: {roku_options.get('total_put_volume', '?')}\n")
            f.write(f"- Total Call Volume: {roku_options.get('total_call_volume', '?')}\n")
            if roku_options.get('gamma_sweeps'):
                f.write("- 🎯 GAMMA SWEEPS:\n")
                for sweep in roku_options['gamma_sweeps']:
                    f.write(f"  ${sweep['strike']}{sweep['type'][0]} DTE={sweep['dte']} Vol/OI={sweep['vol_oi_ratio']}x Premium={sweep['premium_fmt']}\n")
            if roku_options.get('alerts'):
                for alert in roku_options['alerts']:
                    f.write(f"  ALERT: {alert}\n")
        else:
            f.write("- No significant options flow data\n")
        f.write("\n")
        
        roku_filings = [fi for fi in sec_filings if fi.get('ticker', '').upper() == 'ROKU']
        f.write(f"--- SEC FILINGS ---\n")
        if roku_filings:
            for fi in roku_filings:
                f.write(f"- [{fi['form_type']}] {fi['description']}\n")
                f.write(f"  Filed: {fi['date']} | Link: {fi['link']}\n")
        else:
            f.write("- No recent ROKU filings\n")
        f.write("\n")
        
        roku_insiders = [c for c in insider_clusters if c.get('ticker', '').upper() == 'ROKU']
        f.write(f"--- INSIDER ACTIVITY ---\n")
        if roku_insiders:
            for c in roku_insiders:
                f.write(f"- [{c.get('alert_level', 'MEDIUM')}] {c['insider_count']} Form 4 filings in {c.get('period_days', 30)}d\n")
        else:
            f.write("- No insider selling clusters detected\n")
        f.write("\n")
        
        roku_earnings = [e for e in earnings_cal if e.get('ticker', '').upper() == 'ROKU']
        f.write(f"--- EARNINGS ---\n")
        if roku_earnings:
            for e in roku_earnings:
                timing = e.get('timing') or '?'
                days = e.get('days_until', '?')
                f.write(f"- Date: {e.get('earnings_date', '?')} ({timing}, D+{days})\n")
        else:
            f.write("- No upcoming ROKU earnings in window\n")
        f.write("\n")
        
        roku_whispers = [w for w in whispers if 'ROKU' in w.upper() or 'roku' in w.lower()]
        f.write(f"--- SOCIAL MENTIONS ---\n")
        if roku_whispers:
            for w in roku_whispers:
                f.write(f"- {w}\n")
        else:
            f.write("- No ROKU mentions in social whispers\n")
        f.write("\n")
        
        roku_headlines = [h for h in headlines if 'ROKU' in h.upper() or 'roku' in h.lower()]
        f.write(f"--- NEWS HEADLINES ---\n")
        if roku_headlines:
            for h in roku_headlines:
                f.write(f"- {h}\n")
        else:
            f.write("- No ROKU-specific headlines\n")
        f.write("\n")
        
        roku_twitter = [tw for tw in twitter_data if 'ROKU' in tw.get('title', '').upper() or 'roku' in tw.get('query', '').lower()]
        f.write(f"--- X/TWITTER ---\n")
        if roku_twitter:
            for tw in roku_twitter:
                f.write(f"- [{tw.get('account', tw.get('query', 'X'))}] {tw['title']}\n")
                f.write(f"  Link: {tw['link']}\n")
        else:
            f.write("- No ROKU-specific Twitter signals\n")
        f.write("\n")
        
        f.write("=" * 70 + "\n")
        f.write("END OF BRIEF\n")
        f.write("=" * 70 + "\n")
    
    # ========== Write JSON file (structured for LLM) ==========
    filename_json = "gemini_daily_brief.json"
    
    json_data = {
        "generated_at": iso_timestamp,
        "pipeline_time_seconds": round(elapsed, 1),
        "health": health,
        "summary": {
            "total_headlines": len(headlines),
            "total_whispers": len(whispers),
            "total_tickers": len(prices),
            "total_ceo_signals": len(ceo_signals),
            "total_clinical_trials": len(clinical_trials),
            "total_twitter_signals": len(twitter_data),
            "total_sec_filings": len(sec_filings),
            "total_technical_alerts": sum(1 for t in technicals.values() if t.get("alerts")),
            "total_pdufa_catalysts": len(pdufa_data.get('catalysts', [])),
            "total_cash_runway_alerts": len(pdufa_data.get('alerts', [])),
            "total_insider_clusters": len(insider_clusters),
            "total_options_flow_alerts": len({t: d for t, d in options_flow.items() if d.get('alerts')}),
            "total_earnings_tracked": len(earnings_cal),
            "sector_rotation_signal": sector_rotation.get('signal', 'N/A'),
            "rsi_divergences": len({t: d for t, d in technicals.items() if d.get('rsi_divergence')}),
            # Warlord Arsenal
            "total_gamma_sweeps": len(all_gamma_sweeps),
            "backwardation_alerts": len(backwardation.get('alerts', [])),
            "contrarian_alerts": len(contrarian.get('alerts', [])),
        },
        "sections": {
            "headlines": [
                {"text": h, "source": "Google News"} for h in headlines
            ],
            "prices": [
                {"ticker": ticker, "price": str(price), "source": "yfinance"} 
                for ticker, price in prices.items()
            ],
            "technicals": [
                {
                    "ticker": ticker,
                    "rsi": tech.get("rsi"),
                    "trend": tech.get("trend"),
                    "volume_ratio": tech.get("volume_ratio"),
                    "rsi_divergence": tech.get("rsi_divergence"),
                    "alerts": tech.get("alerts", []),
                    "source": "yfinance/computed"
                }
                for ticker, tech in technicals.items()
            ],
            # === WARLORD ARSENAL ===
            "gamma_sweeps": all_gamma_sweeps,
            "backwardation": backwardation,
            "retail_contrarian": contrarian,
            # === END WARLORD ARSENAL ===
            "sec_filings": [
                {
                    "ticker": f["ticker"],
                    "form_type": f["form_type"],
                    "description": f["description"],
                    "date": f["date"],
                    "link": f["link"],
                    "source": "SEC EDGAR"
                } for f in sec_filings
            ],
            "pdufa_catalysts": pdufa_data.get('catalysts', []),
            "cash_runway_alerts": pdufa_data.get('alerts', []),
            "insider_clusters": [
                {
                    "ticker": c["ticker"],
                    "insider_count": c.get("insider_count", 0),
                    "period_days": c.get("period_days", 30),
                    "alert_level": c.get("alert_level", "MEDIUM")
                } for c in insider_clusters
            ],
            "options_flow": {
                ticker: {
                    "put_call_vol_ratio": d.get("put_call_vol_ratio"),
                    "put_call_oi_ratio": d.get("put_call_oi_ratio"),
                    "total_put_volume": d.get("total_put_volume"),
                    "total_call_volume": d.get("total_call_volume"),
                    "gamma_sweeps": d.get("gamma_sweeps", []),
                    "alerts": d.get("alerts", [])
                }
                for ticker, d in options_flow.items()
            },
            "sector_rotation": sector_rotation,
            "earnings_calendar": earnings_cal,
            "ceo_ca_signals": [
                {
                    "title": s['title'],
                    "link": s['link'],
                    "source": s['source'],
                    "verified": s.get('verified', False),
                    "grade_tag": s.get('grade_tag', ''),
                }
                for s in ceo_signals
            ],
            "clinical_trials": [
                {
                    "nct_id": t['nct_id'],
                    "title": t['title'],
                    "status": t['status'],
                    "phase": t['phase'],
                    "sponsor": t['sponsor'],
                    "link": t['link'],
                    "source": "ClinicalTrials.gov"
                } for t in clinical_trials
            ],
            "social_whispers": [
                {"text": w, "source": parse_source_from_string(w)} for w in whispers
            ],
            "twitter_signals": [
                {
                    "account": tw.get('account', tw.get('query', 'X')),
                    "title": tw['title'],
                    "link": tw['link'],
                    "source": "X/Twitter"
                }
                for tw in twitter_data
            ],
            "roku_deep_dive": {
                "price": str(prices.get("ROKU", "N/A")),
                "technicals": technicals.get("ROKU", {}),
                "options_flow": options_flow.get("ROKU", {}),
                "sec_filings": [fi for fi in sec_filings if fi.get("ticker", "").upper() == "ROKU"],
                "insider_clusters": [c for c in insider_clusters if c.get("ticker", "").upper() == "ROKU"],
                "earnings": [e for e in earnings_cal if e.get("ticker", "").upper() == "ROKU"],
                "social_mentions": [w for w in whispers if "roku" in w.lower()],
                "news_headlines": [h for h in headlines if "roku" in h.lower()],
                "twitter_signals": [tw for tw in twitter_data if "roku" in tw.get("title", "").lower() or "roku" in tw.get("query", "").lower()]
            }
        }
    }
    
    with open(filename_json, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False, cls=NumpySafeEncoder)
    
    print(f"\nDone! Exported to:")
    print(f"  - {filename_txt} (human readable)")
    print(f"  - {filename_json} (structured JSON)")
    print(f"\nStats:")
    print(f"  Pipeline time:          {elapsed:.1f}s")
    print(f"  Total whispers:         {len(whispers)}")
    print(f"  Total headlines:        {len(headlines)}")
    print(f"  Total tickers:          {len(prices)}")
    print(f"  Total CEO.ca signals:   {len(ceo_signals)}")
    print(f"  Total clinical trials:  {len(clinical_trials)}")
    print(f"  Total Twitter signals:  {len(twitter_data)}")
    print(f"  Total SEC filings:      {len(sec_filings)}")
    print(f"  Total technical alerts: {sum(1 for t in technicals.values() if t.get('alerts'))}")
    print(f"  Total PDUFA catalysts:  {len(pdufa_data.get('catalysts', []))}")
    print(f"  Total cash runway alerts: {len(pdufa_data.get('alerts', []))}")
    print(f"  Total insider clusters: {len(insider_clusters)}")
    print(f"  Total options flow:     {len({t: d for t, d in options_flow.items() if d.get('alerts')})}")
    print(f"  Total earnings tracked: {len(earnings_cal)}")
    print(f"  Sector rotation signal: {sector_rotation.get('signal', 'N/A')}")
    print(f"  RSI divergences:        {len({t: d for t, d in technicals.items() if d.get('rsi_divergence')})}")
    print(f"  === WARLORD ARSENAL ===")
    print(f"  Gamma sweeps:           {len(all_gamma_sweeps)}")
    print(f"  Backwardation alerts:   {len(backwardation.get('alerts', []))}")
    print(f"  Contrarian alerts:      {len(contrarian.get('alerts', []))}")
    print(f"  Export health:          {health['status']} ({len(health['warnings'])} warnings)")
    for warning in health["warnings"][:5]:
        print(f"    - {warning}")
    
    print("\n" + "="*60)
    print("[!] REMINDER: Ask Grok app for real-time X/Twitter intel!")
    print("    Example: 'What are the top tweets about $TSLA today?'")
    print("="*60)
    print("\n" + "="*60)
    print("[MOBILE] To access on phone: python serve_dump.py")
    print("   Then open the URL on your phone's browser")
    print("="*60 + "\n")

    return filename_txt, filename_json


if __name__ == "__main__":
    generate_daily_brief()
