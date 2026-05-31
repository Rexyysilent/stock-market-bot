"""
Live Verification Script
Tests each agent individually to confirm data fetching works.
"""
import sys
sys.path.insert(0, '.')

import io
import logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

print("=" * 60)
print("MARKET INTELLIGENCE COUNCIL - LIVE VERIFICATION")
print("=" * 60)

# 1. Test NewsAgent
print("\n[1/6] Testing NewsAgent (Google News RSS)...")
try:
    from agents.news_agent import NewsAgent
    news = NewsAgent()
    headlines = news.get_global_headlines()
    print(f"  [OK] Fetched {len(headlines)} headlines")
    for h in headlines[:3]:
        print(f"    - {h[:80]}...")
except Exception as e:
    print(f"  [FAIL] {e}")

# 2. Test SocialAgent
print("\n[2/6] Testing SocialAgent (Reddit + HN + RSS)...")
try:
    from agents.social_agent import SocialAgent
    social = SocialAgent()
    whispers = social.get_whisper()
    print(f"  [OK] Fetched {len(whispers)} whispers")
    for w in whispers[:5]:
        print(f"    - {w[:80]}...")
except Exception as e:
    print(f"  [FAIL] {e}")

# 3. Test WatcherAgent
print("\n[3/6] Testing WatcherAgent (yfinance prices + technicals)...")
try:
    from agents.watcher_agent import WatcherAgent
    watcher = WatcherAgent()
    report = watcher.get_full_report()
    print(f"  [OK] Fetched prices for {len(report)} tickers")
    for ticker, price in list(report.items())[:5]:
        print(f"    - {ticker}: {price}")

    # Test technicals on TSLA
    print("  Testing technicals for TSLA...")
    technicals = watcher.check_technical_indicators("TSLA")
    if technicals.get("error"):
        print(f"  [WARN] Technicals error: {technicals['error']}")
    else:
        print(f"    RSI: {technicals.get('rsi', '?')}")
        print(f"    Trend: {technicals.get('trend', '?')}")
        print(f"    Volume ratio: {technicals.get('volume_ratio', '?')}x")
        if technicals.get("alerts"):
            for a in technicals["alerts"]:
                print(f"    ALERT: {a}")
except Exception as e:
    print(f"  [FAIL] {e}")

# 4. Test SECAgent
print("\n[4/6] Testing SECAgent (EDGAR filings)...")
try:
    from agents.sec_agent import SECAgent
    sec = SECAgent()
    filings = sec.get_recent_filings("TSLA", days_back=14)
    print(f"  [OK] Fetched {len(filings)} filings for TSLA")
    for f in filings[:3]:
        print(f"    - [{f['form_type']}] {f['description'][:60]}...")
except Exception as e:
    print(f"  [FAIL] {e}")

# 5. Test TwitterAgent
print("\n[5/6] Testing TwitterAgent (Nitter RSS + Google News fallback)...")
try:
    from agents.twitter_agent import TwitterAgent
    twitter = TwitterAgent()
    tweets = twitter.get_twitter_intel(max_queries=3)
    print(f"  [OK] Fetched {len(tweets)} X/Twitter signals")
    for t in tweets[:3]:
        print(f"    - [{t.get('account', t.get('query', ''))}] {t['title'][:60]}...")
except Exception as e:
    print(f"  [FAIL] {e}")

# 6. Test AnalystAgent (Ollama Connection)
print("\n[6/6] Testing AnalystAgent (Ollama/LLM)...")
try:
    from agents.analyst_agent import AnalystAgent
    analyst = AnalystAgent()
    test_response = analyst.analyze_sentiment("Test message: Is silver going up?")
    if "Error" in test_response:
        print(f"  [OLLAMA ERROR] {test_response}")
    else:
        print(f"  [OK] Ollama responded ({len(test_response)} chars)")
        print(f"    - {test_response[:150]}...")
except Exception as e:
    print(f"  [FAIL] {e}")

print("\n" + "=" * 60)
print("VERIFICATION COMPLETE")
print("=" * 60)
