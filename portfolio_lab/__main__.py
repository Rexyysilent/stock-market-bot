"""Opt-in research CLI. No command places orders or starts a scheduled service."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from stateutil import atomic_write_json
from .data import MAX_JSON_BYTES, digest, load_decisions, load_json, utc, validate_dataset, validate_study


def _emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False))


def _private_output(path):
    target = Path(path).expanduser().resolve()
    repo = Path(__file__).resolve().parents[1]
    if target == repo or target in repo.parents:
        raise ValueError("Choose a new private output path")
    if target.is_relative_to(repo) and target.relative_to(repo).parts[0] not in (
            "workspaces", "study-runs", "output", "review-results"):
        raise ValueError("Local results belong in an ignored workspace directory")
    if target.exists():
        raise ValueError("Output exists; choose a new path so earlier research is preserved")
    return target


def _evaluate(study, dataset, decisions, args, *, journal=None):
    from .engine import simulate
    from .report import save_run
    report = simulate(study, dataset, decisions, cost_multiplier=args.cost_multiplier,
                      delay_sessions=args.delay_sessions)
    if journal is not None:
        # Study declarations may be retrospective; the actual workspace freeze
        # is a distinct locally recorded event and must not be hidden.
        evaluation = report["evaluation"]
        boundary = utc(evaluation["holdout_boundary_at"])
        evaluation["journal_frozen_at"] = journal["frozen_at"]
        evaluation["local_journal_frozen_before_holdout_date"] = utc(journal["frozen_at"]) < boundary
        evaluation["local_receipt_chain_verified"] = True
        evaluation["local_receipt_chain_is_external_attestation"] = False
        report["evidence_gaps"].append("Local journal hashes were verified; this is not independent timestamp, source-truth or fill attestation.")
        if not evaluation["local_journal_frozen_before_holdout_date"]:
            report["evidence_gaps"].append("The actual local study freeze occurred after the declared holdout boundary.")
    if args.stress:
        report["sensitivity"] = []
        for name, multiplier, delay in (("double_costs", 2.0 * args.cost_multiplier, args.delay_sessions),
                                         ("one_more_session_delay", args.cost_multiplier, args.delay_sessions + 1)):
            other = simulate(study, dataset, decisions, cost_multiplier=multiplier, delay_sessions=delay)
            report["sensitivity"].append({"name": name, "cost_multiplier": multiplier,
                                          "delay_sessions": delay, "strategy": other["strategy"]["metrics"],
                                          "benchmark": other["benchmark"]["metrics"],
                                          "decision_executions": other["decision_executions"]})
    extra = {"journal_genesis_sha256": journal["genesis_sha256"]} if journal else None
    output = save_run(args.out, study, dataset, decisions, report, extra=extra)
    _emit({"report_directory": str(output), "synthetic": dataset["synthetic"],
           "research_only": True, "strategy": report["strategy"]["metrics"],
           "benchmark": report["benchmark"]["metrics"], "evaluation_status": report["evaluation"]["status"]})
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="Run fabricated accounting examples without dependencies or network")
    demo.add_argument("--out", type=Path, required=True)
    demo.add_argument("--stress", action="store_true")
    demo.set_defaults(cost_multiplier=1.0, delay_sessions=0)
    validate = sub.add_parser("validate", help="Validate local inputs, not provider truth or trading suitability")
    for name in ("study", "dataset", "decisions"):
        validate.add_argument(f"--{name}", type=Path, required=True)
    run = sub.add_parser("evaluate", help="Measure supplied historical hypotheses and save a new receipt")
    for name in ("study", "dataset", "decisions"):
        run.add_argument(f"--{name}", type=Path, required=True)
    journal_run = sub.add_parser("evaluate-journal", help="Measure locally recorded paper hypotheses")
    journal_run.add_argument("--workspace", type=Path, required=True)
    journal_run.add_argument("--dataset", type=Path, required=True)
    for command in (run, journal_run):
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--cost-multiplier", type=float, default=1.0)
        command.add_argument("--delay-sessions", type=int, default=0)
        command.add_argument("--stress", action="store_true", help="Also compare double costs and one more session of delay")
    freeze = sub.add_parser("freeze", help="Pin a study in a new private paper workspace")
    freeze.add_argument("--study", type=Path, required=True)
    freeze.add_argument("--workspace", type=Path, required=True)
    record = sub.add_parser("record", help="Record supplied weights and exact local bot archives; no orders")
    record.add_argument("--workspace", type=Path, required=True)
    record.add_argument("--decision", type=Path, required=True)
    record.add_argument("--archive", type=Path, action="append", default=[])
    status = sub.add_parser("journal", help="Read local paper receipts and verify their content chain")
    status.add_argument("--workspace", type=Path, required=True)
    acquire = sub.add_parser("fetch-yahoo", help="Explicit read-only acquisition using existing optional provider packages")
    acquire.add_argument("--study", type=Path, required=True)
    acquire.add_argument("--start", required=True, help="First exchange-local session date, inclusive")
    acquire.add_argument("--end", required=True, help="Last completed session date, inclusive")
    acquire.add_argument("--out", type=Path, required=True, help="New dataset JSON in a private workspace")
    economics = sub.add_parser("income", help="Arithmetic scenarios, not forecasts or sustainable withdrawal advice")
    economics.add_argument("--capital", type=float, required=True)
    economics.add_argument("--currency", choices=("INR", "USD"), default="INR")
    economics.add_argument("--annual-rates", default="-20,0,4,8,12", help="Comma-separated hypothetical percentages")
    economics.add_argument("--annual-fixed-cost", type=float, default=0.0)
    economics.add_argument("--monthly-goal", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "demo":
            from .demo import demo_inputs
            return _evaluate(*demo_inputs(), args)
        if args.command in ("validate", "evaluate"):
            study = validate_study(load_json(args.study))
            dataset = validate_dataset(load_json(args.dataset), study)
            decisions = load_decisions(args.decisions, study)
            if args.command == "validate":
                _emit({"contract_valid": True, "provider_truth_verified": False,
                       "synthetic": dataset["synthetic"], "sessions": len(dataset["sessions"]),
                       "decisions": len(decisions), "study_sha256": digest(study),
                       "dataset_sha256": digest(dataset)})
            else:
                return _evaluate(study, dataset, decisions, args)
        elif args.command == "freeze":
            from .journal import create_workspace
            state = create_workspace(args.workspace, validate_study(load_json(args.study)))
            _emit({key: state[key] for key in ("study_sha256", "frozen_at", "genesis_sha256")})
        elif args.command == "record":
            from .journal import record_decision
            result = record_decision(args.workspace, load_json(args.decision), args.archive)
            _emit({"decision_id": result["decision"]["decision_id"], "recorded_at": result["decision"]["recorded_at"],
                   "receipt_sha256": result["receipt"]["receipt_sha256"], "already_recorded": result["already_recorded"]})
        elif args.command in ("journal", "evaluate-journal"):
            from .journal import read_journal
            state = read_journal(args.workspace)
            if args.command == "journal":
                _emit({"study_sha256": state["study_sha256"], "frozen_at": state["frozen_at"],
                       "decisions": len(state["decisions"]), "local_receipt_chain_verified": True,
                       "latest_receipt": state["receipts"][-1] if state["receipts"] else None})
            else:
                dataset = validate_dataset(load_json(args.dataset), state["study"])
                return _evaluate(state["study"], dataset, state["decisions"], args, journal=state)
        elif args.command == "fetch-yahoo":
            target = _private_output(args.out)
            from .providers import fetch_yahoo
            dataset = fetch_yahoo(validate_study(load_json(args.study)), args.start, args.end,
                                  cache_dir=target.parent / "yfinance-cache")
            if len(json.dumps(dataset, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 1 > MAX_JSON_BYTES:
                raise ValueError("Serialized dataset exceeds the input reader's size limit")
            if target.exists():
                raise ValueError("Output appeared during acquisition; choose a new dataset path")
            atomic_write_json(target, dataset, indent=2)
            _emit({"dataset": str(target), "dataset_sha256": digest(dataset),
                   "sessions": len(dataset["sessions"]), "synthetic": dataset["synthetic"],
                   "price_basis": dataset["price_basis"]})
        elif args.command == "income":
            from .economics import income_scenarios
            rates = [float(value.strip()) / 100 for value in args.annual_rates.split(",")]
            _emit(income_scenarios(args.capital, rates, annual_fixed_cost=args.annual_fixed_cost,
                                   monthly_income_goal=args.monthly_goal, currency=args.currency))
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
