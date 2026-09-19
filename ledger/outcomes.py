"""Lookahead-free forward-return maturation."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from .config import BENCHMARK, DB_PATH, UNIVERSE_TICKERS
from .db import connect
from .prices import ensure_window, fetch_yfinance, get_open_close

logger = logging.getLogger("SignalLedger.Outcomes")


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


def _ret(pair):
    if not pair or not pair[0]:
        return None
    return pair[1] / pair[0] - 1.0


def mature_outcomes(db_path=DB_PATH, now=None, provider=fetch_yfinance,
                    schedule_provider=nyse_schedule, universe=UNIVERSE_TICKERS):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    conn = connect(db_path)
    filled = unpriceable = 0
    try:
        pending = conn.execute(
            """SELECT o.record_id,o.horizon,s.ticker,s.asset_class,r.generated_at
               FROM outcomes o JOIN signals s ON s.record_id=o.record_id
               JOIN runs r ON r.run_id=s.first_seen_run
               WHERE o.status='pending'
               ORDER BY r.generated_at,o.record_id,o.horizon"""
        ).fetchall()
        schedule_cache = {}
        for row in pending:
            generated = _parse_utc(row["generated_at"])
            cache_key = generated.date().isoformat()
            if cache_key not in schedule_cache:
                schedule_cache[cache_key] = schedule_provider(
                    generated.date() - timedelta(days=3),
                    generated.date() + timedelta(days=60),
                )
            sessions = schedule_cache[cache_key]
            entry_index = next(
                (idx for idx, (_, opened, _) in enumerate(sessions) if opened >= generated),
                None,
            )
            if entry_index is None or entry_index + row["horizon"] >= len(sessions):
                continue
            entry_session = sessions[entry_index][0]
            exit_session, _, exit_close_time = sessions[entry_index + row["horizon"]]
            if exit_close_time > now:
                continue

            entry_date = date.fromisoformat(entry_session)
            exit_date = date.fromisoformat(exit_session)
            ticker_state = ensure_window(conn, row["ticker"], entry_date, exit_date, provider)
            benchmark_state = ensure_window(conn, BENCHMARK, entry_date, exit_date, provider)
            if "error" in (ticker_state, benchmark_state):
                continue
            ticker_pair = get_open_close(conn, row["ticker"], entry_session, exit_session)
            benchmark_pair = get_open_close(conn, BENCHMARK, entry_session, exit_session)
            if ticker_pair is None:
                # An empty or incomplete provider response does not establish
                # that an adjusted endpoint can never become available. Keep
                # the row pending so a later bounded run can retry it. The
                # current provider contract has no explicit terminal-absence
                # result; only such a result could justify ``unpriceable``.
                continue
            if benchmark_pair is None:
                continue

            universe_returns = []
            for ticker in universe:
                state = ensure_window(conn, ticker, entry_date, exit_date, provider)
                if state == "error":
                    continue
                pair = get_open_close(conn, ticker, entry_session, exit_session)
                value = _ret(pair)
                if value is not None:
                    universe_returns.append(value)
            # A partial but non-empty basket is honest; transient failures stay
            # absent rather than turning the signal itself unpriceable.
            univ_ret = (sum(universe_returns) / len(universe_returns)
                        if universe_returns else None)
            signal_ret = _ret(ticker_pair)
            spy_ret = _ret(benchmark_pair)
            excess = None
            if row["asset_class"] not in ("future", "index"):
                excess = signal_ret - spy_ret
            conn.execute(
                """UPDATE outcomes SET entry_session=?,exit_session=?,entry_open=?,
                   exit_close=?,ret=?,spy_ret=?,excess=?,univ_ret=?,status='filled'
                   WHERE record_id=? AND horizon=?""",
                (entry_session, exit_session, ticker_pair[0], ticker_pair[1],
                 signal_ret, spy_ret, excess, univ_ret,
                 row["record_id"], row["horizon"]),
            )
            filled += 1
        conn.commit()
    finally:
        conn.close()
    return {"filled": filled, "unpriceable": unpriceable}
