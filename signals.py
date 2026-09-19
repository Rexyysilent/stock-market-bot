"""Deterministic event-classification derivations for the daily brief.

Legacy public keys containing ``signal`` or ``alert`` are retained for schema
compatibility. They represent threshold labels and cross-source coincidence,
not recommendations, expected returns, or execution instructions.

Pure functions over already-fetched section data plus small JSON state files
under state/ (same conventions as update_baselines_and_score in the exporter):
missing files seed empty state, corrupt existing state fails closed, same-date
entries are replaced so reruns are idempotent, rolling windows are trimmed, and
metrics stay null until enough
history exists — no fake backfill.
"""
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from statistics import median

from config import (
    PIPELINE_VERSION,
    SIGNAL_ELIGIBLE_TICKERS,
    SIGNAL_ELIGIBLE_TICKER_SET,
    TICKER_ALIASES,
)
from stateutil import atomic_write_json, load_json_state
from timeutil import to_utc_z

logger = logging.getLogger("Signals")

STATE_DIR = "state"
_STATE_VERSION_TOKEN = re.sub(r"[^A-Za-z0-9_.-]+", "_", PIPELINE_VERSION)
SOCIAL_HISTORY_FILE = os.path.join(
    STATE_DIR, f"social_history_{_STATE_VERSION_TOKEN}.json"
)
MCAP_CACHE_FILE = os.path.join(STATE_DIR, "mcap_cache.json")


def is_signal_eligible_ticker(ticker):
    """True only for a normalized member of the instrumented core universe."""
    if not isinstance(ticker, str):
        return False
    return ticker.strip().upper() in SIGNAL_ELIGIBLE_TICKER_SET

# Burst ratio: mentions today vs trailing-20-run median (floor 1).
SOCIAL_HISTORY_WINDOW = 30      # entries kept per ticker
SOCIAL_BURST_BASELINE = 20      # trailing entries the median is taken over
SOCIAL_MIN_HISTORY = 5          # null until this many prior runs exist
SOCIAL_BURST_RATIO = 3.0        # alert gate (spec value — do not tune)
SOCIAL_BURST_MIN_MENTIONS = 10  # kills small-base noise

# Leaderboard entrances: new top-200 name after 5 runs absent, small/mid only.
TOP200_HISTORY_RUNS = 6         # runs of top-200 membership kept (today + 5 prior)
ENTRANCE_ABSENT_RUNS = 5        # prior runs the ticker must be absent from
ENTRANCE_MAX_MCAP_MUSD = 2000   # mega-cap entrances are stale news
MCAP_CACHE_MAX_AGE_DAYS = 7


def _load_state(path):
    return load_json_state(path, {})


def _save_state(path, state):
    atomic_write_json(path, state, indent=1)


def get_mcap_musd(ticker, now=None):
    """Market cap in $M via a weekly cache; None on fetch failure (not cached)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    cache = _load_state(MCAP_CACHE_FILE)
    entry = cache.get(ticker)
    if entry and entry.get("market_cap_musd") is not None:
        try:
            fetched = datetime.fromisoformat(
                str(entry["fetched_at"]).replace("Z", "+00:00")
            )
            if fetched.tzinfo is None:
                fetched = fetched.replace(tzinfo=timezone.utc)
            if (now - fetched).days <= MCAP_CACHE_MAX_AGE_DAYS:
                return entry["market_cap_musd"]
        except (KeyError, TypeError, ValueError):
            pass
    try:
        import yfinance as yf
        from yfinance_util import configure_yfinance_cache
        configure_yfinance_cache(yf)
        raw = yf.Ticker(ticker).info.get("marketCap")
    except Exception as e:
        logger.warning(f"mcap fetch failed for {ticker}: {e}")
        raw = None
    if raw is None:
        return None
    mcap = round(raw / 1e6, 1)
    cache[ticker] = {
        "market_cap_musd": mcap,
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    _save_state(MCAP_CACHE_FILE, cache)
    return mcap


def update_social_signals(social_attention, top200_rows, run_date,
                          mcap_lookup=get_mcap_musd, observed_at=None):
    """Burst ratios + entrances from the pipeline-versioned social state.

    Mutates universe all-stocks rows in social_attention (adds burst_ratio),
    appends today's history (same-date replace, trim), and returns the list of
    social alert records (SOCIAL_BURST / ATTENTION_BIRTH).
    """
    state = _load_state(SOCIAL_HISTORY_FILE)
    tickers_state = state.setdefault("tickers", {})
    alerts = []
    observed_at = to_utc_z(observed_at)

    # --- 1b: burst ratio per universe ticker -------------------------------
    for rec in social_attention:
        if not rec.get("universe_member") or rec.get("filter") != "all-stocks":
            continue
        ticker = str(rec.get("ticker") or "").strip().upper()
        if not is_signal_eligible_ticker(ticker):
            continue
        history = tickers_state.setdefault(ticker, [])
        prior = [h for h in history if h.get("date") != run_date]
        mentions_today = rec.get("mentions") or 0

        prior_mentions = [h.get("mentions") or 0 for h in prior[-SOCIAL_BURST_BASELINE:]]
        if len(prior_mentions) >= SOCIAL_MIN_HISTORY:
            burst = round(mentions_today / max(median(prior_mentions), 1), 2)
        else:
            burst = None  # warm-up: no fake backfill
        rec["burst_ratio"] = burst

        if (burst is not None and burst >= SOCIAL_BURST_RATIO
                and mentions_today >= SOCIAL_BURST_MIN_MENTIONS):
            alerts.append({
                "ticker": ticker,
                "tag": "SOCIAL_BURST",
                "burst_ratio": burst,
                "mentions": mentions_today,
                "observed_at": rec.get("observed_at") or observed_at,
                "detail": (f"mentions {mentions_today} = {burst}x trailing-"
                           f"{SOCIAL_BURST_BASELINE} median"),
            })

        prior.append({"date": run_date,
                      "mentions": mentions_today,
                      "upvotes": rec.get("upvotes") or 0})
        tickers_state[ticker] = prior[-SOCIAL_HISTORY_WINDOW:]

    # --- 1c: top-200 entrances ---------------------------------------------
    eligible_top200_rows = [
        row for row in (top200_rows or [])
        if isinstance(row, dict)
        and is_signal_eligible_ticker(row.get("ticker"))
    ]
    top200_tickers = sorted({
        str(row["ticker"]).strip().upper() for row in eligible_top200_rows
    })
    history_runs = [h for h in state.get("top200_history", [])
                    if h.get("date") != run_date]
    prior_runs = history_runs[-ENTRANCE_ABSENT_RUNS:]
    if len(prior_runs) >= ENTRANCE_ABSENT_RUNS:
        seen_before = set()
        for run in prior_runs:
            seen_before.update(run.get("tickers", []))
        by_ticker = {
            str(row["ticker"]).strip().upper(): row
            for row in eligible_top200_rows
        }
        for ticker in top200_tickers:
            if ticker in seen_before:
                continue
            mcap = mcap_lookup(ticker)
            if mcap is None or mcap >= ENTRANCE_MAX_MCAP_MUSD:
                continue  # unknown or mega-cap: not a qualifying entrance
            row = by_ticker[ticker]
            alerts.append({
                "ticker": ticker,
                "tag": "ATTENTION_BIRTH",
                "rank": row.get("rank"),
                "mentions": row.get("mentions"),
                "market_cap_musd": mcap,
                "observed_at": row.get("observed_at") or observed_at,
                "detail": (f"entered ApeWisdom top-200 at rank {row.get('rank')} "
                           f"after >={ENTRANCE_ABSENT_RUNS} runs absent "
                           f"(mcap ${mcap}M)"),
            })

    history_runs.append({"date": run_date, "tickers": top200_tickers})
    state["top200_history"] = history_runs[-TOP200_HISTORY_RUNS:]
    _save_state(SOCIAL_HISTORY_FILE, state)
    return alerts


# --- Task 2a: FDA catalyst keyword miner ------------------------------------
# ClinicalTrials.gov does not contain catalysts; 8-Ks and PRs do. Keyword +
# regex only over content the pipeline already ingests — no LLM, precision
# over recall (a false catalyst is worse than a missed one).

FDA_CATALYST_NEAR_DAYS = 90
DATE_WINDOW_CHARS = 200

FDA_EVENT_KEYWORDS = [
    ("PDUFA", "PDUFA_DATE"),
    ("action date", "PDUFA_DATE"),
    ("advisory committee", "ADCOM"),
    ("AdCom", "ADCOM"),
    ("complete response letter", "CRL"),
    ("CRL", "CRL"),
    ("NDA acceptance", "ACCEPTANCE"),
    ("BLA acceptance", "ACCEPTANCE"),
    ("filing acceptance", "ACCEPTANCE"),
    ("priority review", "PRIORITY_REVIEW"),
    ("breakthrough therapy", "DESIGNATION"),
    ("fast track designation", "DESIGNATION"),
]
# Word-bounded so "CRL" can't hit inside another token; multiword keywords
# tolerate any whitespace run.
_KEYWORD_RES = [
    (re.compile(r"\b" + re.escape(kw).replace(r"\ ", r"\s+") + r"\b",
                re.IGNORECASE), etype)
    for kw, etype in FDA_EVENT_KEYWORDS
]

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_MONTH_NUM = {name: i + 1 for i, name in enumerate(_MONTHS)}
_MONTHS_RE = "|".join(_MONTHS)
_DATE_RES = [
    # March 15, 2026 / March 15 2026
    re.compile(rf"\b(?P<month>{_MONTHS_RE})\s+(?P<day>\d{{1,2}})"
               rf"(?:st|nd|rd|th)?,?\s+(?P<year>\d{{4}})\b", re.IGNORECASE),
    # 15 March 2026
    re.compile(rf"\b(?P<day>\d{{1,2}})\s+(?P<month>{_MONTHS_RE})"
               rf"\s+(?P<year>\d{{4}})\b", re.IGNORECASE),
    # 2026-03-15
    re.compile(r"\b(?P<year>\d{4})-(?P<mnum>\d{2})-(?P<dnum>\d{2})\b"),
]

# Universe tickers text mining can attribute to (no futures/indices).
_MINEABLE_TICKERS = [
    ticker for ticker in SIGNAL_ELIGIBLE_TICKERS
    if "^" not in ticker and "=" not in ticker
]


def _extract_event_date(text, kw_start, kw_end):
    """Nearest explicit date within +/-DATE_WINDOW_CHARS of the keyword hit."""
    window_start = max(0, kw_start - DATE_WINDOW_CHARS)
    window = text[window_start:kw_end + DATE_WINDOW_CHARS]
    kw_pos = kw_start - window_start
    best = None  # (distance_from_keyword, datetime)
    for pattern in _DATE_RES:
        for m in pattern.finditer(window):
            gd = m.groupdict()
            try:
                if gd.get("mnum"):
                    dt = datetime(int(gd["year"]), int(gd["mnum"]), int(gd["dnum"]))
                else:
                    dt = datetime(int(gd["year"]),
                                  _MONTH_NUM[gd["month"].lower()],
                                  int(gd["day"]))
            except (KeyError, ValueError):
                continue
            dist = abs(m.start() - kw_pos)
            if best is None or dist < best[0]:
                best = (dist, dt)
    return best[1] if best else None


def _match_universe_tickers(text):
    """Universe tickers a text mentions: bare symbol (case-sensitive,
    word-bounded) or company-name alias (case-insensitive, optional plural)."""
    found = []
    for ticker in _MINEABLE_TICKERS:
        if re.search(rf"\b{re.escape(ticker)}\b", text):
            found.append(ticker)
            continue
        for alias in TICKER_ALIASES.get(ticker, ()):
            alias_re = (r"\b" + re.escape(alias).replace(r"\ ", r"\s+")
                        + r"(?:e?s)?\b")
            if re.search(alias_re, text, re.IGNORECASE):
                found.append(ticker)
                break
    return found


def mine_fda_catalysts(sec_filings, biotech_news, ceo_signals, run_date,
                       adcom_meetings=None):
    """Keyword-mine FDA regulatory events from already-fetched sections.

    Returns fda_catalysts records deduped on (ticker, event_type, event_date);
    event_date stays null when no explicit date sits near the keyword (the
    mention is still a signal). alert = FDA_CATALYST_NEAR when the event is
    0..90 days out.
    """
    run_dt = datetime.strptime(run_date, "%Y-%m-%d")

    # (scan_text, tickers, headline, link, source, as_of)
    items = []
    for f in sec_filings or []:
        # Metadata only (company name + primaryDocDescription) — low yield by
        # design; EDGAR full-text is a separate vendor and out of scope.
        text = " ".join(str(x) for x in (f.get("title"), f.get("description")) if x)
        if f.get("ticker") and text:
            items.append((text, [f["ticker"]], text, f.get("link"),
                          "sec_filing", f.get("filed_at") or f.get("date")))
    for n in biotech_news or []:
        title = n.get("title") or ""
        tickers = _match_universe_tickers(title)
        if tickers:
            items.append((title, tickers, title, n.get("link"),
                          "biotech_news", n.get("as_of") or n.get("published")))
    for s in ceo_signals or []:
        title = s.get("title") or ""
        if s.get("ticker") and s["ticker"] != "SECTOR" and title:
            items.append((title, [s["ticker"]], title, s.get("link"),
                          "ceo_ca", s.get("date")))

    records = {}

    def _add(
        ticker, event_type, event_dt, headline, link, source, as_of,
        observed_at=None,
    ):
        if not is_signal_eligible_ticker(ticker):
            return
        event_date = event_dt.strftime("%Y-%m-%d") if event_dt else None
        key = (ticker, event_type, event_date)
        if key in records:
            return
        days_until = (event_dt - run_dt).days if event_dt else None
        records[key] = {
            "ticker": ticker,
            "event_type": event_type,
            "event_date": event_date,
            "days_until": days_until,
            "headline": headline[:250],
            "link": link,
            "source": source,
            "as_of": as_of,
            "observed_at": observed_at,
            "alert": ("FDA_CATALYST_NEAR"
                      if days_until is not None
                      and 0 <= days_until <= FDA_CATALYST_NEAR_DAYS
                      else None),
        }

    for text, tickers, headline, link, source, as_of in items:
        for pattern, event_type in _KEYWORD_RES:
            m = pattern.search(text)
            if not m:
                continue
            event_dt = _extract_event_date(text, m.start(), m.end())
            for ticker in tickers:
                _add(ticker, event_type, event_dt, headline, link, source, as_of)

    # Task 2b: FDA AdCom calendar rows (already parsed/cached by the research
    # agent). Prefer its explicit sponsor-to-ticker resolution; fall back to
    # universe entity matching over the enriched agenda.
    for meeting in adcom_meetings or []:
        title = meeting.get("title") or ""
        agenda = meeting.get("agenda") or ""
        explicit_ticker = meeting.get("ticker")
        tickers = (
            [explicit_ticker]
            if explicit_ticker
            else _match_universe_tickers(
                " ".join(
                    str(value)
                    for value in (
                        title,
                        agenda,
                        meeting.get("sponsor"),
                        meeting.get("product"),
                    )
                    if value
                )
            )
        )
        if not tickers:
            continue
        try:
            meeting_dt = datetime.strptime(meeting.get("date") or "", "%Y-%m-%d")
        except ValueError:
            meeting_dt = None
        for ticker in tickers:
            _add(ticker, "ADCOM", meeting_dt, agenda or title, meeting.get("link"),
                 "fda_calendar",
                 meeting.get("as_of") or meeting.get("observed_at"),
                 observed_at=meeting.get("observed_at"))

    # Dated upcoming events first (soonest first), then undated/past mentions.
    return sorted(
        records.values(),
        key=lambda r: (0 if r["days_until"] is not None and r["days_until"] >= 0 else 1,
                       r["days_until"] if r["days_until"] is not None else 0,
                       r["ticker"]),
    )


# --- Task 3a: CT.gov registry diff engine -----------------------------------
# The registry's value is day-over-day deviation, not the trial list itself.
# Diffs run against the UNFILTERED merge (a flip to TERMINATED must be seen
# even though the filtered clinical_catalysts section drops that trial).

CTGOV_SNAPSHOT_FILE = os.path.join(
    STATE_DIR, f"ctgov_snapshot_{_STATE_VERSION_TOKEN}.json"
)
DATE_SLIP_MIN_DAYS = 14          # primary completion moved more than this
ENROLLMENT_CHANGE_MIN_PCT = 10.0
REGISTRY_HIGH_SEVERITY = {"TERMINATED", "SUSPENDED", "WITHDRAWN"}


def _parse_ctgov_date(text):
    """CT.gov dates come as YYYY-MM-DD, YYYY-MM, or YYYY."""
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, fmt)
        except (TypeError, ValueError):
            continue
    return None


def diff_registry(registry_view, as_of):
    """Day-over-day CT.gov diffs vs the pipeline-versioned CT.gov snapshot;
    legacy unversioned state remains untouched for rollback.

    registry_view: unfiltered merged trial records (nct_id, ticker, title,
    status, primary_completion_date, enrollment). First run seeds the snapshot
    and emits [] (no fake diffs against nothing). Empty on quiet days is
    expected. as_of = run time: the observation time IS the event time for a
    diff. Trials absent from today's fetch window keep their last-seen state.
    Changes already emitted on the same UTC date are replayed so an intraday
    rerun cannot erase a real day-over-day change from the live brief.
    """
    state = _load_state(CTGOV_SNAPSHOT_FILE)
    prior = state.get("trials", {})
    first_run = not prior
    emission_date = str(as_of)[:10]
    retained = (
        state.get("emitted_changes", [])
        if state.get("emission_date") == emission_date
        else []
    )
    changes = list(retained)
    change_keys = {
        (
            change.get("nct_id"),
            change.get("change_type"),
            str(change.get("old")),
            str(change.get("new")),
        )
        for change in changes
    }
    snapshot = dict(prior)

    for rec in registry_view:
        ticker = rec.get("ticker")
        if ticker is not None and not is_signal_eligible_ticker(ticker):
            continue
        nct_id = rec.get("nct_id")
        if not nct_id or nct_id == "N/A":
            continue
        current = {
            "status": rec.get("status"),
            "primary_completion_date": rec.get("primary_completion_date"),
            "enrollment": rec.get("enrollment"),
        }
        old = prior.get(nct_id)
        snapshot[nct_id] = current
        if old is None:
            continue  # newly tracked: nothing to diff against yet

        common = {"nct_id": nct_id, "ticker": rec.get("ticker"),
                  "title": rec.get("title"), "as_of": as_of}

        if current["status"] and old.get("status") != current["status"]:
            severity = ("high" if current["status"] in REGISTRY_HIGH_SEVERITY
                        else "normal")  # terminations are violent negatives
            change = {**common, "change_type": "STATUS_FLIP",
                      "severity": severity,
                      "old": old.get("status"), "new": current["status"]}
            key = (nct_id, "STATUS_FLIP", str(change["old"]), str(change["new"]))
            if key not in change_keys:
                changes.append(change)
                change_keys.add(key)

        old_dt = _parse_ctgov_date(old.get("primary_completion_date"))
        new_dt = _parse_ctgov_date(current["primary_completion_date"])
        if old_dt and new_dt:
            delta_days = (new_dt - old_dt).days
            if abs(delta_days) > DATE_SLIP_MIN_DAYS:
                # positive delta = slip (mildly negative); negative = pull-in
                change = {**common, "change_type": "DATE_SLIP",
                          "severity": "normal",
                          "old": old.get("primary_completion_date"),
                          "new": current["primary_completion_date"],
                          "delta_days": delta_days}
                key = (nct_id, "DATE_SLIP", str(change["old"]), str(change["new"]))
                if key not in change_keys:
                    changes.append(change)
                    change_keys.add(key)

        old_en, new_en = old.get("enrollment"), current["enrollment"]
        if isinstance(old_en, (int, float)) and isinstance(new_en, (int, float)) and old_en:
            delta_pct = (new_en - old_en) / old_en * 100
            if abs(delta_pct) > ENROLLMENT_CHANGE_MIN_PCT:
                change = {**common, "change_type": "ENROLLMENT_CHANGE",
                          "severity": "normal",
                          "old": old_en, "new": new_en,
                          "delta_pct": round(delta_pct, 1)}
                key = (nct_id, "ENROLLMENT_CHANGE",
                       str(change["old"]), str(change["new"]))
                if key not in change_keys:
                    changes.append(change)
                    change_keys.add(key)

    state["trials"] = snapshot
    state["emission_date"] = emission_date
    state["emitted_changes"] = [] if first_run else changes
    _save_state(CTGOV_SNAPSHOT_FILE, state)
    return [] if first_run else changes


# --- Task 3b: fragility join -------------------------------------------------
# A catalyst on a fragile single-asset name is existential; the same event on
# big pharma is a footnote. This join makes the catalyst sections tradeable.

FRAGILE_MAX_MCAP_MUSD = 2000
FRAGILE_RUNWAY_LEVELS = ("RED", "YELLOW")


def attach_fragility(catalyst_records, financials_map):
    """Attach fragility {runway_risk, market_cap_musd, fragility_flag} to each
    clinical_catalysts / fda_catalysts record (mutates in place).

    market_cap_musd: financials_map (cash-runway check) first, then the weekly
    mcap cache, else null. fragile_alert = FRAGILE_CATALYST when the flag is
    set and the event is 0..90 days out.
    """
    mcap_cache = _load_state(MCAP_CACHE_FILE)
    for rec in catalyst_records:
        ticker = rec.get("ticker")
        runway_risk = None
        mcap = None
        if ticker:
            fin = financials_map.get(ticker) or {}
            runway_risk = fin.get("risk_level")
            raw_mcap = fin.get("market_cap")
            if raw_mcap:
                mcap = round(raw_mcap / 1e6, 1)
            if mcap is None:
                mcap = (mcap_cache.get(ticker) or {}).get("market_cap_musd")
        fragility_flag = bool(
            runway_risk in FRAGILE_RUNWAY_LEVELS
            and mcap is not None and mcap < FRAGILE_MAX_MCAP_MUSD
        )
        rec["fragility"] = {
            "runway_risk": runway_risk,
            "market_cap_musd": mcap,
            "fragility_flag": fragility_flag,
        }
        days = rec.get("days_until")
        rec["fragile_alert"] = (
            "FRAGILE_CATALYST"
            if is_signal_eligible_ticker(ticker)
            and fragility_flag and days is not None
            and 0 <= days <= FDA_CATALYST_NEAR_DAYS
            else None
        )
    return catalyst_records


# --- Task 4: confluence -------------------------------------------------------
# The payoff: a record fires only when >=2 INDEPENDENT source families light
# up the same universe ticker inside 48h. Most days this is 0-2 rows; an
# empty section is a feature, not a bug — thresholds do not get loosened to
# make it populate.

ALERT_HISTORY_FILE = os.path.join(STATE_DIR, "alert_history.json")
CONFLUENCE_WINDOW_HOURS = 48
CONFLUENCE_MIN_FAMILIES = 2

_UNIVERSE_SET = SIGNAL_ELIGIBLE_TICKER_SET


def collect_alert_events(social_alerts, baseline_alerts, options_flow,
                         insider_clusters, technicals, fda_catalysts,
                         clinical_catalysts, registry_changes):
    """Flatten structured alerts into timestamped confluence events.

    Confluence fails closed on time. A record without a source timestamp or
    an honest observation timestamp cannot be promoted as a current event.
    """
    events = []

    def add(ticker, family, tag, detail, event_at=None, observed_at=None):
        normalized_ticker = str(ticker or "").strip().upper()
        stamp = to_utc_z(event_at) or to_utc_z(observed_at)
        if not is_signal_eligible_ticker(normalized_ticker) or not family or not tag or stamp is None:
            return
        identity = "|".join((normalized_ticker, str(family), str(tag), stamp))
        events.append({
            "ticker": normalized_ticker,
            "family": family,
            "tag": tag,
            "detail": detail,
            "event_at": stamp,
            "observed_at": to_utc_z(observed_at),
            "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
        })

    for alert in social_alerts or []:
        add(alert.get("ticker"), "social", alert.get("tag"),
            alert.get("detail"), event_at=alert.get("as_of"),
            observed_at=alert.get("observed_at"))

    for alert in baseline_alerts or []:
        metric = alert.get("metric")
        if metric == "put_call_vol_ratio":
            family = "options"
        elif metric == "volume_ratio":
            family = {
                "HIGH_VOLUME_SURPRISE": "technical_high_volume",
                "LOW_PARTICIPATION": "technical_low_participation",
            }.get(alert.get("signal"))
        else:
            family = None
        if family:
            add(
                alert.get("ticker"), family, alert.get("signal"),
                f"{metric} z={alert.get('z_score')} (value {alert.get('value')})",
                event_at=alert.get("as_of"),
                observed_at=alert.get("observed_at"),
            )

    for ticker, flow in (options_flow or {}).items():
        anomalies = [
            row for row in (flow.get("option_contract_volume_oi_anomaly") or [])
            if row.get("signal_eligible") is not False
        ]
        if anomalies:
            source_times = [to_utc_z(row.get("as_of")) for row in anomalies]
            event_at = max((stamp for stamp in source_times if stamp), default=None)
            add(
                ticker, "options", "OPTION_CONTRACT_VOLUME_OI_ANOMALY",
                "contract volume/OI anomaly snapshot present",
                event_at=event_at or flow.get("as_of"),
                observed_at=flow.get("observed_at"),
            )

    for cluster in insider_clusters or []:
        if cluster.get("signal_eligible") is False:
            continue
        direction = cluster.get("cluster_direction")
        if direction not in ("buy", "mixed"):
            continue
        add(
            cluster.get("ticker"), "insider", "INSIDER_BUY_CLUSTER",
            f"{cluster.get('buyers')} distinct buyers in "
            f"{cluster.get('period_days')}d "
            f"({cluster.get('alert_level')}, {direction})",
            event_at=cluster.get("as_of"),
            observed_at=cluster.get("observed_at"),
        )

    for ticker, technical in (technicals or {}).items():
        divergence = technical.get("rsi_divergence")
        if divergence:
            add(
                ticker, "technical", "RSI_DIVERGENCE", str(divergence),
                event_at=technical.get("as_of"),
                observed_at=technical.get("observed_at"),
            )

    for record in fda_catalysts or []:
        if record.get("alert"):
            add(
                record.get("ticker"), "catalyst", record["alert"],
                f"{record.get('event_type')} "
                f"{record.get('event_date') or '(undated)'}",
                event_at=record.get("as_of"),
                observed_at=record.get("observed_at"),
            )
        if record.get("fragile_alert"):
            add(
                record.get("ticker"), "catalyst", record["fragile_alert"],
                f"{record.get('event_type')} on fragile name",
                event_at=record.get("as_of"),
                observed_at=record.get("observed_at"),
            )

    for record in clinical_catalysts or []:
        if record.get("fragile_alert"):
            add(
                record.get("ticker"), "catalyst", record["fragile_alert"],
                f"{record.get('nct_id')} {record.get('days_until')}d out "
                "on fragile name",
                event_at=record.get("as_of"),
                observed_at=record.get("observed_at"),
            )

    for change in registry_changes or []:
        if change.get("severity") == "high":
            add(
                change.get("ticker"), "catalyst",
                f"REGISTRY_{change.get('change_type')}",
                f"{change.get('nct_id')}: {change.get('old')} -> "
                f"{change.get('new')}",
                event_at=change.get("as_of"),
                observed_at=change.get("observed_at"),
            )

    return events


def build_confluence(events, run_at, pipeline_version=PIPELINE_VERSION):
    """Build exact-window, source-timestamped confluence records.

    Events are de-duplicated by ticker/family/tag/source time. Re-running the
    exporter can no longer refresh an old insider cluster or a persistent
    technical condition merely because the pipeline observed it again.
    """
    raw_state = _load_state(ALERT_HISTORY_FILE)
    if isinstance(raw_state, dict) and isinstance(raw_state.get("versions"), dict):
        state = raw_state
        state["schema_version"] = 3
    else:
        state = {"schema_version": 3, "versions": {}}
        if isinstance(raw_state, dict) and raw_state:
            state["versions"]["legacy-unversioned"] = raw_state
    version_state = state["versions"].setdefault(pipeline_version, {})
    tickers_state = version_state.setdefault("tickers", {})

    run_stamp = to_utc_z(run_at)
    if run_stamp is None:
        raise ValueError("run_at must be a usable timestamp")
    run_dt = datetime.fromisoformat(run_stamp.replace("Z", "+00:00"))
    window = timedelta(hours=CONFLUENCE_WINDOW_HOURS)

    def in_window(entry):
        stamp = to_utc_z(
            entry.get("event_at") or entry.get("as_of") or entry.get("date")
        )
        if stamp is None:
            return False
        try:
            event_dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return False
        age = run_dt - event_dt
        return timedelta(0) <= age <= window

    incoming = {}
    for event in events:
        ticker = str(event.get("ticker") or "").strip().upper()
        if not is_signal_eligible_ticker(ticker):
            continue
        event = dict(event)
        event["ticker"] = ticker
        if not in_window(event):
            continue
        identity = event.get("event_id")
        if not identity:
            basis = "|".join((
                str(event.get("ticker")), str(event.get("family")),
                str(event.get("tag")), str(event.get("event_at")),
            ))
            identity = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]
        incoming.setdefault(event["ticker"], []).append({
            "event_id": identity,
            "event_at": to_utc_z(event.get("event_at")),
            "observed_at": to_utc_z(event.get("observed_at")),
            "family": event["family"],
            "tag": event["tag"],
            "detail": event.get("detail"),
        })

    for ticker in set(tickers_state) | set(incoming):
        if not is_signal_eligible_ticker(ticker):
            tickers_state.pop(ticker, None)
            continue
        current = [entry for entry in tickers_state.get(ticker, [])
                   if in_window(entry)]
        by_id = {
            entry.get("event_id"): entry
            for entry in current
            if entry.get("event_id")
        }
        for event in incoming.get(ticker, []):
            by_id[event["event_id"]] = event
        entries = sorted(
            by_id.values(),
            key=lambda entry: (
                entry.get("event_at") or "",
                entry.get("family") or "",
                entry.get("tag") or "",
            ),
        )
        if entries:
            tickers_state[ticker] = entries
        else:
            tickers_state.pop(ticker, None)
    _save_state(ALERT_HISTORY_FILE, state)

    records = []
    for ticker, entries in tickers_state.items():
        families = sorted({entry["family"] for entry in entries})
        if len(families) < CONFLUENCE_MIN_FAMILIES:
            continue
        records.append({
            "ticker": ticker,
            "families": families,
            "alerts": [
                {
                    "family": entry["family"],
                    "tag": entry["tag"],
                    "detail": entry.get("detail"),
                    "event_at": entry.get("event_at"),
                }
                for entry in entries
            ],
            "confluence_score": len(families),
            "first_seen": min(entry["event_at"] for entry in entries),
            "as_of": max(entry["event_at"] for entry in entries),
        })
    records.sort(key=lambda record: (-record["confluence_score"], record["ticker"]))
    return records
