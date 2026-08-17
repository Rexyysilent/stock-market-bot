"""Offline regression: insider clusters mean distinct filers, not documents.

History (July 15 brief): the EDGAR fallback counted Form 4 documents with no
transaction codes, so "COIN: 10 insiders, HIGH" could have been one CFO
exercising options in ten tranches. A cluster is now >=2 DISTINCT filers
transacting the same open-market direction; identity comes from rptOwnerCik
(EDGAR XML) or the insider name (OpenInsider); records carry insider_count
(humans), buyers/sellers, filing_count (documents), code_counts (measurements
straight off the filing), cluster_direction, and source.

Alert asymmetry (Cohen-Malloy-Pomorski): purchase clusters are the headline
signal (HIGH at >=3 buyers) — insiders only buy for one reason — while sale
clusters are diversification/liquidity/10b5-1-contaminated context, capped at
LOW and excluded from confluence.
"""
import sys

sys.path.insert(0, ".")

from collections import Counter
from agents.sec_agent import SECAgent, FORM4_CODE_RE, FORM4_OWNER_RE

# --- direction: filer majority, tie = mixed, no open-market filers = null
assert SECAgent._cluster_direction(3, 1) == "buy"
assert SECAgent._cluster_direction(1, 4) == "sell"
assert SECAgent._cluster_direction(2, 2) == "mixed"
assert SECAgent._cluster_direction(0, 0) is None

# --- OpenInsider trade_type -> code (leading letter, verbatim from the table)
assert SECAgent._openinsider_code("P - Purchase") == "P"
assert SECAgent._openinsider_code("S - Sale+OE") == "S"
assert SECAgent._openinsider_code("s - sale") == "S"
assert SECAgent._openinsider_code("") is None
assert SECAgent._openinsider_code(None) is None
assert SECAgent._openinsider_code("Purchase") is None  # no code prefix -> not guessed

# --- Form 4 XML: codes and reporting-owner CIKs
form4_xml = """<ownershipDocument>
  <reportingOwner><reportingOwnerId>
    <rptOwnerCik>0001234567</rptOwnerCik><rptOwnerName>DOE JANE</rptOwnerName>
  </reportingOwnerId></reportingOwner>
  <nonDerivativeTransaction>
    <transactionCoding><transactionFormType>4</transactionFormType>
      <transactionCode>S</transactionCode></transactionCoding>
  </nonDerivativeTransaction>
  <nonDerivativeTransaction>
    <transactionCoding><transactionCode> S </transactionCode></transactionCoding>
  </nonDerivativeTransaction>
  <derivativeTransaction>
    <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
  </derivativeTransaction>
</ownershipDocument>"""
assert FORM4_CODE_RE.findall(form4_xml) == ["S", "S", "M"]
assert FORM4_OWNER_RE.findall(form4_xml) == ["1234567"]  # leading zeros stripped

# --- cluster gate: distinct same-direction filers, not filing volume
# one CFO, ten tranches -> no cluster
assert SECAgent._build_cluster("COIN", 1, 1, 10, {"cfo": {"P", "S"}},
                               Counter({"S": 8, "P": 2}), 30, "edgar_fallback") is None
# grants-only activity -> no cluster
assert SECAgent._build_cluster("COIN", 0, 0, 4, {"a": {"A"}, "b": {"M"}},
                               Counter({"A": 3, "M": 1}), 30, "edgar_fallback") is None

# --- alert asymmetry (Cohen-Malloy-Pomorski): buys headline, sells context
# three sellers -> sell cluster exists but never rises above LOW
c = SECAgent._build_cluster("COIN", 1, 3, 10, {"a": {"S"}, "b": {"S"}, "c": {"S", "M"}, "d": {"P"}},
                            Counter({"S": 6, "M": 2, "P": 1}), 30, "edgar_fallback")
assert c["insider_count"] == 3 and c["alert_level"] == "LOW"
assert c["buyers"] == 1 and c["sellers"] == 3
assert c["filing_count"] == 10 and c["unique_insiders"] == 4
assert c["cluster_direction"] == "sell" and c["source"] == "edgar_fallback"
# two buyers -> MEDIUM buy cluster; three -> HIGH
c = SECAgent._build_cluster("ROKU", 2, 0, 2, {"a": {"P"}, "b": {"P"}},
                            Counter({"P": 2}), 30, "openinsider")
assert c["insider_count"] == 2 and c["alert_level"] == "MEDIUM"
assert c["cluster_direction"] == "buy"
c = SECAgent._build_cluster("UUUU", 3, 1, 5, {"a": {"P"}, "b": {"P"}, "c": {"P"}, "d": {"S"}},
                            Counter({"P": 3, "S": 2}), 30, "openinsider")
assert c["alert_level"] == "HIGH" and c["cluster_direction"] == "buy"
# mixed (2v2) = contested buy evidence -> MEDIUM, not HIGH, not LOW
c = SECAgent._build_cluster("FCX", 2, 2, 6, {"a": {"P"}, "b": {"P"}, "c": {"S"}, "d": {"S"}},
                            Counter({"P": 2, "S": 4}), 30, "edgar_fallback")
assert c["alert_level"] == "MEDIUM" and c["cluster_direction"] == "mixed"

# --- ordering: buy > mixed > sell, filer count within each
ordered = SECAgent._sort_clusters([
    {"cluster_direction": "sell", "insider_count": 5},
    {"cluster_direction": "mixed", "insider_count": 2},
    {"cluster_direction": "buy", "insider_count": 2},
    {"cluster_direction": "buy", "insider_count": 4},
])
assert [(c["cluster_direction"], c["insider_count"]) for c in ordered] == [
    ("buy", 4), ("buy", 2), ("mixed", 2), ("sell", 5)]

# --- OpenInsider path end-to-end (no network)
agent = SECAgent.__new__(SECAgent)  # skip __init__: no session, no locks needed


class FakeOpenInsider:
    def get_recent_trades(self, days_back=30, limit=500):
        return [
            # COIN: one insider selling in three tranches + one distinct seller
            {"ticker": "COIN", "trade_type": "S - Sale", "filing_date": "2026-07-10", "insider_name": "Same Cfo"},
            {"ticker": "COIN", "trade_type": "S - Sale+OE", "filing_date": "2026-07-11", "insider_name": "Same Cfo"},
            {"ticker": "COIN", "trade_type": "S - Sale", "filing_date": "2026-07-12", "insider_name": "SAME CFO"},
            {"ticker": "COIN", "trade_type": "S - Sale", "filing_date": "2026-07-12", "insider_name": "Other Vp"},
            # ROKU: one director buying in two tranches -> NOT a cluster
            {"ticker": "ROKU", "trade_type": "P - Purchase", "filing_date": "2026-07-10", "insider_name": "Lone Director"},
            {"ticker": "ROKU", "trade_type": "P - Purchase", "filing_date": "2026-07-11", "insider_name": "Lone Director"},
            # off-watchlist and single-row tickers never cluster
            {"ticker": "ZZZZ", "trade_type": "S - Sale", "filing_date": "2026-07-10", "insider_name": "F"},
            {"ticker": "TSLA", "trade_type": "S - Sale", "filing_date": "2026-07-10", "insider_name": "G"},
        ]


agent.openinsider = FakeOpenInsider()
clusters = {c["ticker"]: c for c in agent._detect_openinsider_clusters(days_back=30)}

assert set(clusters) == {"COIN"}, clusters  # ROKU = one human, not a cluster
coin = clusters["COIN"]
assert coin["insider_count"] == 2  # Same Cfo (case-folded) + Other Vp
assert coin["filing_count"] == 4
assert coin["code_counts"] == {"S": 4}
assert coin["cluster_direction"] == "sell"
assert coin["alert_level"] == "LOW"  # sell cluster = context, never above LOW
assert coin["source"] == "openinsider"


# --- EDGAR document fetch: XSL rewrite, failure honesty
class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


fetched_urls = []


def fake_get(url, params=None, timeout=15, max_attempts=3):
    fetched_urls.append(url)
    return FakeResponse(200, form4_xml)


agent._get_with_retries = fake_get
details = agent._fetch_form4_details({
    "primary_doc_url": "https://www.sec.gov/Archives/edgar/data/1679788/000167978826000042/xslF345X05/wk-form4.xml"
})
assert details == {"codes": ["S", "S", "M"], "owners": ["1234567"]}
# XSL-rendered path was rewritten to the raw XML
assert fetched_urls[-1].endswith("/000167978826000042/wk-form4.xml"), fetched_urls[-1]

# non-XML primary doc or missing URL -> None (counted as a fetch failure upstream)
assert agent._fetch_form4_details({"primary_doc_url": "https://x/form4-index.htm"}) is None
assert agent._fetch_form4_details({}) is None
agent._get_with_retries = lambda *a, **k: FakeResponse(404)
assert agent._fetch_form4_details({"primary_doc_url": "https://x/wk-form4.xml"}) is None

# --- txt renderer suffix
from export_for_gemini import format_insider_direction

assert format_insider_direction(
    {"code_counts": {"S": 8, "M": 2}, "cluster_direction": "sell",
     "buyers": 0, "sellers": 3, "source": "edgar_fallback"}
) == " | direction=sell (buyers 0/sellers 3) | codes M:2 S:8 (edgar_fallback)"
assert format_insider_direction({"code_counts": {}, "cluster_direction": None}) == ""
assert format_insider_direction({}) == ""

print("Insider distinct-filer cluster checks passed")
