"""Cash-funded, fractional total-return *proxy* research accounting.

This module does not produce orders for a broker.  Input weights are an
operator's hypothesis, not a strategy selected or recommended by this package.
Adjusted open/close values represent economic return units, not executable
share prices, tax lots, or cash dividend payments.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone
import math
from zoneinfo import ZoneInfo

from .costs import trade_cost
from .data import (
    CALENDARS, canonical, digest, session_date, utc, validate_dataset,
    validate_decisions, validate_study,
)


_YEAR_SECONDS = 365.25 * 86400
_EPSILON = 1e-12


def _finite(value, label, *, positive=False):
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{label} is nonfinite or outside supported accounting range")
    return value


def _nav(cash, units, prices, mark):
    value = math.fsum([cash] + [units[symbol] * prices[symbol][mark] for symbol in units])
    return _finite(value, "Portfolio value", positive=True)


def _fee(study, symbol, side, notional, multiplier):
    components = trade_cost(
        study["cost_model"], study["assets"][symbol]["kind"], side,
        notional, multiplier=multiplier,
    )
    impact = notional * (study["slippage_bps"] / 10000.0) * multiplier
    total = components["total"] + impact
    _finite(total, "Trading costs")
    return components, impact, total


def _charge_overhead(cash, units, prices, mark, amount):
    """Allocate a known external expense across proxy value, without a sale.

    This explicit economic approximation keeps a fully invested account from
    borrowing fictitious cash to pay infrastructure costs.  It is not a claim
    that a real broker liquidates units without fees or taxes.
    """
    if amount == 0:
        return cash
    value = _nav(cash, units, prices, mark)
    _finite(amount, "Accrued fixed cost")
    if amount >= value:
        raise ValueError("Fixed costs deplete portfolio value; simulation cannot borrow to continue")
    factor = 1.0 - amount / value
    for symbol in units:
        units[symbol] *= factor
    return cash * factor


def _skip(skipped, row, symbol, side, notional, reason, decision_id):
    skipped.append({
        "session": row["session"], "symbol": symbol, "side": side,
        "requested_notional": notional, "reason": reason,
        "decision_id": decision_id,
    })


def _trade(study, row, units, cash, symbol, side, notional, multiplier,
           trades, decision_id):
    components, impact, expense = _fee(study, symbol, side, notional, multiplier)
    quantity = _finite(notional / row["prices"][symbol]["open"], "Proxy quantity", positive=True)
    if side == "BUY":
        cash -= notional + expense
        units[symbol] += quantity
    else:
        cash += notional - expense
        units[symbol] -= quantity
    # Only floating-point dust can be clamped.  An economic overdraft fails.
    tolerance = _EPSILON * max(1.0, notional)
    if cash < -tolerance or units[symbol] < -_EPSILON * max(1.0, quantity):
        raise ValueError("Proxy transaction would create borrowing or a short position")
    cash = max(0.0, cash)
    units[symbol] = max(0.0, units[symbol])
    trades.append({
        "session": row["session"], "at": row["open_at"], "symbol": symbol,
        "side": side, "notional": notional, "economic_units": quantity,
        "reference_proxy_open": row["prices"][symbol]["open"],
        "cost_breakdown": components, "slippage_cost": impact,
        "total_cost": expense, "decision_id": decision_id,
        "execution_kind": "fractional_total_return_proxy_not_broker_fill",
    })
    return cash


def _buy_basket(study, row, units, cash, requested, budget, multiplier,
                trades, skipped, decision_id):
    """Scale all candidate buys together so the basket including fees fits.

    Removing uneconomic/minimum-size orders is recorded explicitly.  The
    method does not choose a preferred asset when fees make the whole basket
    unaffordable; such a basket is skipped rather than borrowing.
    """
    active = {}
    minimum = study["execution"]["min_trade_value"]
    for symbol in sorted(requested):
        amount = requested[symbol]
        if amount <= _EPSILON:
            continue
        if amount < minimum:
            _skip(skipped, row, symbol, "BUY", amount, "below_minimum_trade", decision_id)
        elif _fee(study, symbol, "BUY", amount, multiplier)[2] >= amount:
            _skip(skipped, row, symbol, "BUY", amount, "uneconomic_order_cost", decision_id)
        else:
            active[symbol] = amount
    budget = max(0.0, min(cash, budget))
    while active:
        if budget <= _EPSILON:
            for symbol, amount in active.items():
                _skip(skipped, row, symbol, "BUY", amount, "insufficient_cash", decision_id)
            break

        def basket_cost(scale):
            if scale == 0:
                return 0.0
            return math.fsum(
                amount * scale + _fee(study, symbol, "BUY", amount * scale, multiplier)[2]
                for symbol, amount in active.items()
            )

        if basket_cost(1.0) <= budget:
            scale = 1.0
        else:
            low, high = 0.0, 1.0
            for _ in range(64):
                middle = (low + high) / 2.0
                if basket_cost(middle) <= budget:
                    low = middle
                else:
                    high = middle
            scale = low

        removed = []
        for symbol, amount in active.items():
            scaled = amount * scale
            if scaled <= _EPSILON:
                reason = "insufficient_cash_for_basket_fees"
            elif scaled < minimum:
                reason = "below_minimum_after_cost_scaling"
            elif _fee(study, symbol, "BUY", scaled, multiplier)[2] >= scaled:
                reason = "uneconomic_after_cost_scaling"
            else:
                continue
            _skip(skipped, row, symbol, "BUY", amount, reason, decision_id)
            removed.append(symbol)
        if removed:
            for symbol in removed:
                del active[symbol]
            continue
        for symbol, amount in active.items():
            cash = _trade(study, row, units, cash, symbol, "BUY", amount * scale,
                          multiplier, trades, decision_id)
        break
    return cash


def _rebalance(study, row, units, cash, weights, multiplier, trades,
               skipped, decision_id):
    value = _nav(cash, units, row["prices"], "open")
    targets = {symbol: value * weights.get(symbol, 0.0) for symbol in units}
    minimum = study["execution"]["min_trade_value"]
    for symbol in sorted(units):
        current = units[symbol] * row["prices"][symbol]["open"]
        sale = current - targets[symbol]
        if sale <= _EPSILON * max(1.0, value):
            continue
        if sale < minimum:
            _skip(skipped, row, symbol, "SELL", sale, "below_minimum_trade", decision_id)
        elif _fee(study, symbol, "SELL", sale, multiplier)[2] >= sale:
            _skip(skipped, row, symbol, "SELL", sale, "uneconomic_order_cost", decision_id)
        else:
            cash = _trade(study, row, units, cash, symbol, "SELL", sale,
                          multiplier, trades, decision_id)
    requested = {
        symbol: max(0.0, targets[symbol] - units[symbol] * row["prices"][symbol]["open"])
        for symbol in units
    }
    reserve = value * max(0.0, 1.0 - math.fsum(weights.values()))
    return _buy_basket(study, row, units, cash, requested, max(0.0, cash - reserve),
                       multiplier, trades, skipped, decision_id)


def _schedule(study, sessions, decisions, delay_sessions):
    openings = [utc(row["open_at"]) for row in sessions]
    extra_delay = study["execution"]["extra_delay_sessions"] + delay_sessions
    latency = timedelta(seconds=study["execution"]["latency_seconds"])
    scheduled, receipts = {}, []
    for decision in sorted(decisions, key=lambda item: (utc(item["decided_at"]), item["decision_id"])):
        decided = utc(decision["decided_at"])
        known = max(decided, utc(decision["recorded_at"])) if decision["origin"] == "paper" else decided
        eligible = known + latency
        index = bisect_right(openings, eligible) + extra_delay
        receipt = {
            "decision_id": decision["decision_id"], "origin": decision["origin"],
            "decided_at": decision["decided_at"], "eligible_after": eligible.isoformat(),
            "effective_session": None, "status": "pending", "reason": "no_later_session_in_dataset",
            "evidence_count": len(decision["evidence"]),
            "proxy_order_count": 0, "skipped_trade_count": 0,
        }
        if index < len(sessions):
            receipt["effective_session"] = sessions[index]["session"]
            age_days = (openings[index] - decided).total_seconds() / 86400.0
            if age_days > study["execution"]["max_decision_age_days"]:
                receipt.update(status="rejected_stale", reason="decision_age_exceeds_limit")
            else:
                if index in scheduled:
                    raise ValueError("Multiple decisions map to the same effective session; resolve explicitly")
                receipt.update(status="executed_proxy", reason=None)
                scheduled[index] = (decision, receipt)
        receipts.append(receipt)
    return scheduled, receipts


def _metrics(path):
    if not path:
        return {
            "sessions": 0, "initial_value": None, "ending_value": None,
            "contributions": 0.0, "profit": None, "total_return": None,
            "annualized_return": None, "max_drawdown": None, "elapsed_days": 0.0,
            "costs": {"trading": 0.0, "slippage": 0.0, "overhead": 0.0, "total": 0.0},
            "turnover_notional": 0.0, "turnover_to_mean_nav": None, "orders": 0,
        }
    wealth, peak, drawdown = 1.0, 1.0, 0.0
    for row in path:
        opening_wealth = wealth * row["overnight_return_factor"]
        after_trade_wealth = opening_wealth * row["post_trade_return_factor"]
        wealth = opening_wealth * row["intraday_return_factor"]
        for point in (opening_wealth, after_trade_wealth, wealth):
            peak = max(peak, point)
            drawdown = min(drawdown, point / peak - 1.0)
    _finite(wealth, "Flow-adjusted wealth index", positive=True)
    initial, ending = path[0]["previous_close_value"], path[-1]["closing_value"]
    flows = math.fsum(row["contribution"] for row in path)
    seconds = (utc(path[-1]["close_at"]) - utc(path[0]["period_start"])).total_seconds()
    annualized = math.expm1(math.log(wealth) * _YEAR_SECONDS / seconds) if seconds >= _YEAR_SECONDS else None
    if annualized is not None:
        _finite(annualized, "Annualized return")
    costs = {key: math.fsum(row["costs"][key] for row in path)
             for key in ("trading", "slippage", "overhead", "total")}
    turnover = math.fsum(row["turnover_notional"] for row in path)
    mean_nav = math.fsum(row["opening_value_before_flow"] + row["contribution"] for row in path) / len(path)
    return {
        "sessions": len(path), "initial_value": initial, "ending_value": ending,
        "contributions": flows, "profit": ending - initial - flows,
        "total_return": wealth - 1.0, "annualized_return": annualized,
        "max_drawdown": drawdown, "elapsed_days": seconds / 86400.0,
        "costs": costs, "turnover_notional": turnover,
        "turnover_to_mean_nav": turnover / mean_nav,
        "orders": sum(row["orders"] for row in path),
    }


def _account(study, dataset, scheduled, multiplier, *, benchmark=False,
             annotate_receipts=False):
    units = {symbol: 0.0 for symbol in sorted(study["assets"])}
    cash = float(study["initial_cash"])
    previous_value, previous_close, wealth = cash, None, 1.0
    flows = {flow["session"]: flow["amount"] for flow in study["contributions"]}
    annual_fee = study["benchmark_annual_fixed_cost" if benchmark else "annual_fixed_cost"] * multiplier
    trades, skipped, path = [], [], []
    for index, row in enumerate(dataset["sessions"]):
        opening, closing = utc(row["open_at"]), utc(row["close_at"])
        period_start = previous_close or opening
        overnight_fee = annual_fee * (opening - period_start).total_seconds() / _YEAR_SECONDS
        cash = _charge_overhead(cash, units, row["prices"], "open", overnight_fee)
        opening_preflow = _nav(cash, units, row["prices"], "open")
        contribution = flows.get(row["session"], 0.0)
        cash += contribution
        opening_postflow = _finite(opening_preflow + contribution, "Value after contribution", positive=True)
        trade_start, skipped_start = len(trades), len(skipped)

        if benchmark and index == 0:
            cash = _rebalance(study, row, units, cash, study["benchmark_weights"],
                              multiplier, trades, skipped, "benchmark_initial")
        elif benchmark and contribution:
            requested = {symbol: contribution * weight
                         for symbol, weight in study["benchmark_weights"].items()}
            budget = contribution * math.fsum(study["benchmark_weights"].values())
            cash = _buy_basket(study, row, units, cash, requested, budget, multiplier,
                               trades, skipped, "benchmark_contribution")
        elif not benchmark and index in scheduled:
            decision, receipt = scheduled[index]
            cash = _rebalance(study, row, units, cash, decision["weights"], multiplier,
                              trades, skipped, decision["decision_id"])
            if annotate_receipts:
                receipt["proxy_order_count"] = len(trades) - trade_start
                receipt["skipped_trade_count"] = len(skipped) - skipped_start

        post_trade_value = _nav(cash, units, row["prices"], "open")
        intraday_fee = annual_fee * (closing - opening).total_seconds() / _YEAR_SECONDS
        cash = _charge_overhead(cash, units, row["prices"], "close", intraday_fee)
        closing_value = _nav(cash, units, row["prices"], "close")
        overnight_factor = opening_preflow / previous_value
        intraday_factor = closing_value / opening_postflow
        post_trade_factor = post_trade_value / opening_postflow
        opening_wealth = wealth * overnight_factor
        wealth = _finite(opening_wealth * intraday_factor, "Flow-adjusted wealth index", positive=True)
        today_trades = trades[trade_start:]
        commissions = math.fsum(trade["cost_breakdown"]["total"] for trade in today_trades)
        impact = math.fsum(trade["slippage_cost"] for trade in today_trades)
        overhead = overnight_fee + intraday_fee
        path.append({
            "session": row["session"], "period_start": period_start.isoformat(),
            "open_at": row["open_at"], "close_at": row["close_at"],
            "previous_close_value": previous_value,
            "opening_value_before_flow": opening_preflow, "contribution": contribution,
            "closing_value": closing_value, "cash": cash,
            "positions_in_economic_units": dict(units),
            "holdings_value": closing_value - cash,
            "overnight_return_factor": overnight_factor,
            "post_trade_return_factor": post_trade_factor,
            "intraday_return_factor": intraday_factor,
            "daily_return": overnight_factor * intraday_factor - 1.0,
            "opening_wealth_index": opening_wealth,
            "post_trade_wealth_index": opening_wealth * post_trade_factor,
            "wealth_index": wealth,
            "costs": {"trading": commissions, "slippage": impact, "overhead": overhead,
                      "total": commissions + impact + overhead},
            "turnover_notional": math.fsum(trade["notional"] for trade in today_trades),
            "orders": len(today_trades),
        })
        previous_value, previous_close = closing_value, closing
    return {"metrics": _metrics(path), "daily_path": path, "trades": trades,
            "skipped_trades": skipped}


def _evaluation(study, dataset, decisions, receipts, strategy, benchmark):
    spec = study["evaluation"]
    start = spec["holdout_start"]
    partitions = {}
    for name, predicate in (("development", lambda day: day < start),
                            ("holdout", lambda day: day >= start)):
        partitions[name] = {
            "strategy": _metrics([row for row in strategy["daily_path"] if predicate(row["session"])]),
            "benchmark": _metrics([row for row in benchmark["daily_path"] if predicate(row["session"])]),
        }
    holdout_rows = [row for row in dataset["sessions"] if row["session"] >= start]
    # Compare with the declared boundary itself, not a later first available
    # data row.  Freezing before local midnight is deliberately conservative:
    # a pre-open announcement on the first holdout date is already holdout data.
    boundary = datetime.combine(session_date(start), time.min,
                                tzinfo=ZoneInfo(CALENDARS[study["calendar"]])).astimezone(timezone.utc)
    before_holdout = utc(spec["declared_at"]) < boundary
    executed = [r for r in receipts if r["status"] == "executed_proxy" and r["effective_session"] >= start]
    paper_count = sum(r["origin"] == "paper" for r in executed)
    retrospective_count = sum(r["origin"] == "retrospective" for r in executed)
    if dataset["synthetic"]:
        status = "synthetic_accounting_demonstration"
    elif not holdout_rows:
        status = "holdout_not_observed"
    elif not before_holdout:
        status = "holdout_declaration_not_prior_to_observed_period"
    elif any(r["origin"] == "retrospective" and r["status"] == "executed_proxy" for r in receipts):
        status = "retrospective_reconstruction_not_independent_validation"
    elif not executed:
        status = "no_effective_holdout_decisions"
    else:
        status = "paper_or_operator_asserted_timestamps_not_certified_performance"
    return {
        "holdout_start": start, "holdout_boundary_at": boundary.isoformat(),
        "declared_at": spec["declared_at"],
        "declared_before_holdout": before_holdout,
        "declaration_timestamp_independently_verified": False,
        "objective": spec["objective"], "status": status, "partitions": partitions,
        "holdout_session_count": len(holdout_rows),
        "minimum_holdout_sessions": spec["minimum_holdout_sessions"],
        "minimum_holdout_sessions_met": len(holdout_rows) >= spec["minimum_holdout_sessions"],
        "holdout_paper_decisions": paper_count,
        "holdout_retrospective_decisions": retrospective_count,
        "minimum_paper_decisions": spec["minimum_paper_decisions"],
        "minimum_paper_decisions_met": paper_count >= spec["minimum_paper_decisions"],
        "statistical_or_investment_approval": False,
        "limitations": [
            "A partition is not proof it was untouched during strategy selection; declaration time is an operator assertion.",
            "Declared-before-holdout means before midnight at the stated exchange-local boundary, not before the first available bar.",
            "Paper timestamps do not certify independent knowledge, actual fills or future profitability.",
            "All rules, universes, prompts and parameter trials must be disclosed separately.",
            "Historical index or US-security evidence does not validate a different Indian investable product.",
        ],
    }


def simulate(study, dataset, decisions, *, cost_multiplier=1.0, delay_sessions=0):
    """Evaluate supplied hypothetical weights without network or broker actions.

    ``cost_multiplier`` scales commissions, economic slippage and fixed costs.
    ``delay_sessions`` adds whole supplied sessions to candidate decision delay;
    the predeclared buy-and-hold reference still begins at the first open.
    No return, data-quality flag or sample count can automatically approve a
    strategy.  Output is pre-personal-tax and uses fractional proxy accounting.
    """
    validate_study(study)
    validate_dataset(dataset, study)
    validate_decisions(decisions, study)
    if (isinstance(cost_multiplier, bool) or not isinstance(cost_multiplier, (int, float))
            or not math.isfinite(cost_multiplier) or not 0 <= cost_multiplier <= 100):
        raise ValueError("cost_multiplier must be finite and between zero and 100")
    if isinstance(delay_sessions, bool) or not isinstance(delay_sessions, int) or not 0 <= delay_sessions <= 252:
        raise ValueError("delay_sessions must be an integer between zero and 252")
    try:
        scheduled, receipts = _schedule(study, dataset["sessions"], decisions, delay_sessions)
        strategy = _account(study, dataset, scheduled, cost_multiplier, annotate_receipts=True)
        benchmark = _account(study, dataset, {}, cost_multiplier, benchmark=True)
        gross_strategy = _account(study, dataset, scheduled, 0.0)
        gross_benchmark = _account(study, dataset, {}, 0.0, benchmark=True)
        gaps = []
        if any(not item["evidence"] for item in decisions):
            gaps.append("One or more supplied hypotheses have no attached bot-archive evidence; price-only logic is not verified.")
        if any(item["origin"] == "retrospective" for item in decisions):
            gaps.append("Retrospective decisions are reconstructions, not contemporaneously recorded paper observations.")
        if any(e["health_status"] == "WARN" for item in decisions for e in item["evidence"]):
            gaps.append("At least one decision references a partial-health archive; missing-source impact is not established.")
        if utc(study["universe_declared_at"]) >= utc(dataset["sessions"][0]["open_at"]):
            gaps.append("Universe was declared after the first evaluated open; selection/survivorship bias remains unresolved.")
        gaps.append("Operator-supplied decision logic, archive content and knowledge times are not independently certified by this simulator.")
        if study["cost_model"]["kind"] == "india_delivery":
            gaps.append("Dated India tariff estimates are a prospective cost scenario, not reconstructed historical contract notes.")
        result = {
            "schema_version": "portfolio-lab.report.v1", "research_only": True,
            "study_id": study["study_id"], "dataset_id": dataset["dataset_id"],
            "study_sha256": digest(study), "dataset_sha256": digest(dataset),
            "decisions_sha256": digest(decisions), "strategy_sha256": study["strategy_sha256"],
            "synthetic": dataset["synthetic"], "currency": study["currency"],
            "calendar": study["calendar"], "price_basis": "total_return_proxy",
            "cost_multiplier": cost_multiplier, "extra_delay_sessions": delay_sessions,
            "assumptions": [
                "Fractional economic units of one immutable total-return proxy vintage; no executable share quantities.",
                "Splits and reinvested dividends must already be coherent in supplied open/close ratios; no separate dividend payments.",
                "Decisions execute at the first supplied open strictly after receipt/decision plus latency and explicit session delay.",
                "Target weights use pre-trade value; buys shrink proportionally to afford costs and preserve requested cash where possible.",
                "Cash earns zero; strategy contributions remain cash until the next supplied decision.",
                "Benchmark buys initial weights and adds the same weights to contributions without otherwise rebalancing.",
                "Slippage is an economic charge on notional, not an observed executable quote or fill.",
                "Annual fixed costs accrue over calendar time and proportionally reduce proxy value; no real liquidation, withdrawal or sale-tax process.",
                "Cost multiplier scales trading, slippage and overhead; gross comparators remove all three with the same supplied rules.",
                "Returns are before personal taxes, FX, settlement, integer-lot constraints and actual liquidity/fill verification.",
                "Drawdown uses every supplied open, post-trade open and close; unobserved intraday extremes are unknown.",
                "Turnover ratio is gross traded notional divided by mean pre-trade opening value, not an annualized rate.",
            ],
            "decision_executions": receipts, "evidence_gaps": gaps,
            "strategy": strategy, "benchmark": benchmark,
            "gross_strategy": gross_strategy, "gross_benchmark": gross_benchmark,
            "evaluation": _evaluation(study, dataset, decisions, receipts, strategy, benchmark),
        }
        # Guard all output fields, including extreme price-ratio arithmetic.
        canonical(result)
        return result
    except (OverflowError, ZeroDivisionError) as exc:
        raise ValueError("Inputs exceed finite cash-funded proxy accounting range") from exc
