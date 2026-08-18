"""Offline checks for signals.py sniper derivations (Task 1: burst + entrances).

State files are redirected to a temp dir; market-cap lookups are injected —
no network, no yfinance.
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, ".")

import signals

tmpdir = tempfile.mkdtemp(prefix="sniper_test_")
signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "social_history.json")
signals.MCAP_CACHE_FILE = os.path.join(tmpdir, "mcap_cache.json")


def universe_row(ticker, mentions, upvotes=0):
    return {"ticker": ticker, "filter": "all-stocks", "universe_member": True,
            "mentions": mentions, "upvotes": upvotes}


def no_mcap(ticker, now=None):
    raise AssertionError(f"mcap lookup should not fire here (asked for {ticker})")


try:
    # --- 1b warm-up: burst_ratio null until 5 prior runs exist --------------
    for day in range(1, 6):  # runs 1..5 (0..4 prior entries)
        rows = [universe_row("TSLA", 10), universe_row("DNN", 2)]
        alerts = signals.update_social_signals(rows, [], f"2026-07-{day:02d}",
                                               mcap_lookup=no_mcap)
        assert rows[0]["burst_ratio"] is None, f"run {day} should be warm-up"
        assert rows[1]["burst_ratio"] is None
        assert alerts == []

    # --- run 6: 5 prior entries -> burst computes ----------------------------
    # TSLA: median(10,10,10,10,10)=10; 35/10 = 3.5 -> both gates pass -> alert
    # DNN:  median(2,2,2,2,2)=2 (>1 floor); 8/2 = 4.0 but mentions 8 < 10 -> no alert
    rows = [universe_row("TSLA", 35), universe_row("DNN", 8)]
    alerts = signals.update_social_signals(rows, [], "2026-07-06",
                                           mcap_lookup=no_mcap)
    assert rows[0]["burst_ratio"] == 3.5
    assert rows[1]["burst_ratio"] == 4.0
    assert [a["ticker"] for a in alerts] == ["TSLA"], alerts
    assert alerts[0]["tag"] == "SOCIAL_BURST"
    assert alerts[0]["burst_ratio"] == 3.5 and alerts[0]["mentions"] == 35

    # Non-universe / non-all-stocks rows are ignored, never enter history
    rows = [dict(universe_row("MU", 500), universe_member=False),
            dict(universe_row("TSLA", 500), filter="4chan")]
    alerts = signals.update_social_signals(rows, [], "2026-07-06",
                                           mcap_lookup=no_mcap)
    assert "burst_ratio" not in rows[0] and "burst_ratio" not in rows[1]
    assert alerts == []

    # --- same-date rerun is idempotent: replaces, doesn't append -------------
    rows = [universe_row("TSLA", 12)]
    signals.update_social_signals(rows, [], "2026-07-06", mcap_lookup=no_mcap)
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as f:
        state = json.load(f)
    tsla_hist = state["tickers"]["TSLA"]
    assert len(tsla_hist) == 6, tsla_hist  # 5 warm-up days + one 07-06 entry
    assert tsla_hist[-1] == {"date": "2026-07-06", "mentions": 12, "upvotes": 0}
    # burst still computed against the 5 PRIOR days only (median 10): 12/10
    assert rows[0]["burst_ratio"] == 1.2

    # --- 1c entrances: warm after 5 prior runs of top-200 history ------------
    # The 7 runs above each stored an (empty) top-200 set, so history is warm.
    top200 = [
        {"ticker": "ABCD", "rank": 150, "mentions": 12},   # small cap -> alert
        {"ticker": "MEGA", "rank": 10, "mentions": 900},   # mega cap -> gated out
        {"ticker": "NOPE", "rank": 60, "mentions": 5},     # mcap unknown -> gated out
    ]
    mcaps = {"ABCD": 850.0, "MEGA": 50_000.0, "NOPE": None}
    alerts = signals.update_social_signals([], top200, "2026-07-07",
                                           mcap_lookup=lambda t, now=None: mcaps[t])
    assert [a["ticker"] for a in alerts] == ["ABCD"], alerts
    birth = alerts[0]
    assert birth["tag"] == "ATTENTION_BIRTH"
    assert birth["rank"] == 150 and birth["mentions"] == 12
    assert birth["market_cap_musd"] == 850.0

    # Next run: ABCD was in yesterday's top-200 -> no longer an entrance
    alerts = signals.update_social_signals([], top200, "2026-07-08",
                                           mcap_lookup=lambda t, now=None: mcaps[t])
    assert alerts == [], alerts

    # top-200 history trimmed to TOP200_HISTORY_RUNS
    with open(signals.SOCIAL_HISTORY_FILE, encoding="utf-8") as f:
        state = json.load(f)
    assert len(state["top200_history"]) == signals.TOP200_HISTORY_RUNS

    # --- entrances silent until 5 prior runs exist ---------------------------
    signals.SOCIAL_HISTORY_FILE = os.path.join(tmpdir, "cold_history.json")
    alerts = signals.update_social_signals([], top200, "2026-07-07",
                                           mcap_lookup=no_mcap)
    assert alerts == [], "cold state must not emit entrances"

    # --- mcap weekly cache: fresh entry served without any fetch -------------
    with open(signals.MCAP_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"CACHED": {"market_cap_musd": 123.4,
                              "fetched_at": "2026-07-06T00:00:00"}}, f)
    from datetime import datetime
    assert signals.get_mcap_musd("CACHED", now=datetime(2026, 7, 8)) == 123.4

    # ===== Task 2a: FDA catalyst keyword miner ================================
    RUN = "2026-07-06"

    # SEC filing metadata hit: ticker from the record, date near keyword
    sec = [{"ticker": "VNDA", "title": "Vanda Pharmaceuticals Inc.",
            "description": "8-K: Complete Response Letter received", "date": "2026-07-02",
            "filed_at": "2026-07-02T16:05:00.000Z", "link": "sec://1"}]
    # biotech_news: ticker attributed via alias/symbol match on the title
    news = [
        {"title": "Vanda receives PDUFA action date of March 15, 2027",
         "link": "n://1", "as_of": "2026-07-01T00:00:00Z"},
        {"title": "FDA advisory committee to review CRSP therapy on August 1, 2026",
         "link": "n://2", "as_of": "2026-07-03T00:00:00Z"},
        {"title": "Intellia wins priority review", "link": "n://3"},   # undated mention
        {"title": "Generic biotech story with no universe ticker, PDUFA soon",
         "link": "n://4"},                                             # no ticker -> dropped
        {"title": "MacroLendingCRLtd quarterly update for Cameco",     # CRL word-bounded
         "link": "n://5"},
    ]
    ceo = [
        {"ticker": "UUUU", "title": "Energy Fuels fast track designation chatter 2026-08-15",
         "link": "c://1", "date": "2026-07-05T12:00:00Z"},
        {"ticker": "SECTOR", "title": "PDUFA macro musing", "link": "c://2"},  # excluded
    ]
    adcom = [
        {"date": "2026-09-10", "title": "Meeting to discuss Intellia NTLA-2001",
         "link": "https://www.fda.gov/x"},
        {"date": "2026-09-11", "title": "Committee on something unrelated",
         "link": "https://www.fda.gov/y"},
        {"date": "2026-07-29", "title": "Generic committee title",
         "agenda": "BLA 125842 from Capricor, Inc. for deramiocel",
         "ticker": "CAPR", "as_of": "2026-07-16T00:00:00Z",
         "link": "https://www.fda.gov/z"},
    ]

    recs = signals.mine_fda_catalysts(sec, news, ceo, RUN, adcom_meetings=adcom)
    by_key = {(r["ticker"], r["event_type"]): r for r in recs}

    crl = by_key[("VNDA", "CRL")]
    assert crl["source"] == "sec_filing" and crl["event_date"] is None
    assert crl["as_of"] == "2026-07-02T16:05:00.000Z"

    pdufa = by_key[("VNDA", "PDUFA_DATE")]
    assert pdufa["event_date"] == "2027-03-15"          # Month DD, YYYY
    assert pdufa["days_until"] == 252 and pdufa["alert"] is None  # > 90d out

    adcom_crsp = by_key[("CRSP", "ADCOM")]
    assert adcom_crsp["event_date"] == "2026-08-01"
    assert adcom_crsp["alert"] == "FDA_CATALYST_NEAR"   # 26 days out

    prio = by_key[("NTLA", "PRIORITY_REVIEW")]
    assert prio["event_date"] is None and prio["days_until"] is None
    assert prio["alert"] is None                        # mention is a signal, not near-alert

    fast = by_key[("UUUU", "DESIGNATION")]
    assert fast["source"] == "ceo_ca" and fast["event_date"] == "2026-08-15"  # ISO format
    assert fast["alert"] == "FDA_CATALYST_NEAR"

    ntla_adcom = by_key[("NTLA", "ADCOM")]
    assert ntla_adcom["source"] == "fda_calendar" and ntla_adcom["event_date"] == "2026-09-10"

    capr_adcom = by_key[("CAPR", "ADCOM")]
    assert capr_adcom["event_date"] == "2026-07-29"
    assert capr_adcom["as_of"] == "2026-07-16T00:00:00Z"
    assert "Capricor" in capr_adcom["headline"]

    # Word-bounded CRL: "MacroLendingCRLtd" must NOT create a CCJ CRL record
    assert ("CCJ", "CRL") not in by_key
    # No-universe-ticker headline and SECTOR channel produce nothing
    tickers_seen = {r["ticker"] for r in recs}
    assert "SECTOR" not in tickers_seen
    assert len(recs) == 7, [(r["ticker"], r["event_type"]) for r in recs]

    # Dedupe on (ticker, event_type, event_date): same event via two sources
    dup_news = [{"title": "Vanda complete response letter follow-up", "link": "n://9"}]
    recs2 = signals.mine_fda_catalysts(sec, dup_news, [], RUN)
    crls = [r for r in recs2 if r["event_type"] == "CRL"]
    assert len(crls) == 1 and crls[0]["source"] == "sec_filing"  # first source wins

    # DD Month YYYY date form
    recs3 = signals.mine_fda_catalysts(
        [], [{"title": "Rivian PDUFA set for 15 March 2027", "link": "n://10"}], [], RUN)
    assert recs3[0]["ticker"] == "RIVN" and recs3[0]["event_date"] == "2027-03-15"

    # ===== Task 3a: CT.gov registry diff engine ===============================
    signals.CTGOV_SNAPSHOT_FILE = os.path.join(tmpdir, "ctgov_snapshot.json")

    def trial(nct, status="RECRUITING", pcd="2027-01-15", enroll=100, ticker="CRSP"):
        return {"nct_id": nct, "status": status, "primary_completion_date": pcd,
                "enrollment": enroll, "ticker": ticker, "title": f"Trial {nct}"}

    day1 = [trial("NCT001"), trial("NCT002", ticker=None), trial("NCT003")]
    assert signals.diff_registry(day1, "2026-07-06T09:00:00") == [], \
        "first run seeds and emits [] — no fake diffs against nothing"

    day2 = [
        trial("NCT001", status="TERMINATED"),                # violent negative
        trial("NCT002", pcd="2027-02-20", ticker=None),      # 36-day slip
        trial("NCT003", enroll=115),                         # +15% enrollment
        trial("NCT004"),                                     # newly tracked: no diff
    ]
    changes = signals.diff_registry(day2, "2026-07-07T09:00:00")
    by_type = {(c["nct_id"], c["change_type"]): c for c in changes}
    assert len(changes) == 3, changes

    flip = by_type[("NCT001", "STATUS_FLIP")]
    assert flip["severity"] == "high" and flip["old"] == "RECRUITING"
    assert flip["new"] == "TERMINATED" and flip["ticker"] == "CRSP"
    assert flip["as_of"] == "2026-07-07T09:00:00"

    slip = by_type[("NCT002", "DATE_SLIP")]
    assert slip["delta_days"] == 36 and slip["severity"] == "normal"
    assert slip["ticker"] is None

    enr = by_type[("NCT003", "ENROLLMENT_CHANGE")]
    assert enr["old"] == 100 and enr["new"] == 115 and enr["delta_pct"] == 15.0

    # Same-date reruns retain the day's changes after the checkpoint advances.
    same_day = signals.diff_registry(day2, "2026-07-07T18:30:00Z")
    assert same_day == changes

    # Below-threshold moves are noise, not changes; quiet day -> []
    day3 = [
        trial("NCT001", status="TERMINATED"),        # unchanged from day 2
        trial("NCT002", pcd="2027-02-25", ticker=None),  # +5d < 14d threshold
        trial("NCT003", enroll=110),                 # -4.3% < 10% threshold
        trial("NCT004"),
    ]
    assert signals.diff_registry(day3, "2026-07-08T09:00:00") == []

    # A trial absent from today's fetch keeps its last-seen snapshot state
    with open(signals.CTGOV_SNAPSHOT_FILE, encoding="utf-8") as f:
        snap = json.load(f)["trials"]
    assert set(snap) == {"NCT001", "NCT002", "NCT003", "NCT004"}
    assert snap["NCT001"]["status"] == "TERMINATED"

    # ===== Task 3b: fragility join ============================================
    financials = {
        "CRSP": {"risk_level": "GREEN", "market_cap": 4_500_000_000},
        "RGNX": {"risk_level": "RED", "market_cap": 800_000_000},
    }
    cats = [
        {"ticker": "RGNX", "days_until": 30},   # fragile + near -> alert
        {"ticker": "RGNX", "days_until": 200},  # fragile but far -> flag only
        {"ticker": "CRSP", "days_until": 10},   # healthy runway -> no flag
        {"ticker": "CACHED", "days_until": 5},  # mcap from weekly cache, no runway
        {"ticker": None, "days_until": 3},      # unattributed -> nulls
    ]
    signals.attach_fragility(cats, financials)
    assert cats[0]["fragility"] == {"runway_risk": "RED", "market_cap_musd": 800.0,
                                    "fragility_flag": True}
    assert cats[0]["fragile_alert"] == "FRAGILE_CATALYST"
    assert cats[1]["fragility"]["fragility_flag"] is True
    assert cats[1]["fragile_alert"] is None                 # 200d > 90d window
    assert cats[2]["fragility"]["fragility_flag"] is False  # GREEN runway
    assert cats[2]["fragile_alert"] is None
    assert cats[3]["fragility"] == {"runway_risk": None, "market_cap_musd": 123.4,
                                    "fragility_flag": False}
    assert cats[4]["fragility"] == {"runway_risk": None, "market_cap_musd": None,
                                    "fragility_flag": False}

    # ===== Task 4: confluence =================================================
    signals.ALERT_HISTORY_FILE = os.path.join(tmpdir, "alert_history.json")

    # collect_alert_events: every family, universe filter enforced
    event_at = "2026-07-07T14:00:00Z"
    events = signals.collect_alert_events(
        social_alerts=[{
            "ticker": "TSLA", "tag": "SOCIAL_BURST", "detail": "3.5x",
            "observed_at": event_at,
        }],
        baseline_alerts=[
            {"ticker": "TSLA", "metric": "put_call_vol_ratio", "value": 2.1,
             "z_score": 2.4, "signal": "BEARISH_VS_BASELINE",
             "as_of": event_at},
            {"ticker": "ROKU", "metric": "volume_ratio", "value": 3.0,
             "z_score": -2.2, "signal": "LOW_PARTICIPATION",
             "as_of": event_at},
            {"ticker": "SHOP", "metric": "volume_ratio", "value": 3.0,
             "z_score": 2.2, "signal": "HIGH_VOLUME_SURPRISE",
             "as_of": event_at},
        ],
        options_flow={"COIN": {"option_contract_volume_oi_anomaly": [
                          {"strike": 300, "as_of": event_at},
                          {"strike": 310, "as_of": event_at},
                      ], "observed_at": event_at},
                      "PLTR": {"option_contract_volume_oi_anomaly": []}},
        insider_clusters=[
            {"ticker": "RGNX", "insider_count": 3, "buyers": 3, "sellers": 0,
             "cluster_direction": "buy", "period_days": 30, "alert_level": "HIGH",
             "as_of": event_at},
            # sell clusters are context, never confluence
            {"ticker": "NOC", "insider_count": 4, "buyers": 0, "sellers": 4,
             "cluster_direction": "sell", "period_days": 30, "alert_level": "LOW",
             "as_of": event_at},
        ],
        technicals={
            "TSLA": {"rsi_divergence": "BULLISH", "as_of": event_at},
            "ROKU": {},
        },
        fda_catalysts=[
            {"ticker": "CRSP", "event_type": "ADCOM", "event_date": "2026-08-01",
             "alert": "FDA_CATALYST_NEAR", "fragile_alert": None,
             "as_of": event_at},
            # SRPT resolves via sponsor map but is NOT in the universe -> dropped
            {"ticker": "SRPT", "event_type": "CRL", "event_date": None,
             "alert": "FDA_CATALYST_NEAR", "fragile_alert": None,
             "as_of": event_at},
        ],
        clinical_catalysts=[{"ticker": "RGNX", "nct_id": "NCT9", "days_until": 30,
                             "fragile_alert": "FRAGILE_CATALYST",
                             "as_of": event_at}],
        registry_changes=[
            {"ticker": "NTLA", "nct_id": "NCT8", "change_type": "STATUS_FLIP",
             "severity": "high", "old": "RECRUITING", "new": "TERMINATED",
             "as_of": event_at},
            {"ticker": "NTLA", "nct_id": "NCT7", "change_type": "DATE_SLIP",
             "severity": "normal", "old": "2027-01", "new": "2027-03",
             "as_of": event_at},
        ],
    )
    by_fam = {}
    for e in events:
        by_fam.setdefault(e["ticker"], set()).add(e["family"])
    assert by_fam["TSLA"] == {"social", "options", "technical"}
    assert by_fam["ROKU"] == {"technical_low_participation"}
    assert by_fam["SHOP"] == {"technical_high_volume"}
    assert by_fam["COIN"] == {"options"}          # many contracts collapse to one event
    coin_event = next(e for e in events if e["ticker"] == "COIN")
    assert coin_event["detail"] == "contract volume/OI anomaly snapshot present"
    assert by_fam["RGNX"] == {"insider", "catalyst"}
    assert by_fam["CRSP"] == {"catalyst"}
    assert by_fam["NTLA"] == {"catalyst"}         # high-severity flip only
    assert "SRPT" not in by_fam                   # non-universe ticker dropped
    assert "PLTR" not in by_fam
    assert "NOC" not in by_fam                    # sell cluster = context, no event

    # build_confluence: >=2 families gate + exact 48h + event idempotency
    def ev(ticker, family, tag, stamp):
        return {
            "ticker": ticker,
            "family": family,
            "tag": tag,
            "detail": "d",
            "event_at": stamp,
        }

    # A flat pre-version state is preserved but cannot leak into v2.6.0.
    with open(signals.ALERT_HISTORY_FILE, "w", encoding="utf-8") as handle:
        json.dump({"tickers": {"TSLA": [{
            "date": "2026-07-06", "family": "options",
            "tag": "GAMMA_SWEEP", "detail": "23 gamma sweep(s) detected",
        }]}}, handle)
    recs = signals.build_confluence(
        [ev("TSLA", "social", "SOCIAL_BURST", "2026-07-06T12:00:00Z")],
        "2026-07-06T12:00:00Z",
        pipeline_version="2.6.0",
    )
    assert recs == []
    with open(signals.ALERT_HISTORY_FILE, encoding="utf-8") as handle:
        migrated = json.load(handle)
    assert migrated["versions"]["legacy-unversioned"]["tickers"]["TSLA"][0]["tag"] == "GAMMA_SWEEP"
    assert migrated["versions"]["2.6.0"]["tickers"]["TSLA"][0]["tag"] == "SOCIAL_BURST"

    # Day 2: options joins social from yesterday -> confluence fires
    recs = signals.build_confluence(
        [ev("TSLA", "options", "OPTION_CONTRACT_VOLUME_OI_ANOMALY",
            "2026-07-07T12:00:00Z")],
        "2026-07-07T12:00:00Z",
        pipeline_version="2.6.0",
    )
    assert len(recs) == 1
    rec = recs[0]
    assert rec["ticker"] == "TSLA" and rec["confluence_score"] == 2
    assert rec["families"] == ["options", "social"]
    assert rec["first_seen"] == "2026-07-06T12:00:00Z"
    assert rec["as_of"] == "2026-07-07T12:00:00Z"
    assert {a["tag"] for a in rec["alerts"]} == {"SOCIAL_BURST", "OPTION_CONTRACT_VOLUME_OI_ANOMALY"}

    # Same-event rerun replaces (no duplicate alerts)
    recs = signals.build_confluence(
        [ev("TSLA", "options", "OPTION_CONTRACT_VOLUME_OI_ANOMALY",
            "2026-07-07T12:00:00Z")],
        "2026-07-07T12:00:00Z",
        pipeline_version="2.6.0",
    )
    assert len(recs[0]["alerts"]) == 2

    # One second past 48h: social ages out -> back to one family.
    recs = signals.build_confluence(
        [],
        "2026-07-08T12:00:01Z",
        pipeline_version="2.6.0",
    )
    assert recs == [], recs
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("Sniper signals (burst + entrances + FDA miner + registry diff + fragility + confluence) checks passed")
