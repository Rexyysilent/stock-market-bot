"""Quick smoke test for dip anticipation features."""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(
        sys.stdout.buffer, encoding="utf-8", errors="replace"
    )

sys.path.insert(0, '.')

from agents.watcher_agent import WatcherAgent
from agents.sec_agent import SECAgent
from config import GROWTH_BASKET, DEFENSIVE_BASKET, PUT_CALL_ALERT_THRESHOLD

print("=== IMPORT CHECK ===")
print(f"Growth basket: {GROWTH_BASKET}")
print(f"Defensive basket: {DEFENSIVE_BASKET}")
print(f"P/C threshold: {PUT_CALL_ALERT_THRESHOLD}")

w = WatcherAgent()
s = SECAgent()
print("Agents initialized OK")

# Check all methods exist
methods = [
    ("WatcherAgent.get_options_flow", hasattr(w, "get_options_flow")),
    ("WatcherAgent.get_all_options_flow", hasattr(w, "get_all_options_flow")),
    ("WatcherAgent.get_earnings_calendar", hasattr(w, "get_earnings_calendar")),
    ("WatcherAgent.get_sector_rotation", hasattr(w, "get_sector_rotation")),
    ("WatcherAgent._detect_rsi_divergence", hasattr(w, "_detect_rsi_divergence")),
    ("SECAgent.detect_insider_clusters", hasattr(s, "detect_insider_clusters")),
]
print("\n=== METHOD CHECK ===")
all_pass = True
for name, exists in methods:
    status = "OK" if exists else "MISSING"
    print(f"  {name}: {status}")
    if not exists:
        all_pass = False

print(f"\n=== LIVE TEST: Options flow for TSLA ===")
try:
    flow = w.get_options_flow("TSLA")
    print(f"  P/C Vol Ratio: {flow.get('put_call_vol_ratio', 'N/A')}")
    print(f"  P/C OI Ratio: {flow.get('put_call_oi_ratio', 'N/A')}")
    print(f"  Alerts: {flow.get('alerts', [])}")
    print(f"  Error: {flow.get('error', 'none')}")
except Exception as e:
    print(f"  Error: {e}")

print(f"\n=== LIVE TEST: Earnings calendar (first 3) ===")
try:
    earnings = w.get_earnings_calendar()
    for e in earnings[:3]:
        print(f"  {e['ticker']}: {e['earnings_date']} ({e['days_until']:+d} days)")
    if not earnings:
        print("  No earnings data found")
except Exception as e:
    print(f"  Error: {e}")

print(f"\n=== LIVE TEST: Sector rotation ===")
try:
    rotation = w.get_sector_rotation()
    print(f"  Signal: {rotation.get('signal', 'N/A')}")
    print(f"  Growth 5d: {rotation.get('growth_5d', 0):+.1f}%")
    print(f"  Defensive 5d: {rotation.get('defensive_5d', 0):+.1f}%")
    print(f"  Spread: {rotation.get('spread_5d', 0):+.1f}%")
    if rotation.get('alerts'):
        for a in rotation['alerts']:
            print(f"  Alert: {a}")
except Exception as e:
    print(f"  Error: {e}")

print(f"\n=== LIVE TEST: RSI divergence (via technicals for TSLA) ===")
try:
    tech = w.check_technical_indicators("TSLA")
    print(f"  RSI: {tech.get('rsi', 'N/A')}")
    print(f"  RSI Divergence: {tech.get('rsi_divergence', 'None detected')}")
    if tech.get('alerts'):
        for a in tech['alerts']:
            print(f"  Alert: {a}")
except Exception as e:
    print(f"  Error: {e}")

if all_pass:
    print("\n=== ALL CHECKS PASSED ===")
else:
    print("\n=== SOME CHECKS FAILED ===")
