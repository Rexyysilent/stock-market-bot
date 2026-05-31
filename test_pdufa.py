"""Quick test for PDUFA catalyst scanner + cash runway checker."""
import io
import sys

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, '.')
from agents.research_agent import ResearchAgent

ra = ResearchAgent()

print("=== PDUFA CATALYSTS TEST ===")
cats = ra.get_pdufa_catalysts(days_ahead=60)
print(f"Found {len(cats)} catalysts")
for c in cats[:5]:
    src = c['source']
    title = c['title'][:80]
    pri = c['is_priority']
    days = c.get('days_until', '?')
    print(f"  [{src}] {title} (priority={pri}, days={days})")

print()
print("=== CASH RUNWAY TEST ===")
test_tickers = ['CRSP', 'NTLA', 'RGNX']
for ticker in test_tickers:
    print(f"  Checking {ticker}...")
    d = ra.check_cash_runway(ticker)
    if 'error' in d:
        print(f"    ERROR: {d['error']}")
    else:
        print(f"    {ticker}: {d['risk_level']} | Cash {d['cash_formatted']} | Burn {d['burn_formatted']} | Runway {d['runway_quarters']}Q")

print()
print("=== COMBINED TEST ===")
combo = ra.get_pdufa_with_financials(days_ahead=60)
print(f"Catalysts: {len(combo['catalysts'])}")
print(f"Financials: {len(combo['financials'])}")
print(f"Alerts: {len(combo['alerts'])}")
for a in combo['alerts']:
    print(f"  ALERT: {a['message']}")

print()
print("=== TEST PASSED ===")
