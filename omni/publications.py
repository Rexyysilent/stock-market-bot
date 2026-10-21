"""Complete proposal census, separate from independently sampled source recall.

The conservative frozen publication rule includes every scoped classifier event
packet. Human rejection, a display cap, or a later correction cannot remove an
earlier proposal. Labels remain supplied human evidence, never generated here.
"""
from __future__ import annotations

import json

from .identities import digest, instant
from .receipts import get_receipt, list_receipts, put_receipt
from .prospective import (
    VERSION, _bindings, _closed, _inventory, _origin, _review, _text, _unique,
    current_bindings, protocol_state, transaction)

INVENTORY_KIND = "prospective_publication_inventory"
ADJUDICATION_KIND = "prospective_publication_adjudication"


def _rows(store, protocol_id, kind, *, at=None):
    return [r for r in list_receipts(store, kind=kind)
            if r["payload"].get("protocol_id") == protocol_id
            and (at is None or instant(r["observed_at"]) <= instant(at))]


def _append(store, kind, payload, *, now, refs):
    receipt_id = "prospective-publications:" + digest([kind, payload])
    try:
        previous = get_receipt(store, receipt_id, kind=kind)
    except (ValueError, KeyError):
        previous = None
    if previous:
        return previous
    return put_receipt(store, kind, payload, observed_at=now, refs=sorted(set(refs)),
                       receipt_id=receipt_id, manage_transaction=False)


def closed_inventory(store, protocol_id, *, at=None):
    rows = _rows(store, protocol_id, INVENTORY_KIND, at=at)
    if len(rows) > 1:
        raise ValueError("multiple publication inventories for one protocol")
    return rows[0] if rows else None


def proposal_inventory(store, protocol, *, now):
    """Read immutable emitted packets, never only current/approved event heads."""
    if protocol["precision_stream_rule"] != "all_scoped_classifier_event_units":
        raise ValueError("unsupported frozen precision stream")
    start, end = instant(protocol["window_start"]), instant(protocol["window_end"])
    frame = _inventory(store, protocol, now=now)
    by_origin = {}
    for unit in frame["units"]:
        by_origin.setdefault(unit["origin_id"], []).append(unit)
    groups, reasons = {}, []
    rows = store.conn.execute(
        "SELECT event_id,revision_id,packet_hash,payload,observed_at FROM event_packets ORDER BY rowid"
    ).fetchall()
    for row in rows:
        if not start <= instant(row["observed_at"]) <= min(end, instant(now)):
            continue
        event = json.loads(row["payload"])
        if digest(event) != row["packet_hash"]:
            raise ValueError("immutable publication packet hash mismatch")
        family = event["family"]
        supports = {(c["support"]["evidence_id"], c["support"]["revision_id"])
                    for c in event["claims"]}
        documents, scopes, origins, correspondence = [], [], set(), []
        native_refs = []
        for eid, revision in sorted(supports):
            evidence = store.conn.execute(
                "SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?", (eid, revision)).fetchone()
            if not evidence:
                raise ValueError("publication proposal evidence missing")
            document = json.loads(evidence[0])
            scope = [s for s in protocol["source_scope"]
                     if s["source_id"] == document["source_policy_id"] and s["family"] == family]
            if not scope:
                continue
            documents.append(document)
            scopes.extend(scope)
            ref = {"source_id": document["source_policy_id"], "native_id": document["native_id"],
                   "revision_id": document["revision_id"]}
            native_refs.append(ref)
            origin = _origin(store, ref, protocol, now=now)
            if not origin.get("origin_id"):
                reasons.append("publication_assertion_origin_unresolved")
                continue
            origins.add(origin["origin_id"])
            correspondence += origin.get("correspondence_receipt_ids", [])
        if not scopes:
            continue
        if len(origins) != 1:
            # A single emitted packet can never be credited as multiple units.
            reasons.append("publication_packet_requires_one_reviewed_assertion_origin")
            origin_id = "ambiguous:" + digest(sorted(origins))
        else:
            origin_id = next(iter(origins))
        source_units = [u for u in by_origin.get(origin_id, []) if u["family"] == family]
        if len(source_units) == 1:
            unit_id = source_units[0]["unit_id"]
        else:
            # Conservative parent grouping keeps multi-event ambiguity pending.
            unit_id = "unit:" + digest([origin_id, "whole_disclosure"])
            if len(source_units) > 1:
                reasons.append("publication_bundle_correspondence_ambiguous")
            if not source_units:
                reasons.append("publication_origin_absent_from_source_inventory")
        classes = sorted({s["source_class"] for s in scopes})
        if len(classes) != 1:
            reasons.append("publication_source_class_requires_reviewed_origin_mapping")
        proposal = {"event_id": row["event_id"], "event_revision": row["revision_id"],
                    "packet_hash": row["packet_hash"], "packet_observed_at": row["observed_at"],
                    "evidence_hash": digest(documents),
                    "native_refs": sorted(native_refs, key=digest),
                    "correspondence_receipt_ids": sorted(set(correspondence))}
        unit = groups.setdefault(unit_id, {"unit_id": unit_id, "origin_id": origin_id,
                    "families": [], "source_classes": [], "proposals": [], "native_refs": []})
        unit["families"].append(family)
        unit["source_classes"] += classes
        unit["proposals"].append(proposal)
        unit["native_refs"] += native_refs
    for unit in groups.values():
        unit["families"] = sorted(set(unit["families"]))
        unit["source_classes"] = sorted(set(unit["source_classes"]))
        unit["proposals"] = sorted(unit["proposals"], key=lambda r: (r["packet_observed_at"], r["packet_hash"]))
        unit["native_refs"] = sorted({digest(r): r for r in unit["native_refs"]}.values(), key=digest)
        unit["first_proposed_at"] = min((r["packet_observed_at"] for r in unit["proposals"]), key=instant)
        if len(unit["families"]) != 1 or len(unit["source_classes"]) != 1:
            reasons.append("publication_unit_stratum_ambiguous")
    return {"units": sorted(groups.values(), key=lambda r: r["unit_id"]),
            "reason_codes": sorted(set(reasons)),
            "denominator_rule": protocol["precision_stream_rule"],
            "approved_only_filter": False, "sampled_recall_filter": False,
            "all_emitted_packet_revisions_retained": True}


def closure_request(store, protocol_id, registry, policies, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    if instant(now) < instant(protocol["window_end"]):
        raise ValueError("publication observation window has not elapsed")
    source = _closed(store, protocol_id)
    if not source:
        raise ValueError("closed independent source inventory required")
    inventory = proposal_inventory(store, protocol, now=now)
    return {"contract": VERSION, "protocol_id": protocol_id,
            "source_inventory_receipt_id": source["receipt_id"],
            **inventory, "inventory_hash": digest(inventory["units"]),
            "evidence_kind": protocol["evidence_kind"], **_bindings(protocol)}


def close_inventory(store, protocol_id, registry, policies, review_receipt_id, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    previous = closed_inventory(store, protocol_id)
    if previous:
        if previous["payload"]["review_receipt_id"] != review_receipt_id:
            raise ValueError("publication census already closed with another review")
        return previous
    subject = closure_request(store, protocol_id, registry, policies, now=now)
    if subject["reason_codes"]:
        raise ValueError("publication census incomplete: " + ", ".join(subject["reason_codes"]))
    _review(store, review_receipt_id, purpose="prospective_publication_closure",
            subject=subject, protocol=protocol, now=now)
    with transaction(store):
        return _append(store, INVENTORY_KIND, dict(subject, review_receipt_id=review_receipt_id),
                       now=now, refs=[protocol_id, subject["source_inventory_receipt_id"], review_receipt_id])


def latest_adjudications(store, protocol_id, *, at=None):
    rows = _rows(store, protocol_id, ADJUDICATION_KIND, at=at)
    superseded = {r["payload"].get("supersedes") for r in rows}
    heads = [r for r in rows if r["receipt_id"] not in superseded]
    if len(heads) != len({r["payload"]["unit_id"] for r in heads}):
        raise ValueError("conflicting publication adjudication heads")
    return heads


def inspect_packet(store, protocol_id, registry, policies, record, *, now):
    """Inspect an exact historical proposal without substituting its new head."""
    protocol = protocol_state(store, protocol_id, registry, policies, require_current=False)
    closed = closed_inventory(store, protocol_id)
    if not closed:
        raise ValueError("closed publication census required")
    identity = {k: _text(record.get(k), k) for k in ("event_id", "event_revision", "packet_hash")}
    found = [p for u in closed["payload"]["units"] for p in u["proposals"]
             if all(p[k] == identity[k] for k in identity)]
    if len(found) != 1:
        raise ValueError("exact emitted packet is outside the closed publication census")
    access_receipt_id = None
    if protocol["partition"] == "untouched":
        from .holdout import record_access
        if not isinstance(record.get("access"), dict):
            raise ValueError("untouched packet inspection requires an explicit custody access log")
        access = record_access(store, protocol_id, registry, policies, record["access"], now=now)
        access_receipt_id = access["receipt_id"]
    row = store.conn.execute(
        "SELECT payload,observed_at FROM event_packets WHERE event_id=? AND revision_id=? AND packet_hash=?",
        (identity["event_id"], identity["event_revision"], identity["packet_hash"])).fetchone()
    if not row or digest(json.loads(row["payload"])) != identity["packet_hash"]:
        raise ValueError("immutable emitted packet is missing or damaged")
    event = json.loads(row["payload"])
    documents = []
    from .clinical_scope import dependency_references
    refs = sorted({(c["support"]["evidence_id"], c["support"]["revision_id"]) for c in event["claims"]}|dependency_references(event))
    for eid, revision in refs:
        evidence = store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?", (eid, revision)).fetchone()
        if not evidence:
            raise ValueError("historical proposal evidence missing")
        doc = json.loads(evidence[0])
        documents.append(doc)
    from .briefs import permitted_documents,permitted_claims
    synthetic=protocol["evidence_kind"]=="synthetic"
    visible,withheld=permitted_documents(store,documents,policies,now=now,allow_synthetic=synthetic)
    claims,claim_withheld=permitted_claims(store,event,documents,policies,now=now,allow_synthetic=synthetic)
    safe_event={k:event[k] for k in ("event_id","event_revision","family","scope_id")}
    safe_event.update(claims=claims,signal_eligible=False,ai_domain_labels="pending_human_review")
    return {"contract": VERSION, "protocol_id": protocol_id, **identity,
            "event": safe_event if claims else None, "documents": visible,
            "withheld": withheld+claim_withheld, "packet_observed_at": row["observed_at"],
            "access_receipt_id": access_receipt_id, "historical_proposal": True,
            "current_publication_approval": False, "automatic_activation": False}


def adjudication_request(store, protocol_id, registry, policies, decision, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    closed = closed_inventory(store, protocol_id)
    if not closed:
        raise ValueError("complete publication census required before adjudication")
    unit = next((u for u in closed["payload"]["units"] if u["unit_id"] == decision.get("unit_id")), None)
    if not unit:
        raise ValueError("publication unit outside closed census")
    fields = ("correct", "critical_error", "meaningfully_exercised", "unknown_issuer")
    if any(type(decision.get(k)) is not bool for k in fields):
        raise ValueError("publication scoring requires explicit boolean decisions")
    issuers = _unique(decision.get("issuer_ids", []), "publication issuer IDs", empty=True)
    if decision["unknown_issuer"] == bool(issuers):
        raise ValueError("publication issuer mapping is incomplete or contradictory")
    if any(i not in registry.entities or registry.entities[i]["kind"] != "issuer" for i in issuers):
        raise ValueError("publication issuer mapping unsupported by frozen registry")
    if any(instant(registry.entities[i]["known_from"]) > instant(protocol["frozen_at"]) for i in issuers):
        raise ValueError("publication issuer identity was unavailable at freeze")
    reviewed = _text(decision.get("reviewed_at"), "publication review clock")
    if not max(instant(r["packet_observed_at"]) for r in unit["proposals"]) <= instant(reviewed) <= instant(now):
        raise ValueError("publication adjudication must follow every retained proposal")
    previous = next((r for r in latest_adjudications(store, protocol_id)
                     if r["payload"]["unit_id"] == unit["unit_id"]), None)
    supersedes = decision.get("supersedes")
    if (previous and supersedes != previous["receipt_id"]) or (supersedes and not previous):
        raise ValueError("publication correction must supersede current adjudication")
    return {"contract": VERSION, "protocol_id": protocol_id, "unit_id": unit["unit_id"],
            "publication_inventory_receipt_id": closed["receipt_id"],
            "publication_inventory_hash": closed["payload"]["inventory_hash"],
            "proposal_set_hash": digest(unit["proposals"]),
            "every_material_claim_and_revision_scored": True,
            **{k: decision[k] for k in fields}, "issuer_ids": issuers,
            "reviewer": _text(decision.get("reviewer"), "publication reviewer", 120),
            "reviewed_at": reviewed, "supersedes": supersedes,
            "note": str(decision.get("note", ""))[:2000],
            "evidence_kind": protocol["evidence_kind"], **_bindings(protocol)}


def adjudicate(store, protocol_id, registry, policies, decision, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    for previous in _rows(store, protocol_id, ADJUDICATION_KIND):
        if previous["payload"].get("submitted_hash") == digest(decision):
            return previous
    subject = adjudication_request(store, protocol_id, registry, policies, decision, now=now)
    receipt = _review(store, decision["review_receipt_id"], purpose="prospective_publication_adjudication",
                      subject=subject, protocol=protocol, now=now)
    if receipt["payload"]["reviewer"] != subject["reviewer"] or receipt["payload"]["reviewed_at"] != subject["reviewed_at"]:
        raise ValueError("publication reviewer and clock disagree with attestation")
    refs = [protocol_id, subject["publication_inventory_receipt_id"], decision["review_receipt_id"]]
    if subject["supersedes"]:
        refs.append(subject["supersedes"])
    with transaction(store):
        if protocol["partition"] == "untouched":
            from .holdout import record_access
            record_access(store, protocol_id, registry, policies,
                          {"actor": subject["reviewer"], "at": subject["reviewed_at"],
                           "purpose": "blind_publication_adjudication",
                           "attestation_id": decision["review_receipt_id"],
                           "attested_subject": subject}, now=now)
        return _append(store, ADJUDICATION_KIND,
                       dict(subject, review_receipt_id=decision["review_receipt_id"], submitted_hash=digest(decision)),
                       now=now, refs=refs)


def verified_records(store, protocol_id, registry, policies, *, now):
    """Return a complete census audit and verified supplied labels."""
    protocol = protocol_state(store, protocol_id, registry, policies, require_current=False)
    closed = closed_inventory(store, protocol_id, at=now)
    reasons, valid, invalid = [], [], []
    if any(protocol.get(k) != v for k, v in current_bindings(registry, policies).items()):
        reasons.append("publication_implementation_registry_policy_or_locks_changed")
    units = closed["payload"]["units"] if closed else []
    by_id = {u["unit_id"]: u for u in units}
    if not closed:
        reasons.append("publication_census_not_closed")
    else:
        subject = {k: v for k, v in closed["payload"].items() if k != "review_receipt_id"}
        try:
            _review(store, closed["payload"]["review_receipt_id"], purpose="prospective_publication_closure",
                    subject=subject, protocol=protocol, now=now)
        except (ValueError, KeyError):
            reasons.append("publication_closure_review_stale_or_revoked")
        current = proposal_inventory(store, protocol, now=now)
        if digest(current["units"]) != closed["payload"]["inventory_hash"]:
            reasons.append("publication_census_changed")
        reasons += current["reason_codes"]
    for receipt in latest_adjudications(store, protocol_id, at=now):
        a = receipt["payload"]
        unit = by_id.get(a["unit_id"])
        issue = []
        subject = {k: v for k, v in a.items() if k not in ("review_receipt_id", "submitted_hash")}
        try:
            _review(store, a["review_receipt_id"], purpose="prospective_publication_adjudication",
                    subject=subject, protocol=protocol, now=now)
        except (ValueError, KeyError):
            issue.append("publication_adjudication_review_stale_or_revoked")
        if not unit or a["proposal_set_hash"] != digest(unit["proposals"]):
            issue.append("publication_adjudication_input_mismatch")
        if issue:
            invalid.append({"unit_id": a["unit_id"], "reason_codes": issue})
        else:
            valid.append((receipt, unit))
    pending = sorted(set(by_id) - {r[0]["payload"]["unit_id"] for r in valid})
    if pending:
        reasons.append("publication_adjudications_incomplete")
    if invalid:
        reasons.append("publication_adjudications_invalidated")
    report = {"closed": bool(closed), "expected": len(units), "adjudicated": len(valid),
              "pending": pending, "invalidated_adjudications": invalid,
              "emitted_packet_revisions": sum(len(u["proposals"]) for u in units),
              "reason_codes": sorted(set(reasons)),
              "denominator_rule": protocol["precision_stream_rule"],
              "sampled_recall_filter": False, "approved_only_filter": False}
    return report, valid
