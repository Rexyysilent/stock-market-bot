"""Local expansion proposal, observation and report commands; no activation."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .expansion import create_proposal, observation_request, record_observation, expansion_report
from .verticals.common import load_local_json

COMMANDS = {"expansion-plan", "expansion-propose", "expansion-observe", "expansion-report"}


def register_commands(sub):
    sub.add_parser("expansion-plan", help="show evidence requirements without selecting candidates")
    propose = sub.add_parser("expansion-propose", help="freeze an explicit operator candidate list before observation")
    propose.add_argument("protocol_id"); propose.add_argument("candidates", type=Path)
    propose.add_argument("--minimum-independent-cases", type=int, default=100)
    observe = sub.add_parser("expansion-observe", help="prepare or import one referenced measurement")
    observe.add_argument("proposal_id"); observe.add_argument("input", type=Path)
    observe.add_argument("--prepare", action="store_true", help="return the exact subject for an operator's attestation")
    report = sub.add_parser("expansion-report", help="derive a proposal-only report from referenced records")
    report.add_argument("proposal_id"); report.add_argument("--reviews", type=Path)
    report.add_argument("--persist", action="store_true")


def dispatch(args, *, store=None, registry=None, policies=None, instruments=None, current_coverage=None, now=None):
    if args.command == "expansion-plan":
        return {"contract": "omni-expansion-1", "status": "pending", "candidate_list": None,
                "requirements": ["explicit_operator_candidates", "versioned_current_coverage", "future_source_first_protocol",
                                 "issuer_security_identity_evidence", "complete_adjudicated_source_inventory",
                                 "useful_event_assessments", "complete_aligned_request_logs", "complete_aligned_operating_costs",
                                 "wave_a_and_candidate_review_attestations"],
                "automatic_membership_change": False, "automatic_activation": False, "network_requests": 0,
                "human_review_status": "pending", "signal_eligible": False}
    if any(x is None for x in (store, registry, policies, instruments, current_coverage, now)):
        raise ValueError("explicit current local context required")
    if args.command == "expansion-propose":
        return create_proposal(store, args.protocol_id, registry, policies, instruments, list(current_coverage),
                               load_local_json(args.candidates), now=now, minimum_independent_cases=args.minimum_independent_cases)
    if args.command == "expansion-observe":
        record = load_local_json(args.input)
        if args.prepare:
            subject, refs = observation_request(store, args.proposal_id, record, registry, policies, now=now)
            from .identities import digest
            return {"subject": subject, "subject_hash": digest(subject), "refs": refs,
                    "attestation_bindings": {k: subject[k] for k in ("proposal_id", "implementation_hash", "policy_hash",
                        "source_scope_hash", "registry_fingerprint", "dependency_locks_hash")},
                    "attestation_purpose": "expansion_observation", "human_review_status": "pending"}
        return record_observation(store, args.proposal_id, record, registry, policies, now=now)
    if args.command == "expansion-report":
        reviews = load_local_json(args.reviews) if args.reviews else {}
        if not isinstance(reviews, dict) or set(reviews) - {"wave_a_review_ref", "candidate_review_ref", "identity_review_refs"}:
            raise ValueError("review file accepts only exact receipt references")
        return expansion_report(store, args.proposal_id, registry, policies, instruments, list(current_coverage),
                                now=now, persist=args.persist, **reviews)
    raise ValueError("unsupported expansion command")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--clinical-catalog", type=Path, help="explicit reviewed clinical scope catalog")
    parser.add_argument("--sources", type=Path, default=Path(__file__).resolve().parent.parent / "registry/event-sources.json")
    register_commands(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(argv)
    if args.command == "expansion-plan":
        result = dispatch(args)
    else:
        if args.db is None:
            raise ValueError("--db is required for the local evidence store")
        from .store import EventStore
        from .identities import load_registry
        from instrument_registry import load_registry as load_instruments
        from config import EDITORIAL_COVERAGE_TICKERS
        now = datetime.now(timezone.utc).isoformat()
        store = EventStore(args.db)
        try:
            registry = load_registry(clinical_catalog_path=args.clinical_catalog) if args.clinical_catalog else load_registry()
            result = dispatch(args, store=store, registry=registry, policies=load_local_json(args.sources)["policies"],
                              instruments=load_instruments(), current_coverage=list(EDITORIAL_COVERAGE_TICKERS), now=now)
        finally:
            store.close()
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError, PermissionError, OSError) as exc:
        print(json.dumps({"status": "failed", "detail": str(exc)[:300]}), file=sys.stderr)
        raise SystemExit(1)
