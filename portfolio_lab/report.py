"""Readable research receipts, with assumptions before performance numbers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from stateutil import atomic_write_json, atomic_write_bytes
from .data import digest


def _verify_report(report, study, dataset, decisions=None):
    if (report.get("schema_version") != "portfolio-lab.report.v1"
            or report.get("study_sha256") != digest(study)
            or report.get("dataset_sha256") != digest(dataset)
            or report.get("synthetic") is not dataset["synthetic"]
            or report.get("currency") != study["currency"]
            or report.get("calendar") != study["calendar"]
            or report.get("dataset_id") != dataset["dataset_id"]
            or report.get("price_basis") != dataset["price_basis"]
            or report.get("strategy_sha256") != study["strategy_sha256"]
            or report.get("research_only") is not True
            or report.get("study_id") != study["study_id"]):
        raise ValueError("Report identity disagrees with its study/dataset; refusing mislabelled results")
    if decisions is not None and report.get("decisions_sha256") != digest(decisions):
        raise ValueError("Report decision identity disagrees with the supplied hypotheses")


def _percentage(value):
    return "unavailable" if value is None else f"{value * 100:,.2f}%"


def _number(value):
    return "unavailable" if value is None else f"{value:,.2f}"


def render_report(report, study, dataset):
    _verify_report(report, study, dataset)
    lines = ["# Portfolio research receipt", "",
             "**SYNTHETIC DEMONSTRATION — NOT INVESTMENT EVIDENCE**" if dataset["synthetic"]
             else "**Historical return-proxy research — not an account statement or verified trading result.**",
             "", f"Study: `{study['study_id']}`. Currency: **{study['currency']}**. Calendar: `{study['calendar']}`.",
             "", "This model uses fractional adjusted economic units, not executable share quantities. "
             "Results include configured trading friction and running costs, before personal taxes. "
             "Dividend cash payments, actual lots, settlements, FX conversion and live liquidity are not modelled.",
             "", f"Evaluation status: **{report['evaluation']['status']}**. "
             f"Declared before the configured holdout boundary: **{report['evaluation']['declared_before_holdout']}**. "
             "No result from this module constitutes investment approval.",
             "", "## Whole-period comparison", "",
             "| Measurement | Supplied hypothesis | Passive benchmark |", "|---|---:|---:|"]
    candidate = report["strategy"]["metrics"]
    benchmark = report["benchmark"]["metrics"]
    for label, key, formatter in (
        ("Starting value", "initial_value", _number),
        ("Contributions", "contributions", _number),
        ("Ending value", "ending_value", _number),
        ("Gain after configured costs", "profit", _number),
        ("Time-weighted return", "total_return", _percentage),
        ("Annualized return (only for at least one year)", "annualized_return", _percentage),
        ("Maximum flow-adjusted drawdown", "max_drawdown", _percentage),
        ("Turnover notional", "turnover_notional", _number),
        ("Hypothetical transactions", "orders", _number),
    ):
        lines.append(f"| {label} | {formatter(candidate.get(key))} | {formatter(benchmark.get(key))} |")
    for name in ("trading", "slippage", "overhead", "total"):
        lines.append(f"| {name.title()} costs | {_number(candidate.get('costs', {}).get(name))} | "
                     f"{_number(benchmark.get('costs', {}).get(name))} |")
    lines.append(f"| Gross time-weighted return before all configured costs | "
                 f"{_percentage(report['gross_strategy']['metrics']['total_return'])} | "
                 f"{_percentage(report['gross_benchmark']['metrics']['total_return'])} |")
    counts = {}
    for receipt in report.get("decision_executions", []):
        key = receipt["status"]
        counts[key] = counts.get(key, 0) + 1
    lines += ["", "Decision outcomes: " + (", ".join(f"{key}: {value}" for key, value in sorted(counts.items())) or "no supplied decisions") + ".",
              f"Skipped candidate transactions: **{len(report['strategy']['skipped_trades'])}**; "
              f"skipped benchmark transactions: **{len(report['benchmark']['skipped_trades'])}**. "
              "Reasons remain in report.json; a skipped/pending decision is not a fill."]
    if "journal_frozen_at" in report["evaluation"]:
        lines += ["", f"Actual local journal freeze: **{report['evaluation']['journal_frozen_at']}**. "
                  f"Frozen before holdout: **{report['evaluation']['local_journal_frozen_before_holdout_date']}**. "
                  "Local receipt-chain verification is not external timestamp attestation."]
    lines += ["", "Contributions are external cash, not investment profit. Total returns are not cash distributions "
              "or promised monthly income. Overhead is an economic value deduction, not a simulated broker withdrawal.",
              "", "## Chronological partitions", "",
              f"Holdout boundary: **{study['evaluation']['holdout_start']}**. "
              "A historical period labelled holdout is not proof that decisions were made without seeing it.", "",
              "| Partition | Hypothesis TWR | Benchmark TWR | Hypothesis drawdown | Sessions |",
              "|---|---:|---:|---:|---:|"]
    for partition, values in report.get("evaluation", {}).get("partitions", {}).items():
        a, b = values.get("strategy", {}), values.get("benchmark", {})
        lines.append(f"| {partition} | {_percentage(a.get('total_return'))} | {_percentage(b.get('total_return'))} | "
                     f"{_percentage(a.get('max_drawdown'))} | {a.get('sessions', 0)} |")
    if report.get("sensitivity"):
        lines += ["", "## Fixed-rule sensitivity", "",
                  "| Scenario | Hypothesis TWR | Benchmark TWR | Hypothesis drawdown |",
                  "|---|---:|---:|---:|"]
        for scenario in report["sensitivity"]:
            a, b = scenario["strategy"], scenario["benchmark"]
            lines.append(f"| {scenario['name']} | {_percentage(a['total_return'])} | "
                         f"{_percentage(b['total_return'])} | {_percentage(a['max_drawdown'])} |")
    lines += ["", "## Evidence limitations", ""]
    gaps = report.get("evidence_gaps", [])
    if isinstance(gaps, list):
        lines.extend(f"- {value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)}" for value in gaps)
    else:
        lines.append(json.dumps(gaps, ensure_ascii=False, indent=2))
    lines += ["- No automated approval, live orders or profit certification is produced.",
              "- A local receipt records supplied content and local time; it is not external timestamp notarization.",
              "- Source and calendar assertions require independent checking; passing validators is not market-data certification.",
              "", "## Reproduce", "",
              "The companion JSON retains daily paths, hypothetical transactions, timing, costs and evidence flags. "
              "The exact input files and their canonical hashes are saved beside this report.", "",
              f"- Study SHA-256: `{digest(study)}`",
              f"- Dataset SHA-256: `{digest(dataset)}`",
              f"- Price basis: `{dataset['price_basis']}`",
              f"- Source: {dataset['source']['name']}",
              f"- Source URL: {dataset['source']['url']}",
              f"- Adjustment convention: {dataset['adjustment_policy']}", ""]
    return "\n".join(lines)


def save_run(out, study, dataset, decisions, report, *, extra=None):
    _verify_report(report, study, dataset, decisions)
    rendered = render_report(report, study, dataset)
    root = Path(out).resolve()
    repo = Path(__file__).resolve().parents[1]
    if root == repo or root in repo.parents:
        raise ValueError("Use a dedicated output directory, not the repository or its parents")
    if root.is_relative_to(repo) and root.relative_to(repo).parts[0] not in (
            "workspaces", "study-runs", "output", "review-results"):
        raise ValueError("Research outputs inside this repository must use an ignored workspace directory")
    # Never overwrite a research result, even when the latest run looks better.
    root.mkdir(parents=True, exist_ok=False)
    payloads = {
        "study.json": study, "dataset.json": dataset,
        "decisions.json": {"schema_version": "portfolio-lab.decisions.v1", "decisions": decisions},
        "report.json": report,
    }
    receipt = {"schema_version": "portfolio-lab.run.v1", "created_at": datetime.now(timezone.utc).isoformat(),
               "inputs": {key: digest(value) for key, value in payloads.items()},
               "research_only": True}
    if extra:
        receipt["context"] = extra
    # A missing receipt identifies an incomplete write; no successful partial run is advertised.
    for name, value in payloads.items():
        atomic_write_json(root / name, value, indent=2)
    atomic_write_bytes(root / "report.md", rendered.encode("utf-8"))
    atomic_write_json(root / "receipt.json", receipt, indent=2)
    return root
