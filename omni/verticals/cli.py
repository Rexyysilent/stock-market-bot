"""Offline vertical command hooks. No command enables or executes a source."""
from __future__ import annotations

from pathlib import Path

from ..identities import digest
from ..transport import CONTRACTS
from .common import load_local_json, persist_batch
from .eia import pin_metadata, parse_eia_observations
from .eia860 import parse_eia860_bundle
from .nrc import parse_nrc_metadata
from .procurement import parse_awards, parse_transactions

COMMANDS = {"vertical-plan", "vertical-parse"}


def register_commands(sub):
    sub.add_parser("vertical-plan", help="show disabled source contracts and unresolved requirements")
    parser = sub.add_parser("vertical-parse", help="parse a local source payload into pending draft receipts")
    parser.add_argument("adapter", choices=("nrc-aps", "eia-metadata", "eia-nuclear", "eia860", "usaspending-awards", "usaspending-transactions"))
    parser.add_argument("input", type=Path)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--policy-fingerprint", required=True)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--query", type=Path)
    parser.add_argument("--vintage", type=int, default=2025)
    parser.add_argument("--evidence-kind", choices=("unverified_local_input", "synthetic", "historical_replay"), default="unverified_local_input")
    parser.add_argument("--persist", action="store_true", help="append only draft receipts to the explicitly selected local database")


def adapter_plan():
    return {"contract": "omni-vertical-plan-1", "network_requests": 0, "automatic_activation": False,
            "source_approvals": "pending", "human_review_status": "pending", "signal_eligible": False,
            "adapters": [
                {"id": "nrc-aps", "enabled": False, "pure_parser": True,
                 "blocked_by": ["operator_source_purpose_approval", "typed_transport_contract_approval", "credential_reference",
                                "APS_added_timestamp_timezone", "stable_pagination_contract", "docket_project_crosswalk_review"]},
                {"id": "eia-nuclear", "enabled": False, "pure_parser": True,
                 "blocked_by": ["operator_source_purpose_approval", "typed_transport_contract_approval", "credential_reference",
                                "actual_nuclear_metadata_units_facets_snapshot", "unit_entity_and_NRC_lineage_review"]},
                {"id": "eia860", "enabled": False, "pure_parser": True, "supported_vintage": 2025,
                 "blocked_by": ["operator_source_purpose_approval", "source_bundle_provenance_review", "dated_entity_crosswalk_review"]},
                {"id": "usaspending", "enabled": False, "pure_parser": True,
                 "blocked_by": ["operator_source_purpose_approval", "typed_transport_contract_approval", "bounded_operator_query",
                                "publication_and_update_deletion_window", "currency_contract", "recipient_parent_crosswalk_review"]}],
            "read_only_contracts": {key: {**value, "contract_hash": digest(value), "enabled": False}
                                    for key, value in CONTRACTS.items()}}


def dispatch(args, *, store=None):
    if args.command == "vertical-plan":
        return adapter_plan()
    if args.command != "vertical-parse":
        raise ValueError("unsupported vertical command")
    kwargs = {"observed_at": args.observed_at, "source_policy_fingerprint": args.policy_fingerprint,
              "evidence_kind": args.evidence_kind}
    if args.adapter == "eia860":
        if args.input.stat().st_size > 30_000_000:
            raise ValueError("eia860_file_size_limit")
        result = parse_eia860_bundle(args.input.read_bytes(), vintage=args.vintage, **kwargs)
    else:
        payload = load_local_json(args.input)
        if args.adapter == "nrc-aps":
            query = load_local_json(args.query) if args.query else {"query": "unverified_local_input"}
            result = parse_nrc_metadata(payload, query_fingerprint=digest(query), **kwargs)
        elif args.adapter == "eia-metadata":
            if args.persist:
                raise ValueError("metadata pin must be reviewed before persistence through a candidate batch")
            result = pin_metadata(payload, observed_at=args.observed_at, evidence_kind=args.evidence_kind)
        elif args.adapter == "eia-nuclear":
            if not args.metadata:
                raise ValueError("pinned route metadata file required")
            result = parse_eia_observations(payload, metadata_snapshot=load_local_json(args.metadata), **kwargs)
        else:
            if not args.query:
                raise ValueError("exact bounded source query file required")
            parse = parse_awards if args.adapter == "usaspending-awards" else parse_transactions
            result = parse(payload, approved_query=load_local_json(args.query), **kwargs)
    if args.persist:
        if store is None:
            raise ValueError("explicit local event store required")
        return persist_batch(store, result, observed_at=args.observed_at)
    return result
