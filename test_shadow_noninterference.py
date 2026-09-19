"""Frozen production projection/state equivalence across off and shadow modes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import agents.news_agent as news
import signals
import export_for_gemini as exporter
from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult
from config import EDITORIAL_COVERAGE_TICKERS, EDITORIAL_ONLY_TICKERS, SIGNAL_ELIGIBLE_TICKERS
from editorial_focus import select_focus
from ledger.db import connect
from ledger.ingest import ingest_file

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
def story(ticker, title):
    return {
        "title": title, "link": "https://" + ticker.lower() + ".example/release",
        "canonical_url": "https://" + ticker.lower() + ".example/release",
        "source_record_id": "frozen:" + ticker, "provider": ticker + " fixture",
        "publisher": ticker + " issuer", "publisher_domain": ticker.lower() + ".example",
        "source_class": "official", "source_time_kind": "published",
        "published": "2026-09-06T11:00:00Z", "provider_seen_at": "2026-09-06T11:05:00Z",
        "tickers": [ticker], "ticker_metadata_kind": "subject",
    }
class Frozen:
    name = "Frozen metadata"
    def __init__(self, rows):
        self.rows = rows
    def fetch(self):
        return ProviderResult(self.name, "ok", deepcopy(self.rows))

def projection(rows, mode):
    news.EDITORIAL_COVERAGE_MODE = mode
    agent = NewsAgent(now=NOW, providers=[Frozen(rows)])
    agent._google_enabled = False
    agent.get_global_headlines()
    selected = agent.get_scored_headlines()
    focus = select_focus(selected, SIGNAL_ELIGIBLE_TICKERS, NOW,
                         coverage_tickers=EDITORIAL_COVERAGE_TICKERS, coverage_mode=mode)
    return {"headlines": selected, "focus": focus}

cases = [
    [story("MRNA", "Moderna Phase 3 trial met primary endpoint")],
    [story("MRK", "Merck & Co. Phase 3 trial met primary endpoint")],
    [story("PLTR", "Palantir and NVIDIA announce AI partnership"),
     story("MRNA", "Moderna (NASDAQ: MRNA) Phase 3 trial met primary endpoint")],
    [story("TSLA", "Tesla reports earnings beat"),
     story("MRK", "Merck KGaA (ETR: MRK) announces acquisition")],
]
previous_mode = news.EDITORIAL_COVERAGE_MODE
try:
    for i, rows in enumerate(cases):
        assert projection(rows, "off") == projection(rows, "shadow"), i
finally:
    news.EDITORIAL_COVERAGE_MODE = previous_mode

# Exercise nonempty social/technical/registry/confluence state and ledger rows.
def state_projection(mode):
    news.EDITORIAL_COVERAGE_MODE = mode
    with tempfile.TemporaryDirectory(prefix="omni-shadow-state-") as directory:
        root = Path(directory)
        signals.SOCIAL_HISTORY_FILE = str(root / "social.json")
        signals.CTGOV_SNAPSHOT_FILE = str(root / "clinical.json")
        signals.ALERT_HISTORY_FILE = str(root / "alerts.json")
        exporter.BASELINE_STATE_FILE = str(root / "baselines.json")
        blocked = EDITORIAL_ONLY_TICKERS + ("OUTSIDE",)
        social_alerts = []
        baseline_alerts = []
        for day in range(1, 8):
            date = "2026-08-" + str(day).zfill(2)
            attention = [{
                "ticker": ticker, "filter": "all-stocks", "universe_member": True,
                "mentions": 50 if day == 7 else 10, "upvotes": 1,
            } for ticker in ("TSLA",) + blocked]
            social_alerts = signals.update_social_signals(
                attention, [], date, mcap_lookup=lambda *a, **k: None,
                observed_at=date + "T21:00:00Z",
            )
            _, baseline_alerts = exporter.update_baselines_and_score({}, {
                ticker: {"volume_ratio": 8 if day == 7 else 1, "as_of": date + "T20:00:00Z"}
                for ticker in ("TSLA",) + blocked
            }, date)
        trials = [{"nct_id": "NCT-" + t, "ticker": t, "status": "RECRUITING",
                   "primary_completion_date": "2027-01-01", "enrollment": 100}
                  for t in ("CRSP",) + blocked]
        signals.diff_registry(trials, "2026-08-06T12:00:00Z")
        for t in trials:
            t["enrollment"] = 120
        registry = signals.diff_registry(trials, "2026-08-07T12:00:00Z")
        events = [{"ticker": t, "family": family, "tag": family,
                   "event_at": "2026-08-07T20:00:00Z", "observed_at": "2026-08-07T21:00:00Z"}
                  for t in ("TSLA",) + blocked for family in ("social", "options", "technical")]
        confluence = signals.build_confluence(events, "2026-08-07T21:00:00Z")
        assert social_alerts and confluence and registry
        assert all(r["ticker"] not in blocked for r in social_alerts + baseline_alerts + confluence)
        doc = {"schema_version": "2.8", "pipeline_version": "2.6.4",
               "generated_at": "2026-08-07T21:00:00Z", "sections": {
                   "social_alerts": social_alerts, "confluence": confluence,
                   "insider_clusters": [{"ticker": t, "cluster_direction": "buy",
                                         "record_id": "insider-" + t}
                                        for t in ("TSLA",) + blocked],
               }}
        file = root / "2026-08-07_210000Z.json"
        file.write_text(json.dumps(doc), encoding="utf-8")
        conn = connect(root / "ledger.db")
        ingest_file(file, conn)
        ledger = [tuple(r) for r in conn.execute("SELECT ticker,family,record_id FROM signals ORDER BY record_id")]
        assert ledger and {r[0] for r in ledger} == {"TSLA"}
        conn.close()
        state = {p.name: json.loads(p.read_text()) for p in root.glob("*.json")
                 if p.name != file.name}
        assert set(state["social.json"]["tickers"]) == {"TSLA"}
        assert set(state["baselines.json"]["versions"]["2.6.4"]) == {"TSLA"}
        return state, social_alerts, baseline_alerts, registry, confluence, ledger

original = (signals.SOCIAL_HISTORY_FILE, signals.CTGOV_SNAPSHOT_FILE,
            signals.ALERT_HISTORY_FILE, exporter.BASELINE_STATE_FILE)
try:
    assert state_projection("off") == state_projection("shadow")
finally:
    (signals.SOCIAL_HISTORY_FILE, signals.CTGOV_SNAPSHOT_FILE,
     signals.ALERT_HISTORY_FILE, exporter.BASELINE_STATE_FILE) = original
    news.EDITORIAL_COVERAGE_MODE = previous_mode
print("PASS complete frozen selected/focus projection and nonempty production state/ledger equivalence")

