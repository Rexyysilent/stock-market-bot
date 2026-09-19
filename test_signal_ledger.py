"""Offline Tier-1 Signal Ledger acceptance checks."""
import hashlib
import io
import json
import logging
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

from config import EDITORIAL_ONLY_TICKERS
from ledger.db import connect
from ledger.ingest import ingest_file
from ledger.outcomes import mature_outcomes
from ledger.stats import write_stats


def brief(version, generated, suffix):
    return {
        "schema_version": "2.5",
        "pipeline_version": version,
        "generated_at": generated,
        "sections": {
            "regime": {"regime": "RISK_ON"},
            "insider_clusters": [
                {"ticker": "TSLA", "cluster_direction": "buy",
                 "alert_level": "HIGH", "record_id": "same-insider"}
            ],
            "confluence": [
                {"ticker": "TSLA", "families": ["options", "social"],
                 "confluence_score": 2, "record_id": f"conf-{suffix}"},
                {"ticker": "TSLA", "families": ["insider", "options"],
                 "confluence_score": 2, "record_id": f"conf-2-{suffix}"},
            ],
            "baseline_alerts": [
                {"ticker": "SPY", "metric": "put_call_vol_ratio",
                 "signal": "BULLISH_VS_BASELINE", "z_score": -3.2,
                 "record_id": f"spy-{suffix}"},
                {"ticker": "STTDF", "metric": "put_call_vol_ratio",
                 "signal": "BEARISH_VS_BASELINE", "z_score": 3.1,
                 "record_id": f"sttdf-{suffix}"},
            ],
            "options_flow": {
                "TSLA": {
                    "as_of": generated,
                    "option_contract_volume_oi_anomaly": [
                        {"ticker": "TSLA", "type": "PUT", "premium": 600000,
                         "strike": 300, "expiration": "2026-07-10", "as_of": generated},
                        {"ticker": "TSLA", "type": "CALL", "premium": 1500000,
                         "strike": 320, "expiration": "2026-07-10", "as_of": generated},
                    ],
                }
            },
            "social_alerts": [
                {"ticker": "TSLA",
                 "tag": "ATTENTION_BIRTH" if suffix == "a" else "SOCIAL_BURST",
                 "burst_ratio": 6.0,
                 "record_id": f"social-{suffix}"}
            ],
            "social_attention": [
                {"ticker": "TSLA", "burst_ratio": 6.0, "mentions": 20,
                 "universe_member": True, "filter": "all-stocks",
                 "is_low_volume": False, "record_id": f"attention-{suffix}"},
                # Same ratio but below the production 10-mention floor.
                {"ticker": "LMT", "burst_ratio": 7.0, "mentions": 7,
                 "universe_member": True, "filter": "all-stocks",
                 "is_low_volume": False, "record_id": f"low-base-{suffix}"},
                # No explicit alert: this genuinely qualifying row exercises
                # legacy/fallback extraction.
                {"ticker": "ROKU", "burst_ratio": 4.0, "mentions": 12,
                 "universe_member": True, "filter": "all-stocks",
                 "is_low_volume": False, "record_id": f"fallback-{suffix}"},
            ],
            "fda_catalysts": [
                {"ticker": "CRSP", "alert": "FDA_CATALYST_NEAR",
                 "fragility": {"runway_risk": "YELLOW"}, "record_id": f"fda-{suffix}"}
            ],
            "clinical_catalysts": [],
        },
    }


def schedule(start, end):
    rows = []
    for day in range(6, 31):
        current = date(2026, 7, day)
        if current.weekday() >= 5:
            continue
        opened = datetime(2026, 7, day, 13, 30, tzinfo=timezone.utc)
        closed = datetime(2026, 7, day, 20, 0, tzinfo=timezone.utc)
        rows.append((current.isoformat(), opened, closed))
    return rows


def provider(ticker, start, end):
    if ticker == "STTDF":
        return []
    base = {"SPY": 100.0, "TSLA": 200.0, "CRSP": 50.0}.get(ticker, 75.0)
    rows = []
    for session, _, _ in schedule(start, end):
        current = date.fromisoformat(session)
        if start <= current <= end:
            offset = (current - date(2026, 7, 6)).days
            rows.append({"session": session, "open": base + offset,
                         "close": base + offset + 1.0})
    return rows


with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    db_path = root / "ledger.db"
    first = root / "2026-07-06_140000Z.json"
    second = root / "2026-07-07_140000Z.json"
    first.write_text(json.dumps(brief("legacy-unversioned", "2026-07-06T14:00:00Z", "a")), encoding="utf-8")
    second.write_text(json.dumps(brief("2.5.1", "2026-07-07T14:00:00Z", "b")), encoding="utf-8")

    conn = connect(db_path)
    ingest_file(first, conn)
    before = [tuple(row) for row in conn.execute(
        "SELECT record_id,source_record_id,family,subtype,ticker,direction,strength,first_seen_run,payload FROM signals ORDER BY record_id"
    )]
    ingest_file(first, conn)
    after = [tuple(row) for row in conn.execute(
        "SELECT record_id,source_record_id,family,subtype,ticker,direction,strength,first_seen_run,payload FROM signals ORDER BY record_id"
    )]
    assert before == after, "same brief must be an immutable no-op"
    ingest_file(second, conn)

    # From pipeline 2.6.3 onward, the ledger independently rejects all
    # editorial-only tickers and arbitrary outsiders from every extractor.
    blocked = tuple(EDITORIAL_ONLY_TICKERS) + ("OUTSIDE",)
    strict_path = root / "2026-07-07_150000Z.json"
    strict_path.write_text(json.dumps({
        "schema_version": "2.7",
        "pipeline_version": "2.6.3",
        "generated_at": "2026-07-07T15:00:00Z",
        "sections": {
            "regime": {"regime": "RISK_ON"},
            "insider_clusters": [
                {"ticker": ticker, "cluster_direction": "buy", "record_id": f"i-{ticker}"}
                for ticker in blocked
            ],
            "confluence": [
                {"ticker": ticker, "families": ["social", "options"],
                 "confluence_score": 2, "record_id": f"c-{ticker}"}
                for ticker in blocked
            ],
            "baseline_alerts": [
                {"ticker": ticker, "metric": "put_call_vol_ratio",
                 "signal": "BEARISH_VS_BASELINE", "z_score": 3.0,
                 "record_id": f"b-{ticker}"}
                for ticker in blocked
            ],
            "options_flow": {
                ticker: {"option_contract_volume_oi_anomaly": [
                    {"ticker": ticker, "type": "CALL", "premium": 200000,
                     "as_of": "2026-07-07T15:00:00Z"}]}
                for ticker in blocked
            },
            "social_alerts": [
                {"ticker": ticker, "tag": "ATTENTION_BIRTH", "record_id": f"s-{ticker}"}
                for ticker in blocked
            ],
            "social_attention": [
                {"ticker": ticker, "burst_ratio": 8.0, "mentions": 50,
                 "universe_member": True, "filter": "all-stocks",
                 "is_low_volume": False, "record_id": f"a-{ticker}"}
                for ticker in blocked
            ],
            "fda_catalysts": [{"ticker": ticker, "alert": "FDA_CATALYST_NEAR"}
                              for ticker in blocked],
            "clinical_catalysts": [{"ticker": ticker, "fragile_alert": "FRAGILE_CATALYST"}
                                   for ticker in blocked],
        },
    }), encoding="utf-8")
    ingest_file(strict_path, conn)
    placeholders = ",".join("?" for _ in blocked)
    assert conn.execute(
        f"SELECT COUNT(*) AS n FROM signals WHERE ticker IN ({placeholders})", blocked
    ).fetchone()["n"] == 0

    malformed = root / "2026-07-08_140000Z.json"
    malformed_payload = {
        "schema_version": "2.5",
        "pipeline_version": "2.5.1",
        "generated_at": "2026-07-08T14:00:00Z",
        "sections": {
            "regime": {"regime": "RISK_ON"},
            "social_alerts": {"not": "a list"},
            "confluence": [{
                "ticker": "CRSP", "families": ["catalyst", "insider"],
                "confluence_score": 2, "record_id": "malformed-survivor",
            }],
        },
    }
    malformed.write_text(json.dumps(malformed_payload), encoding="utf-8")
    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    logging.getLogger("SignalLedger.Ingest").addHandler(handler)
    try:
        ingest_file(malformed, conn)
    finally:
        logging.getLogger("SignalLedger.Ingest").removeHandler(handler)
    assert "attention extraction failed" in log_stream.getvalue()
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM signals WHERE source_record_id='malformed-survivor'"
    ).fetchone()["n"] == 1

    # Same source id is measured independently across pipeline segments.
    insiders = conn.execute(
        "SELECT record_id FROM signals WHERE source_record_id='same-insider'"
    ).fetchall()
    assert len(insiders) == 2 and insiders[0]["record_id"] != insiders[1]["record_id"]

    # Explicit SOCIAL_BURST suppresses fallback duplication; options collapse.
    counts = dict(conn.execute(
        "SELECT family,COUNT(*) AS n FROM signals GROUP BY family"
    ).fetchall())
    assert set(counts) == {
        "attention", "baseline_alert", "catalyst", "confluence",
        "insider_cluster", "option_contract_volume_oi_anomaly",
    }
    assert counts["attention"] == 4  # explicit + one qualifying fallback/run
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM signals WHERE ticker='LMT'"
    ).fetchone()["n"] == 0
    option = conn.execute(
        "SELECT subtype,direction,strength,payload FROM signals WHERE family='option_contract_volume_oi_anomaly' ORDER BY record_id LIMIT 1"
    ).fetchone()
    payload = json.loads(option["payload"])
    assert option["subtype"] == "CALL" and option["strength"] == "1m+"
    assert option["direction"] == "none"
    assert payload["total_premium"] == 2100000 and len(payload["contracts"]) == 2
    assert payload["total_notional_estimate"] == 2100000
    conn.close()

    result = mature_outcomes(
        db_path=db_path,
        now=datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc),
        provider=provider,
        schedule_provider=schedule,
        universe=("SPY", "TSLA", "CRSP"),
    )
    assert result["filled"] > 0 and result["unpriceable"] == 0
    # A temporary empty response is retryable, not proof of terminal absence.
    conn = connect(db_path)
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM outcomes WHERE exit_session<=entry_session"
    ).fetchone()["n"] == 0
    for row in conn.execute(
        """SELECT o.entry_session,r.generated_at
           FROM outcomes o JOIN signals s ON s.record_id=o.record_id
           JOIN runs r ON r.run_id=s.first_seen_run
           WHERE o.status='filled'"""
    ):
        opened = next(
            session_open for session, session_open, _ in schedule(None, None)
            if session == row["entry_session"]
        )
        generated = datetime.fromisoformat(row["generated_at"].replace("Z", "+00:00"))
        assert opened >= generated
    spy = conn.execute(
        """SELECT excess FROM outcomes o JOIN signals s ON s.record_id=o.record_id
           WHERE s.ticker='SPY' AND o.status='filled' LIMIT 1"""
    ).fetchone()
    assert spy and abs(spy["excess"]) < 1e-12
    assert conn.execute(
        """SELECT COUNT(*) AS n FROM outcomes o JOIN signals s ON s.record_id=o.record_id
           WHERE s.ticker='STTDF' AND o.status='pending'"""
    ).fetchone()["n"] > 0
    conn.close()

    stats_json = root / "stats.json"
    stats_md = root / "stats.md"
    write_stats(db_path, stats_json, stats_md, min_n=1, bootstrap_samples=100)
    first_bytes = stats_json.read_bytes()
    write_stats(db_path, stats_json, stats_md, min_n=1, bootstrap_samples=100)
    assert stats_json.read_bytes() == first_bytes
    stats = json.loads(first_bytes)
    assert {segment["pipeline_version"] for segment in stats["segments"]} == {
        "legacy-unversioned", "2.5.1"
    }
    assert stats["config"]["n_definition"] == "unique (ticker, entry_session) clusters"
    legacy = next(
        segment for segment in stats["segments"]
        if segment["pipeline_version"] == "legacy-unversioned"
    )
    confluence = next(
        group for group in legacy["groups"]
        if group["grain"] == "family" and group["key"] == "confluence"
    )
    assert confluence["horizons"]["1"]["n"] == 1
    assert confluence["horizons"]["1"]["n_rows"] == 2

    rebuilt_db = root / "rebuilt.db"
    rebuilt_conn = connect(rebuilt_db)
    for source in (first, second, strict_path, malformed):
        ingest_file(source, rebuilt_conn)
    rebuilt_conn.close()
    mature_outcomes(
        db_path=rebuilt_db,
        now=datetime(2026, 7, 10, 22, 0, tzinfo=timezone.utc),
        provider=provider,
        schedule_provider=schedule,
        universe=("SPY", "TSLA", "CRSP"),
    )
    rebuilt_json = root / "rebuilt-stats.json"
    write_stats(rebuilt_db, rebuilt_json, root / "rebuilt-stats.md",
                min_n=1, bootstrap_samples=100)
    assert rebuilt_json.read_bytes() == first_bytes

print("Signal Ledger acceptance checks passed")
