"""Lookahead-free forward-return maturation with explicit benchmark revisions.

Raw maturity and benchmark maturity are separate. A signal outcome fills once
its own and the SPY window are coherent; the frozen archive-cohort benchmark
may still be partial or unavailable. Every published state is an append-only
row in ``outcome_revisions``; ``outcomes`` is the latest-revision projection.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone

from .config import BENCHMARK, BENCHMARK_RETRY_DAYS, DB_PATH, UNIVERSE_TICKERS
from .db import connect
from .prices import ensure_window, fetch_yfinance, get_open_close, get_window_metadata

logger = logging.getLogger("SignalLedger.Outcomes")

_RETRYABLE_BENCHMARK = ("partial", "unavailable")


def nyse_schedule(start, end):
    import pandas_market_calendars as mcal

    frame = mcal.get_calendar("NYSE").schedule(
        start_date=start.isoformat(), end_date=end.isoformat()
    )
    rows = []
    for session, row in frame.iterrows():
        opened = row["market_open"].to_pydatetime().astimezone(timezone.utc)
        closed = row["market_close"].to_pydatetime().astimezone(timezone.utc)
        rows.append((session.date().isoformat(), opened, closed))
    return rows


def _parse_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_z(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ret(pair):
    if not pair or not pair[0]:
        return None
    return pair[1] / pair[0] - 1.0


def _acquisition_id(conn, ticker, entry_session, exit_session):
    metadata = get_window_metadata(conn, ticker, entry_session, exit_session)
    return metadata["acquisition_id"] if metadata else None


def _window(sessions, generated, horizon, now):
    """Entry is the first session opening at/after the completed brief."""
    entry_index = next(
        (idx for idx, (_, opened, _) in enumerate(sessions) if opened >= generated),
        None,
    )
    if entry_index is None or entry_index + horizon >= len(sessions):
        return None
    exit_session, _, exit_close_time = sessions[entry_index + horizon]
    if exit_close_time > now:
        return None
    return sessions[entry_index][0], exit_session


def _benchmark(conn, cohort, entry_session, exit_session, provider):
    """Frozen-cohort benchmark; never renormalizes a partial basket."""
    if cohort is None:
        return {"status": "refused", "reason": "no_archive_cohort",
                "cohort_sha256": None, "weight_method": None,
                "expected_n": 0, "covered_n": 0, "covered_weight": 0.0,
                "missing": [], "members": {}, "basis_sha256": None,
                "partial_univ_ret": None, "univ_ret": None}
    base = {"cohort_sha256": cohort["cohort_sha256"],
            "weight_method": cohort["weight_method"]}
    if cohort["status"] != "valid":
        reason = ("legacy_unfrozen_cohort" if cohort["status"] == "legacy_unfrozen"
                  else cohort["reason"] or "refused_cohort")
        return {**base, "status": "refused", "reason": reason,
                "expected_n": 0, "covered_n": 0, "covered_weight": 0.0,
                "missing": [], "members": {}, "basis_sha256": None,
                "partial_univ_ret": None, "univ_ret": None}

    weights = json.loads(cohort["weights_json"])
    entry_date = date.fromisoformat(entry_session)
    exit_date = date.fromisoformat(exit_session)
    members, missing = {}, []
    contribution = covered_weight = 0.0
    for ticker in json.loads(cohort["members_json"]):
        state = ensure_window(conn, ticker, entry_date, exit_date, provider)
        value = None
        if state == "ok":
            value = _ret(get_open_close(conn, ticker, entry_session, exit_session))
        if value is None:
            members[ticker] = {"status": state if state != "ok" else "incomplete"}
            missing.append(ticker)
            continue
        weight = float(weights[ticker])
        members[ticker] = {
            "status": "ok", "ret": value, "weight": weight,
            "acquisition_id": _acquisition_id(conn, ticker, entry_session, exit_session),
        }
        contribution += weight * value
        covered_weight += weight

    covered_n = len(members) - len(missing)
    if not missing:
        status, reason = "complete", None
    elif covered_n:
        status, reason = "partial", "missing_members"
    else:
        status, reason = "unavailable", "no_member_prices"
    basis = {ticker: row["acquisition_id"] for ticker, row in members.items()
             if row["status"] == "ok"}
    return {
        **base, "status": status, "reason": reason,
        "expected_n": len(members), "covered_n": covered_n,
        "covered_weight": covered_weight, "missing": missing, "members": members,
        "basis_sha256": (_digest(_canonical({"cohort": cohort["cohort_sha256"],
                                             "acquisitions": basis}))
                         if basis else None),
        # Covered-weight contribution only; deliberately not rescaled to 1.0.
        "partial_univ_ret": contribution if status == "partial" else None,
        "univ_ret": contribution if status == "complete" else None,
    }


def _latest_revision(conn, record_id, horizon):
    return conn.execute(
        """SELECT * FROM outcome_revisions WHERE record_id=? AND horizon=?
           ORDER BY revision_number DESC LIMIT 1""",
        (record_id, horizon),
    ).fetchone()


def _revision_reason(latest, state):
    if latest is None:
        return "initial_maturity"
    if latest["basis_run_id"] != state["basis_run_id"]:
        return "sighting_basis_change"
    if state["benchmark_status"] == "complete":
        return "benchmark_recovery"
    return "benchmark_state_change"


def _is_improvement(latest, state):
    """Same-basis retries publish only strictly larger benchmark coverage.

    A transient failure on a previously covered member must not replace a
    published revision with a narrower one.
    """
    if latest is None or latest["basis_run_id"] != state["basis_run_id"]:
        return True
    before = {ticker for ticker, row in json.loads(latest["member_results_json"]).items()
              if row.get("status") == "ok"}
    after = {ticker for ticker, row in json.loads(state["member_results_json"]).items()
             if row.get("status") == "ok"}
    return before < after


def _append_revision(conn, record_id, horizon, latest, state, now):
    state_sha256 = _digest(_canonical(state))
    if latest is not None and latest["state_sha256"] == state_sha256:
        return False
    if not _is_improvement(latest, state):
        return False
    number = 1 if latest is None else latest["revision_number"] + 1
    revision_id = _digest(f"{record_id}|{horizon}|{number}|{state_sha256}")[:32]
    columns = {
        "revision_id": revision_id,
        "record_id": record_id,
        "horizon": horizon,
        "revision_number": number,
        "parent_revision_id": None if latest is None else latest["revision_id"],
        "revised_at": _utc_z(now),
        "revision_reason": _revision_reason(latest, state),
        "raw_status": "complete",
        "state_sha256": state_sha256,
        **state,
    }
    names = ",".join(columns)
    marks = ",".join("?" for _ in columns)
    conn.execute(f"INSERT INTO outcome_revisions({names}) VALUES({marks})",
                 tuple(columns.values()))
    conn.execute(
        """UPDATE outcomes SET entry_session=?,exit_session=?,entry_open=?,
           exit_close=?,ret=?,spy_ret=?,excess=?,univ_ret=?,status='filled'
           WHERE record_id=? AND horizon=?""",
        (state["entry_session"], state["exit_session"], state["entry_open"],
         state["exit_close"], state["ret"], state["spy_ret"], state["excess"],
         state["univ_ret"], record_id, horizon),
    )
    return True


def _needs_work(row, latest, now):
    if row["status"] == "pending":
        return True
    if latest is None:
        # Filled before the revision contract existed: preserved as published.
        # A ledger rebuild from archives produces its full revision history.
        return False
    if latest["basis_run_id"] != row["basis_run_id"]:
        return True
    if latest["benchmark_status"] not in _RETRYABLE_BENCHMARK:
        return False
    # Bounded retry: after BENCHMARK_RETRY_DAYS the last revision stands
    # (stats already exclude partial/unavailable benchmarks).
    exit_date = date.fromisoformat(latest["exit_session"])
    return (now.date() - exit_date).days <= BENCHMARK_RETRY_DAYS


def mature_outcomes(db_path=DB_PATH, now=None, provider=fetch_yfinance,
                    schedule_provider=nyse_schedule, universe=UNIVERSE_TICKERS):
    """Fill raw outcomes and publish benchmark revisions.

    ``universe`` is retained for caller compatibility only. The benchmark is
    always the frozen cohort archived with the signal's basis run; runtime
    configuration can never rewrite a historical benchmark.
    """
    del universe
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    conn = connect(db_path)
    filled = unpriceable = 0
    try:
        candidates = conn.execute(
            """SELECT o.record_id,o.horizon,o.status,s.ticker,s.asset_class,
                      s.first_seen_run AS basis_run_id,r.generated_at
               FROM outcomes o JOIN signals s ON s.record_id=o.record_id
               JOIN runs r ON r.run_id=s.first_seen_run
               WHERE o.status IN ('pending','filled')
               ORDER BY r.generated_at,o.record_id,o.horizon"""
        ).fetchall()
        schedule_cache = {}
        for row in candidates:
            latest = _latest_revision(conn, row["record_id"], row["horizon"])
            if not _needs_work(row, latest, now):
                continue
            generated = _parse_utc(row["generated_at"])
            cache_key = generated.date().isoformat()
            if cache_key not in schedule_cache:
                schedule_cache[cache_key] = schedule_provider(
                    generated.date() - timedelta(days=3),
                    generated.date() + timedelta(days=60),
                )
            window = _window(schedule_cache[cache_key], generated, row["horizon"], now)
            if window is None:
                continue
            entry_session, exit_session = window
            entry_date = date.fromisoformat(entry_session)
            exit_date = date.fromisoformat(exit_session)
            ticker_state = ensure_window(conn, row["ticker"], entry_date, exit_date, provider)
            benchmark_state = ensure_window(conn, BENCHMARK, entry_date, exit_date, provider)
            if "error" in (ticker_state, benchmark_state):
                continue
            ticker_pair = get_open_close(conn, row["ticker"], entry_session, exit_session)
            spy_pair = get_open_close(conn, BENCHMARK, entry_session, exit_session)
            if ticker_pair is None or spy_pair is None:
                # An empty or incomplete provider response does not establish
                # that an adjusted endpoint can never become available. Keep
                # the row pending so a later bounded run can retry it. The
                # current provider contract has no explicit terminal-absence
                # result; only such a result could justify ``unpriceable``.
                continue

            cohort = conn.execute(
                "SELECT * FROM run_cohorts WHERE run_id=?", (row["basis_run_id"],)
            ).fetchone()
            benchmark = _benchmark(conn, cohort, entry_session, exit_session, provider)
            signal_ret = _ret(ticker_pair)
            spy_ret = _ret(spy_pair)
            excess = None
            if row["asset_class"] not in ("future", "index"):
                excess = signal_ret - spy_ret
            state = {
                "basis_run_id": row["basis_run_id"],
                "benchmark_status": benchmark["status"],
                "benchmark_reason": benchmark["reason"],
                "entry_session": entry_session,
                "exit_session": exit_session,
                "entry_open": ticker_pair[0],
                "exit_close": ticker_pair[1],
                "ret": signal_ret,
                "signal_acquisition_id": _acquisition_id(
                    conn, row["ticker"], entry_session, exit_session),
                "spy_ret": spy_ret,
                "spy_acquisition_id": _acquisition_id(
                    conn, BENCHMARK, entry_session, exit_session),
                "excess": excess,
                "cohort_sha256": benchmark["cohort_sha256"],
                "weight_method": benchmark["weight_method"],
                "expected_n": benchmark["expected_n"],
                "covered_n": benchmark["covered_n"],
                "covered_weight": benchmark["covered_weight"],
                "missing_members_json": _canonical(benchmark["missing"]),
                "member_results_json": _canonical(benchmark["members"]),
                "benchmark_basis_sha256": benchmark["basis_sha256"],
                "partial_univ_ret": benchmark["partial_univ_ret"],
                "univ_ret": benchmark["univ_ret"],
            }
            published = _append_revision(
                conn, row["record_id"], row["horizon"], latest, state, now
            )
            if published and row["status"] == "pending":
                filled += 1
        conn.commit()
    finally:
        conn.close()
    return {"filled": filled, "unpriceable": unpriceable}
