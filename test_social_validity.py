"""M12-M14 social missingness and comparable-snapshot regression checks."""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

import agents.social_agent as social_module
from agents.social_agent import SocialAgent
import signals


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def source_row(ticker, rank, mentions):
    return {
        "rank": str(rank), "ticker": ticker, "name": ticker,
        "mentions": str(mentions), "upvotes": "0",
        "rank_24h_ago": str(rank), "mentions_24h_ago": str(mentions),
    }


def snapshot(status="observed", coverage="complete", rows=None,
             reason=None, comparable=None):
    if comparable is None:
        comparable = status == "observed" and coverage == "complete"
    return {
        "source": "ApeWisdom", "filter": "all-stocks", "scope": "top-200",
        "observation_status": status, "coverage_status": coverage,
        "reason": reason, "comparable": comparable, "observed_at": None,
        "rows": list(rows or []),
    }


def row(ticker, mentions, status, collection_status):
    return {
        "ticker": ticker, "filter": "all-stocks", "universe_member": True,
        "mentions": mentions, "upvotes": 0 if mentions is not None else None,
        "observation_status": status,
        "observation_reason": None if mentions is not None else "http_503",
        "collection_status": collection_status,
    }


original_get = social_module.requests.get
original_pages = social_module.APEWISDOM_UNIVERSE_PAGES
original_history = signals.SOCIAL_HISTORY_FILE
original_client_id = os.environ.get("REDDIT_CLIENT_ID")
original_client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
tmpdir = tempfile.mkdtemp(prefix="social_validity_")

try:
    os.environ.pop("REDDIT_CLIENT_ID", None)
    os.environ.pop("REDDIT_CLIENT_SECRET", None)
    signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "social.json")

    # M12: all requested pages succeeded, but a ticker beyond the bounded
    # leaderboard depth is censored. A row actually present remains measured.
    social_module.APEWISDOM_UNIVERSE_PAGES = 1

    def bounded_get(url, headers=None, timeout=None):
        if url.endswith("/all-stocks"):
            return FakeResponse(200, {"results": [source_row("TSLA", 1, 12)]})
        if url.endswith("/4chan"):
            return FakeResponse(200, {"results": []})
        raise AssertionError(f"unexpected URL: {url}")

    social_module.requests.get = bounded_get
    agent = SocialAgent()
    agent.SOCIAL_UNIVERSE = ("TSLA", "ROKU")
    attention, _ = agent._build_social_attention()
    by_ticker = {item["ticker"]: item for item in attention
                 if item.get("universe_member")}
    assert by_ticker["TSLA"]["mentions"] == 12
    assert by_ticker["TSLA"]["observation_status"] == "observed"
    assert by_ticker["TSLA"]["collection_status"] == "observed"
    assert by_ticker["TSLA"]["coverage_status"] == "censored"
    assert by_ticker["ROKU"]["mentions"] is None
    assert by_ticker["ROKU"]["upvotes"] is None
    assert by_ticker["ROKU"]["in_leaderboard"] is None
    assert by_ticker["ROKU"]["observation_status"] == "censored"
    assert by_ticker["ROKU"]["observation_reason"] == "not_in_scanned_leaderboard"
    bounded_snapshot = agent.get_apewisdom_top200()
    assert bounded_snapshot["coverage_status"] == "censored"
    assert bounded_snapshot["comparable"] is False

    alerts = signals.update_social_signals(
        attention, bounded_snapshot, "2026-09-01",
        mcap_lookup=lambda ticker, now=None: 850.0,
    )
    assert alerts == []
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as handle:
        state = json.load(handle)
    assert state["tickers"]["TSLA"][0]["mentions"] == 12
    assert "ROKU" not in state["tickers"]
    assert state["top200_history"] == []

    # M14: a source-provided zero is a typed measurement and remains zero.
    social_module.requests.get = lambda *args, **kwargs: FakeResponse(
        200, {"results": [source_row("ROKU", 1, 0)]}
    )
    zero = SocialAgent()._fetch_apewisdom("all-stocks", limit=1, pages=1)[0]
    assert zero["mentions"] == 0
    assert zero["observation_status"] == "explicit_zero"
    assert zero["observation_reason"] == "source_reported_zero"
    zero.update({"universe_member": True, "in_leaderboard": True})
    signals.update_social_signals(
        [zero], snapshot(status="unavailable", coverage="unavailable"),
        "2026-09-02", mcap_lookup=lambda ticker, now=None: 850.0,
    )
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as handle:
        state = json.load(handle)
    assert state["tickers"]["ROKU"][-1]["mentions"] == 0
    assert state["tickers"]["ROKU"][-1]["observation_status"] == "explicit_zero"

    # M13: five failures seed neither count baselines nor certified absences;
    # the first recovered top-200 observation cannot become an entrance.
    signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "outage.json")
    failed = snapshot("unavailable", "unavailable", reason="http_503")
    for day in range(1, 6):
        alerts = signals.update_social_signals(
            [row("DNN", None, "unavailable", "unavailable")], failed,
            f"2026-09-{day:02d}", mcap_lookup=lambda ticker, now=None: 850.0,
        )
        assert alerts == []
    recovered_rows = [{"ticker": "DNN", "rank": 150, "mentions": 30}]
    alerts = signals.update_social_signals(
        [row("DNN", 30, "observed", "observed")],
        snapshot(rows=recovered_rows), "2026-09-06",
        mcap_lookup=lambda ticker, now=None: 850.0,
    )
    assert alerts == []
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as handle:
        outage_state = json.load(handle)
    assert len(outage_state["tickers"]["DNN"]) == 1
    assert len(outage_state["top200_history"]) == 1

    # Five complete, comparable absences do establish a top-200 entrance.
    signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "entrance.json")
    for day in range(1, 6):
        signals.update_social_signals(
            [], snapshot(rows=[]), f"2026-08-{day:02d}",
            mcap_lookup=lambda ticker, now=None: 850.0,
        )
    alerts = signals.update_social_signals(
        [], snapshot(rows=recovered_rows), "2026-08-06",
        mcap_lookup=lambda ticker, now=None: 850.0,
    )
    assert [item["tag"] for item in alerts] == ["TOP200_ENTRANCE"]
    assert alerts[0]["ticker"] == "DNN"

    # Partial-page failure preserves a found row but advances neither baseline
    # nor certified absence history.
    def partial_get(url, headers=None, timeout=None):
        if url.endswith("/all-stocks"):
            return FakeResponse(200, {"results": [source_row("TSLA", 1, 9)]})
        return FakeResponse(503)

    social_module.requests.get = partial_get
    partial_agent = SocialAgent()
    partial_rows = partial_agent._fetch_apewisdom(
        "all-stocks", limit=None, pages=2
    )
    assert partial_rows[0]["observation_status"] == "observed"
    assert partial_rows[0]["collection_status"] == "partial"
    signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "partial.json")
    partial_rows[0].update({"universe_member": True})
    signals.update_social_signals(
        partial_rows, snapshot("partial", "partial", partial_rows, "http_503"),
        "2026-09-01", mcap_lookup=lambda ticker, now=None: 850.0,
    )
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as handle:
        partial_state = json.load(handle)
    assert partial_state["tickers"] == {}
    assert partial_state["top200_history"] == []

    # Duplicate membership across page boundaries makes coverage partial.
    def moving_get(url, headers=None, timeout=None):
        rank = 2 if "/page/2" in url else 1
        return FakeResponse(200, {"results": [source_row("TSLA", rank, 9)]})

    social_module.requests.get = moving_get
    moving_agent = SocialAgent()
    moving_agent._fetch_apewisdom("all-stocks", limit=None, pages=2)
    moving_status = moving_agent.get_apewisdom_collection_status()["all-stocks"]
    assert moving_status["status"] == "partial"
    assert moving_status["reason"] == "page_boundary_overlap"

    # A copied/unversioned state file is refused rather than trusted.
    signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "legacy.json")
    legacy_runs = [
        {
            "date": f"2026-07-{day:02d}", "tickers": [],
            "source": "ApeWisdom", "filter": "all-stocks",
            "scope": "top-200", "observation_status": "observed",
            "coverage_status": "complete", "comparable": True,
        }
        for day in range(1, 6)
    ]
    with open(signals.SOCIAL_HISTORY_FILE, "w", encoding="utf-8") as handle:
        json.dump({"tickers": {"DNN": [{"date": "2026-07-01", "mentions": 0}]},
                   "top200_history": legacy_runs}, handle)
    alerts = signals.update_social_signals(
        [], snapshot(rows=recovered_rows), "2026-07-06",
        mcap_lookup=lambda ticker, now=None: 850.0,
    )
    assert alerts == []
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as handle:
        reset_state = json.load(handle)
    assert reset_state["contract_version"] == signals.SOCIAL_STATE_CONTRACT_VERSION
    assert reset_state["tickers"] == {}
    assert len(reset_state["top200_history"]) == 1

finally:
    social_module.requests.get = original_get
    social_module.APEWISDOM_UNIVERSE_PAGES = original_pages
    signals.SOCIAL_HISTORY_FILE = original_history
    shutil.rmtree(tmpdir, ignore_errors=True)
    if original_client_id is not None:
        os.environ["REDDIT_CLIENT_ID"] = original_client_id
    if original_client_secret is not None:
        os.environ["REDDIT_CLIENT_SECRET"] = original_client_secret

# The renamed entrance tag must still reach the outcome ledger, as its own
# subtype; legacy ATTENTION_BIRTH archives remain readable unchanged.
from ledger.db import connect as ledger_connect
from ledger.ingest import ingest_file

ledger_dir = tempfile.mkdtemp()
try:
    archive = os.path.join(ledger_dir, "2026-09-21_120000Z.json")
    with open(archive, "w", encoding="utf-8") as handle:
        json.dump({
            "schema_version": "2.8",
            "pipeline_version": "2.6.5",
            "generated_at": "2026-09-21T12:00:00Z",
            "universe": {"instrumented_tickers": ["TSLA", "ROKU"]},
            "sections": {"social_alerts": [
                {"ticker": "TSLA", "tag": "TOP200_ENTRANCE",
                 "record_id": "entrance-new"},
                {"ticker": "ROKU", "tag": "ATTENTION_BIRTH",
                 "record_id": "entrance-legacy"},
            ]},
        }, handle)
    conn = ledger_connect(os.path.join(ledger_dir, "ledger.db"))
    ingest_file(archive, conn)
    stored = {
        row["ticker"]: row["subtype"] for row in conn.execute(
            "SELECT ticker, subtype FROM signals WHERE family='attention'"
        )
    }
    conn.close()
    assert stored == {"TSLA": "TOP200_ENTRANCE", "ROKU": "ATTENTION_BIRTH"}, stored
finally:
    shutil.rmtree(ledger_dir, ignore_errors=True)

print("Social validity M12-M14 regression checks passed")
