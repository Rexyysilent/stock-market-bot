"""Run a fixed historical monthly-SMA reference, never the private bot's strategy.

This explicit command downloads one US ETF's adjusted daily history. Raw data
and reports stay in the selected private directory. It preserves the independent
month-end calendar, full warmup, actual declaration time, rule hash and all cost
scenarios. Results are retrospective, before personal taxes/FX, with zero cash
yield. No parameter selection, brokerage account or trade is involved.
"""
from __future__ import annotations

import argparse
import calendar
from copy import deepcopy
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio_lab.data import (CALENDARS, MAX_SESSIONS, digest, load_json, session_date, utc,
                                validate_dataset, validate_study)
from portfolio_lab.demo import demo_inputs
from portfolio_lab.engine import simulate
from portfolio_lab.reference import monthly_sma_decisions, reference_rule
from portfolio_lab.report import save_run
from stateutil import atomic_write_json


def _calendar_contract(start, end, as_of):
    """Independently bind a bounded, completed acquisition window to NYSE."""
    if (end - start).days > MAX_SESSIONS * 2:
        raise ValueError("Requested calendar span exceeds the bounded acquisition limit")
    try:
        zone = ZoneInfo(CALENDARS["XNYS"])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Exchange timezone data is missing; use the repository's locked Python environment") from exc
    if end > as_of.astimezone(zone).date():
        raise ValueError("Future end dates are not accepted; request a completed date explicitly")
    import pandas_market_calendars as mcal
    # Extend to the true end of the last month. A partial quote month must not
    # invent a completed monthly observation from its last available bar.
    month_start = date(start.year, start.month, 1)
    month_end = date(end.year, end.month, calendar.monthrange(end.year, end.month)[1])
    try:
        schedule = mcal.get_calendar("NYSE").schedule(start_date=month_start, end_date=month_end)
    except Exception as exc:
        raise ValueError(f"Independent exchange calendar failed ({type(exc).__name__})") from exc
    expected, month_ends = [], {}
    for stamp, row in schedule.iterrows():
        day = stamp.date().isoformat()
        month_ends[day[:7]] = day
        if start.isoformat() <= day <= end.isoformat():
            opening, closing = utc(row["market_open"].isoformat()), utc(row["market_close"].isoformat())
            if closing >= as_of:
                raise ValueError(f"Session {day} is incomplete; request a completed end date explicitly")
            expected.append({"session": day, "open_at": opening.isoformat(), "close_at": closing.isoformat()})
    if not 1 <= len(expected) <= MAX_SESSIONS:
        raise ValueError("The requested calendar interval must contain 1–30000 completed exchange sessions")
    metadata = {"calendar": "XNYS", "library": "pandas_market_calendars",
                "library_version": str(getattr(mcal, "__version__", "unreported")),
                "history_start": start.isoformat(), "end": end.isoformat(),
                "sessions": len(expected), "schedule_sha256": digest(expected),
                "check": "Exact session labels and open/close instants; no provider truth certification."}
    return expected, list(month_ends.values()), metadata


def _check_acquisition(study, dataset, expected):
    validate_dataset(dataset, study)
    if dataset["expected_sessions"] != [row["session"] for row in expected]:
        raise ValueError("Acquisition must match the exact independent calendar for history-start through end")
    for observed, scheduled in zip(dataset["sessions"], expected):
        if any(utc(observed[key]) != utc(scheduled[key]) for key in ("open_at", "close_at")):
            raise ValueError("Acquisition open/close instants disagree with the independent exchange calendar")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True, help="An explicitly chosen USD US-session research ETF")
    parser.add_argument("--history-start", required=True, help="Includes at least ten month-end warmup observations")
    parser.add_argument("--evaluation-start", required=True)
    parser.add_argument("--end", required=True, help="Last completed date, inclusive")
    parser.add_argument("--holdout-start", required=True, help="Diagnostic partition; current declaration time is retained")
    parser.add_argument("--initial-cash", type=float, required=True)
    parser.add_argument("--annual-fixed-cost", type=float, default=0.0)
    parser.add_argument("--cost-bps", type=float, default=5.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--fixed-per-order", type=float, default=1.0)
    parser.add_argument("--dataset", type=Path, help="Replay a prior acquisition instead of downloading")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from portfolio_lab.__main__ import _private_output
        output = _private_output(args.out)
        if output.parent == ROOT:
            raise ValueError("Choose a study subdirectory inside an ignored workspace; its private cache and staging share that parent")
        start, eval_start, end = map(session_date, (args.history_start, args.evaluation_start, args.end))
        if not start <= eval_start <= end:
            raise ValueError("Require history-start <= evaluation-start <= end")
        started_at = datetime.now(timezone.utc)
        declared_at = started_at.isoformat()
        rule = reference_rule(symbol=args.symbol, months=10, equity_weight=1.0)
        study = demo_inputs()[0]
        study.update(study_id=f"reference-{args.symbol}-sma10", strategy_sha256=digest(rule),
                     assets={args.symbol: {"kind": "equity_etf"}},
                     universe_declared_at=declared_at, initial_cash=args.initial_cash,
                     benchmark_weights={args.symbol: 1.0}, annual_fixed_cost=args.annual_fixed_cost,
                     slippage_bps=args.slippage_bps, contributions=[],
                     notes="Fixed retrospective reference rule, not the private bot. One parameter setting, no optimization; cash yield zero.")
        study["cost_model"] = {"kind": "generic", "buy_bps": args.cost_bps,
                               "sell_bps": args.cost_bps, "fixed_per_order": args.fixed_per_order}
        study["evaluation"].update(holdout_start=args.holdout_start, declared_at=declared_at,
                                   objective="Measure a fixed published-style trend reference against buy and hold; no alpha certification.")
        validate_study(study)
        expected, month_end_sessions, calendar_validation = _calendar_contract(start, end, started_at)
        requested_range = {"history_start": args.history_start, "evaluation_start": args.evaluation_start,
                           "end": args.end}
        if args.dataset:
            full = load_json(args.dataset)
        else:
            from portfolio_lab.providers import fetch_yahoo
            full = fetch_yahoo(study, args.history_start, args.end,
                               cache_dir=output.parent / "yfinance-cache")
        _check_acquisition(study, full, expected)
        decisions = monthly_sma_decisions(study, full, symbol=args.symbol,
                                          month_end_sessions=month_end_sessions)
        data = deepcopy(full)
        data["sessions"] = [row for row in data["sessions"]
                            if args.evaluation_start <= row["session"] <= args.end]
        data["expected_sessions"] = [row["session"] for row in expected
                                     if args.evaluation_start <= row["session"] <= args.end]
        if not data["sessions"]:
            raise ValueError("No evaluation sessions remain")
        first_open = utc(data["sessions"][0]["open_at"])
        prior = [item for item in decisions if utc(item["decided_at"]) < first_open]
        if not prior:
            raise ValueError("Insufficient completed-month warmup before evaluation-start")
        # Carry the latest known warmup exposure into the first eligible open;
        # older monthly hypotheses are superseded before the study begins.
        decisions = prior[-1:] + [item for item in decisions if utc(item["decided_at"]) >= first_open]
        data["dataset_id"] = "evaluation-" + digest({k: v for k, v in data.items() if k != "dataset_id"})[:32]
        scenarios = []
        # Stage privately until every scenario succeeds. Avoid holding all full
        # daily paths in memory or publishing a normal-looking partial study.
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".reference-study-", dir=output.parent) as temporary:
            staged = Path(temporary)
            atomic_write_json(staged / "full-acquisition.json", full, indent=2)
            atomic_write_json(staged / "rule.json", rule, indent=2)
            atomic_write_json(staged / "month-end-sessions.json", month_end_sessions, indent=2)
            atomic_write_json(staged / "calendar-validation.json", calendar_validation, indent=2)
            for name, cost_multiplier, delay in (("base", 1.0, 0), ("double-costs", 2.0, 0),
                                                 ("one-session-delay", 1.0, 1)):
                report = simulate(study, data, decisions, cost_multiplier=cost_multiplier, delay_sessions=delay)
                save_run(staged / name, study, data, decisions, report,
                         extra={"full_acquisition_sha256": digest(full), "rule_sha256": digest(rule),
                                "month_end_sessions_sha256": digest(month_end_sessions),
                                "calendar_validation_sha256": digest(calendar_validation),
                                "requested_range": requested_range, "parameter_trials": 1, "scenario": name})
                scenarios.append({"name": name, "study_sha256": digest(study), "dataset_sha256": digest(data),
                                  "decisions_sha256": digest(decisions),
                                  "strategy": report["strategy"]["metrics"], "benchmark": report["benchmark"]["metrics"],
                                  "evaluation": report["evaluation"]})
            summary = {"schema_version": "portfolio-lab.reference-study.v1", "synthetic": data["synthetic"],
                       "research_only": True, "not_private_bot_performance": True, "symbol": args.symbol,
                       "history_start": args.history_start, "evaluation_start": data["expected_sessions"][0],
                       "end": data["expected_sessions"][-1], "declared_at": declared_at,
                       "sessions": len(data["sessions"]), "decisions": len(decisions), "parameter_trials": 1,
                       "scenarios": scenarios, "rule": rule, "full_acquisition_sha256": digest(full),
                       "month_end_sessions_sha256": digest(month_end_sessions), "requested_range": requested_range,
                       "calendar_validation_sha256": digest(calendar_validation)}
            atomic_write_json(staged / "summary.json", summary, indent=2)
            _private_output(output)
            staged.rename(output)
        print(json.dumps({"output": str(output), "synthetic": data["synthetic"], "sessions": summary["sessions"],
                          "scenarios": [{"name": item["name"],
                                         "strategy_cagr": item["strategy"]["annualized_return"],
                                         "benchmark_cagr": item["benchmark"]["annualized_return"],
                                         "strategy_drawdown": item["strategy"]["max_drawdown"],
                                         "benchmark_drawdown": item["benchmark"]["max_drawdown"]}
                                        for item in scenarios]}, indent=2, allow_nan=False))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
