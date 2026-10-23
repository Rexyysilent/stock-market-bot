"""Fabricated, dependency-free examples for accounting and local integration."""
from __future__ import annotations

from .data import digest


def demo_inputs():
    rule = {"name": "fabricated-exposure-example", "version": 1}
    study = {
        "schema_version": "portfolio-lab.study.v1", "study_id": "synthetic-demo",
        "strategy_sha256": digest(rule), "currency": "USD", "calendar": "XNYS",
        "assets": {"ALFA": {"kind": "equity"}, "BETA": {"kind": "equity_etf"},
                   "BMK": {"kind": "equity_etf"}},
        "universe_basis": "fixed_operator_selection",
        "universe_declared_at": "2024-01-01T00:00:00Z", "initial_cash": 10000.0,
        "benchmark_weights": {"BMK": 1.0},
        "cost_model": {"kind": "generic", "buy_bps": 5.0, "sell_bps": 5.0,
                       "fixed_per_order": 1.0},
        "slippage_bps": 5.0, "annual_fixed_cost": 120.0,
        "benchmark_annual_fixed_cost": 0.0,
        "execution": {"latency_seconds": 60, "extra_delay_sessions": 0,
                      "max_decision_age_days": 35, "min_trade_value": 10.0},
        "limits": {"max_weight": 1.0, "max_gross": 1.0},
        "evaluation": {"holdout_start": "2024-01-08", "declared_at": "2024-01-01T00:00:00Z",
                       "minimum_holdout_sessions": 252, "minimum_paper_decisions": 12,
                       "objective": "Demonstrate accounting; no real investment hypothesis."},
        "contributions": [{"session": "2024-01-08", "amount": 1000.0}],
        "notes": "All symbols, prices and choices are fabricated. Not a portfolio recommendation.",
    }
    days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
            "2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11"]
    alfa = [(100, 101), (120, 119), (118, 120), (121, 117),
            (116, 113), (114, 112), (112, 114), (115, 116)]
    beta = [(100, 100), (100, 101), (101, 102), (102, 101),
            (101, 102), (102, 103), (103, 102), (102, 104)]
    bmk = [(100, 100.5), (101, 101.3), (101.2, 101.5), (101.5, 101),
           (100.5, 101), (101, 101.5), (101.5, 101.7), (101.8, 102)]
    dataset = {
        "schema_version": "portfolio-lab.dataset.v1", "dataset_id": "fabricated-eight-sessions",
        "synthetic": True, "currency": "USD", "calendar": "XNYS",
        "price_basis": "total_return_proxy",
        "adjustment_policy": "Fabricated coherent return units; no cash dividends or split events.",
        "acquired_at": "2024-01-12T00:00:00Z",
        "source": {"name": "Deterministic synthetic fixture", "url": "https://example.invalid/synthetic",
                   "rights_note": "Authored synthetic values; no third-party market data.",
                   "calendar_source": "Explicit fabricated eight-session test schedule."},
        "expected_sessions": days,
        "sessions": [{"session": day, "open_at": day + "T14:30:00Z", "close_at": day + "T21:00:00Z",
                      "prices": {symbol: {"open": pair[0], "close": pair[1]}
                                 for symbol, pair in zip(("ALFA", "BETA", "BMK"), (alfa[i], beta[i], bmk[i]))}}
                     for i, day in enumerate(days)],
    }
    decisions = []
    for identifier, at, weights in (
        ("demo-one", "2024-01-02T21:05:00Z", {"ALFA": 0.5, "BETA": 0.4}),
        ("demo-two", "2024-01-08T21:05:00Z", {"BETA": 0.8}),
    ):
        decisions.append({"decision_id": identifier, "strategy_sha256": study["strategy_sha256"],
                          "decided_at": at, "information_cutoff": at, "weights": weights,
                          "origin": "retrospective", "evidence": []})
    return study, dataset, decisions
