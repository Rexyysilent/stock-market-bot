"""
Single-Ticker Deep Dive Export
Usage:  python export_ticker.py ROKU
        python export_ticker.py TSLA
        python export_ticker.py CCJ

Generates:
  - deep_dive_ROKU.txt  (human readable)
  - deep_dive_ROKU.json (structured for LLM parsing)
"""
import sys
sys.path.insert(0, '.')

import io
import json
import re
from datetime import datetime

# Fix Windows console encoding
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from agents.news_agent import NewsAgent
from agents.social_agent import SocialAgent
from agents.watcher_agent import WatcherAgent
from agents.research_agent import ResearchAgent
from agents.twitter_agent import TwitterAgent
from agents.sec_agent import SECAgent
from config import ALL_TICKERS


def parse_source_from_string(text):
    match = re.match(r'\[([^\]]+)\]', text)
    return match.group(1) if match else "unknown"


def export_ticker_deep_dive(ticker):
    ticker = ticker.upper()
    
    if ticker not in [t.upper() for t in ALL_TICKERS]:
        print(f"[!] Warning: {ticker} is not in your watchlist. Proceeding anyway...")
    
    print(f"Generating deep dive for {ticker}...")
    
    news_agent = NewsAgent()
    social_agent = SocialAgent()
    watcher_agent = WatcherAgent()
    research_agent = ResearchAgent()
    twitter_agent = TwitterAgent()
    sec_agent = SECAgent()
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    iso_timestamp = datetime.now().isoformat()
    
    # ===== Gather ALL data =====
    print(f"  [1/8] Fetching {ticker} price & technicals...")
    price_data = watcher_agent.get_stock_price(ticker)
    tech_data = watcher_agent.check_technical_indicators(ticker)
    
    print(f"  [2/8] Fetching {ticker} options flow...")
    options_data = watcher_agent.get_options_flow(ticker)
    
    print(f"  [3/8] Fetching {ticker} SEC filings...")
    all_sec = sec_agent.scan_all_watchlist(days_back=14)
    ticker_filings = [f for f in all_sec if f.get('ticker', '').upper() == ticker]
    
    print(f"  [4/8] Scanning {ticker} insider clusters...")
    all_insiders = sec_agent.detect_insider_clusters(days_back=60)
    ticker_insiders = [c for c in all_insiders if c.get('ticker', '').upper() == ticker]
    
    print(f"  [5/8] Fetching {ticker} earnings calendar...")
    all_earnings = watcher_agent.get_earnings_calendar()
    ticker_earnings = [e for e in all_earnings if e.get('ticker', '').upper() == ticker]
    
    print(f"  [6/8] Scanning social whispers for {ticker}...")
    all_whispers = social_agent.get_whisper()
    ticker_whispers = [w for w in all_whispers if ticker.lower() in w.lower()]
    
    print(f"  [7/8] Scanning news headlines for {ticker}...")
    all_headlines = news_agent.get_global_headlines()
    ticker_headlines = [h for h in all_headlines if ticker.lower() in h.lower()]
    
    print(f"  [8/8] Scanning X/Twitter for {ticker}...")
    all_twitter = twitter_agent.get_twitter_intel(max_queries=5)
    ticker_twitter = [tw for tw in all_twitter 
                      if ticker.lower() in tw.get('title', '').lower() 
                      or ticker.lower() in tw.get('query', '').lower()]
    
    # Also search Google News RSS specifically for this ticker
    ticker_news_extra = []
    try:
        import feedparser
        rss_url = f"https://news.google.com/rss/search?q={ticker}+stock&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:15]:
            ticker_news_extra.append({
                "title": entry.get("title", ""),
                "link": entry.get("link", ""),
                "published": entry.get("published", ""),
                "source": "Google News (ticker search)"
            })
    except Exception as e:
        print(f"  [!] Google News ticker search failed: {e}")
    
    # ===== Write TXT =====
    filename_txt = f"deep_dive_{ticker}.txt"
    with open(filename_txt, "w", encoding="utf-8") as f:
        f.write("=" * 70 + "\n")
        f.write(f"DEEP DIVE: {ticker}\n")
        f.write(f"Generated: {timestamp}\n")
        f.write("=" * 70 + "\n\n")
        
        # Price
        f.write("## PRICE\n")
        f.write("-" * 40 + "\n")
        if price_data:
            f.write(f"- {ticker}: {price_data}\n")
        else:
            f.write(f"- {ticker}: No price data available\n")
        f.write("\n")
        
        # Technicals
        f.write("## TECHNICAL INDICATORS\n")
        f.write("-" * 40 + "\n")
        if tech_data:
            f.write(f"- RSI: {tech_data.get('rsi', '?')}\n")
            f.write(f"- Trend: {tech_data.get('trend', '?')}\n")
            f.write(f"- Volume Ratio: {tech_data.get('volume_ratio', '?')}\n")
            if tech_data.get('rsi_divergence'):
                f.write(f"- RSI Divergence: {tech_data['rsi_divergence'].upper()}\n")
            if tech_data.get('alerts'):
                f.write(f"- ALERTS: {', '.join(tech_data['alerts'])}\n")
            if tech_data.get('sma_50'):
                f.write(f"- SMA-50: {tech_data.get('sma_50', '?')}\n")
            if tech_data.get('sma_200'):
                f.write(f"- SMA-200: {tech_data.get('sma_200', '?')}\n")
            if tech_data.get('high_52w'):
                f.write(f"- 52W High: {tech_data.get('high_52w', '?')}\n")
            if tech_data.get('low_52w'):
                f.write(f"- 52W Low: {tech_data.get('low_52w', '?')}\n")
            if tech_data.get('pct_from_52w_high') is not None:
                f.write(f"- Distance from 52W High: {tech_data.get('pct_from_52w_high', '?')}%\n")
            if tech_data.get('pct_from_52w_low') is not None:
                f.write(f"- Distance from 52W Low: {tech_data.get('pct_from_52w_low', '?')}%\n")
        else:
            f.write("- No technical data available\n")
        f.write("\n")
        
        # Options Flow
        f.write("## OPTIONS FLOW\n")
        f.write("-" * 40 + "\n")
        if options_data and (options_data.get('pc_volume_ratio') or options_data.get('total_put_volume')):
            f.write(f"- P/C Volume Ratio: {options_data.get('pc_volume_ratio', '?')}\n")
            f.write(f"- P/C OI Ratio: {options_data.get('pc_oi_ratio', '?')}\n")
            f.write(f"- Total Put Volume: {options_data.get('total_put_volume', '?')}\n")
            f.write(f"- Total Call Volume: {options_data.get('total_call_volume', '?')}\n")
            if options_data.get('total_put_oi'):
                f.write(f"- Total Put OI: {options_data.get('total_put_oi', '?')}\n")
            if options_data.get('total_call_oi'):
                f.write(f"- Total Call OI: {options_data.get('total_call_oi', '?')}\n")
            if options_data.get('expiry'):
                f.write(f"- Nearest Expiry: {options_data.get('expiry', '?')}\n")
            if options_data.get('alerts'):
                f.write("\n  Alerts:\n")
                for alert in options_data['alerts']:
                    f.write(f"  - {alert}\n")
        else:
            f.write("- No options data available\n")
        f.write("\n")
        
        # SEC Filings
        f.write("## SEC FILINGS (14 days)\n")
        f.write("-" * 40 + "\n")
        if ticker_filings:
            for fi in ticker_filings:
                f.write(f"- [{fi['form_type']}] {fi['description']}\n")
                f.write(f"  Filed: {fi['date']} | Link: {fi['link']}\n")
        else:
            f.write("- No recent filings\n")
        f.write("\n")
        
        # Insider Activity
        f.write("## INSIDER ACTIVITY (60 days)\n")
        f.write("-" * 40 + "\n")
        if ticker_insiders:
            for c in ticker_insiders:
                f.write(f"- [{c.get('alert_level', 'MEDIUM')}] {c['insider_count']} Form 4 filings in {c.get('period_days', 60)}d\n")
        else:
            f.write("- No insider selling clusters detected\n")
        f.write("\n")
        
        # Earnings
        f.write("## EARNINGS\n")
        f.write("-" * 40 + "\n")
        if ticker_earnings:
            for e in ticker_earnings:
                f.write(f"- Date: {e.get('date', '?')} ({e.get('timing', '?')})\n")
        else:
            f.write("- No upcoming earnings in scan window\n")
        f.write("\n")
        
        # Google News for ticker
        f.write("## RECENT NEWS (Google News)\n")
        f.write("-" * 40 + "\n")
        if ticker_news_extra:
            for n in ticker_news_extra:
                f.write(f"- {n['title']}\n")
                if n.get('published'):
                    f.write(f"  Published: {n['published']}\n")
                f.write(f"  Link: {n['link']}\n")
        elif ticker_headlines:
            for h in ticker_headlines:
                f.write(f"- {h}\n")
        else:
            f.write("- No ticker-specific headlines found\n")
        f.write("\n")
        
        # Social Mentions
        f.write("## SOCIAL MENTIONS (Reddit / HackerNews / RSS)\n")
        f.write("-" * 40 + "\n")
        if ticker_whispers:
            for w in ticker_whispers:
                f.write(f"- {w}\n")
        else:
            f.write(f"- No {ticker} mentions in social whispers\n")
        f.write("\n")
        
        # Twitter
        f.write("## X/TWITTER SIGNALS\n")
        f.write("-" * 40 + "\n")
        if ticker_twitter:
            for tw in ticker_twitter:
                f.write(f"- [{tw.get('account', tw.get('query', 'X'))}] {tw['title']}\n")
                f.write(f"  Link: {tw['link']}\n")
        else:
            f.write(f"- No {ticker}-specific Twitter signals\n")
        f.write("\n")
        
        f.write("=" * 70 + "\n")
        f.write(f"END OF {ticker} DEEP DIVE\n")
        f.write("=" * 70 + "\n")
    
    # ===== Write JSON =====
    filename_json = f"deep_dive_{ticker}.json"
    json_data = {
        "ticker": ticker,
        "generated_at": iso_timestamp,
        "price": str(price_data) if price_data else None,
        "technicals": tech_data or {},
        "options_flow": options_data or {},
        "sec_filings": ticker_filings,
        "insider_clusters": ticker_insiders,
        "earnings": ticker_earnings,
        "news": ticker_news_extra if ticker_news_extra else ticker_headlines,
        "social_mentions": ticker_whispers,
        "twitter_signals": ticker_twitter
    }
    
    with open(filename_json, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    # Stats
    print(f"\nDone! Exported to:")
    print(f"  - {filename_txt}")
    print(f"  - {filename_json}")
    print(f"\n{ticker} Stats:")
    print(f"  Price: {price_data}")
    print(f"  RSI: {tech_data.get('rsi', 'N/A') if tech_data else 'N/A'}")
    print(f"  Trend: {tech_data.get('trend', 'N/A') if tech_data else 'N/A'}")
    print(f"  SEC filings: {len(ticker_filings)}")
    print(f"  Insider clusters: {len(ticker_insiders)}")
    print(f"  Options alerts: {len(options_data.get('alerts', [])) if options_data else 0}")
    print(f"  News articles: {len(ticker_news_extra)}")
    print(f"  Social mentions: {len(ticker_whispers)}")
    print(f"  Twitter signals: {len(ticker_twitter)}")
    
    return filename_txt, filename_json


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python export_ticker.py <TICKER>")
        print("Example: python export_ticker.py ROKU")
        print(f"\nAvailable tickers: {', '.join(ALL_TICKERS)}")
        sys.exit(1)
    
    ticker = sys.argv[1]
    export_ticker_deep_dive(ticker)
