"""Persistent source changes, exact-state review and reviewed origin correspondence."""
from __future__ import annotations

import hashlib
import json
import re

from .evidence import check_span
from .identities import ROOT, digest, instant
from .receipts import get_receipt, list_receipts, put_receipt, transaction

VERSION = "omni-source-lifecycle-1"
MATERIAL_METADATA = ("title", "url", "form", "cik", "submission_url", "filed_date",
                     "registrants", "published", "publication_date")
RELATIONS = {"same_release", "republishes", "independently_reports", "corrects", "retracts"}


def implementation_hash():
    files = sorted((ROOT / "omni").rglob("*.py")) + [ROOT / "source_request_gate.py", ROOT / "editorial_focus.py"]
    return digest({p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def reference(doc):
    return {"source_id": doc["source_policy_id"], "native_id": doc["native_id"], "revision_id": doc["revision_id"]}


def reference_key(ref):
    if not isinstance(ref, dict) or set(ref) != {"source_id", "native_id", "revision_id"}:
        raise ValueError("exact source/native/revision reference required")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 600 for v in ref.values()):
        raise ValueError("invalid source reference")
    return digest(ref)


def resolve_reference(store, ref):
    reference_key(ref)
    eid = "evidence:" + digest([ref["source_id"], ref["native_id"]])
    row = store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?", (eid, ref["revision_id"])).fetchone()
    if row:
        return {"kind": "document", "document": json.loads(row[0]), "reference": ref}
    # Source-first inventories can precede body acquisition/classification.
    for receipt in list_receipts(store, kind="native_inventory_observation"):
        payload = receipt["payload"]
        if ref in payload.get("native_inventory", []):
            return {"kind": "inventory", "receipt_id": receipt["receipt_id"], "reference": ref}
    raise ValueError("source revision is not in retained evidence or a native inventory")


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _affected(store, source_id, native_id):
    result = {}
    rows = store.conn.execute(
        "SELECT DISTINCT e.event_id,e.revision_id,e.payload,d.payload FROM events e "
        "JOIN event_evidence x ON e.event_id=x.event_id AND e.revision_id=x.revision_id "
        "JOIN evidence d ON d.evidence_id=x.evidence_id AND d.revision_id=x.evidence_revision")
    for row in rows:
        event, doc = json.loads(row[2]), json.loads(row[3])
        if doc["source_policy_id"] == source_id and (doc["native_id"] == native_id or doc["native_id"].startswith(native_id + "/")):
            key = (row[0], row[1])
            claims=set(result.get(key,{}).get("claim_ids",[]))
            for claim in event["claims"]:
                definition_sources=((claim.get("numeric_result") or {}).get("scope_binding") or {}).get("evidence_sources",[])
                if claim["support"]["evidence_id"] == doc["evidence_id"] or any(s["evidence_id"]==doc["evidence_id"] for s in definition_sources):
                    claims.add(claim["claim_id"])
            result[key] = {"event_id": row[0], "event_revision": row[1], "claim_ids": sorted(claims)}
    return list(result.values())


def _link(store, envelope, impacts):
    for impact in impacts:
        store.conn.execute("INSERT OR IGNORE INTO event_change_links VALUES(?,?)", (impact["event_id"], envelope["receipt_id"]))
        if envelope["payload"].get("invalidates_review"):
            # Queue state is only an operator convenience; the receipt remains
            # the gate even if a hydration worker changes acquisition status.
            store.conn.execute("UPDATE outbox SET state='pending',acknowledged_at=NULL WHERE event_id=?", (impact["event_id"],))


def observe_change(store, *, source_id, native_id, old, new, kind, observed_at,
                   policy_hash=None, proof_refs=(), invalidates_review=True, manage_transaction=True):
    if kind not in {"metadata_enrichment", "discrepancy", "substantive_correction", "amendment",
                    "confirmed_withdrawal", "stale_replay"}:
        raise ValueError("unknown source change kind")
    if kind in {"amendment", "confirmed_withdrawal"} and not proof_refs:
        raise ValueError("authoritative reviewed correspondence required")
    instant(observed_at)
    with transaction(store, manage_transaction=manage_transaction):
        impacts = _affected(store, source_id, native_id)
        payload = {"contract": VERSION, "source_id": source_id, "native_id": native_id, "kind": kind,
                   "old": old, "new": new, "old_hash": digest(old), "new_hash": digest(new),
                   "old_evidence_revision": (old or {}).get("revision_id"),
                   "new_evidence_revision": (new or {}).get("revision_id"),
                   "old_metadata_hash": (old or {}).get("metadata_hash", digest(old)), "new_metadata_hash": (new or {}).get("metadata_hash", digest(new)),
                   "policy_hash": policy_hash, "implementation_hash": implementation_hash(),
                   "affected": sorted(impacts, key=lambda r: (r["event_id"], r["event_revision"])),
                   "invalidates_review": bool(invalidates_review), "human_review_status": "pending",
                   "historical_availability_reconstructed": False}
        envelope = put_receipt(store, "source_change", payload, observed_at=observed_at, refs=list(proof_refs), manage_transaction=False)
        _link(store, envelope, impacts)
    return envelope


def observe_candidate(store, source_id, row, *, observed_at, policy_hash=None):
    """Called inside the acquisition transaction; hydration state is excluded."""
    native = row["native_id"]
    metadata = {k: row[k] for k in MATERIAL_METADATA if k in row and row[k] is not None}
    old_row = store.conn.execute("SELECT payload,observed_at FROM source_heads WHERE source_id=? AND native_id=?", (source_id, native)).fetchone()
    old = json.loads(old_row[0]) if old_row else None
    current = {"metadata": metadata, "metadata_hash": digest(metadata)}
    if old_row and instant(observed_at) < instant(old_row[1]):
        if current != old:
            observe_change(store, source_id=source_id, native_id=native, old=old, new=current, kind="stale_replay",
                           observed_at=observed_at, policy_hash=policy_hash, invalidates_review=False, manage_transaction=False)
        return False
    if old and old != current:
        prior = old["metadata"]
        changed = [k for k in set(prior) & set(metadata) if prior[k] != metadata[k]]
        removed = set(prior) - set(metadata)
        kind = "discrepancy" if changed or removed else "metadata_enrichment"
        observe_change(store, source_id=source_id, native_id=native, old=old, new=current, kind=kind,
                       observed_at=observed_at, policy_hash=policy_hash, invalidates_review=kind == "discrepancy", manage_transaction=False)
    store.conn.execute("INSERT INTO source_heads VALUES(?,?,?,?) ON CONFLICT(source_id,native_id) "
                       "DO UPDATE SET observed_at=excluded.observed_at,payload=excluded.payload",
                       (source_id, native, observed_at, _encode(current)))
    return True


def _document_state(doc):
    metadata = {k: doc[k] for k in ("url", "title", "publication", "source_policy_fingerprint", "reporting_party", "source_class")}
    return {"reference": reference(doc), "revision_id": doc["revision_id"], "content_hash": doc["content_hash"],
            "metadata": metadata, "metadata_hash": digest(metadata)}


def observe_document(store, doc, *, observed_at):
    row = store.conn.execute("SELECT revision_id,observed_at FROM evidence_heads WHERE evidence_id=?", (doc["evidence_id"],)).fetchone()
    if row:
        old_row = store.conn.execute("SELECT payload FROM evidence WHERE evidence_id=? AND revision_id=?", (doc["evidence_id"], row[0])).fetchone()
        old = json.loads(old_row[0]) if old_row else None
        if instant(doc["retrieved_at"]) < instant(row[1]):
            return False
        if old and _document_state(old) != _document_state(doc):
            observe_change(store, source_id=doc["source_policy_id"], native_id=doc["native_id"],
                           old=_document_state(old), new=_document_state(doc),
                           kind="substantive_correction" if old["content_hash"] != doc["content_hash"] else "discrepancy",
                           observed_at=observed_at, policy_hash=doc["source_policy_fingerprint"], manage_transaction=False)
    store.conn.execute("INSERT INTO evidence_heads VALUES(?,?,?) ON CONFLICT(evidence_id) "
                       "DO UPDATE SET revision_id=excluded.revision_id,observed_at=excluded.observed_at",
                       (doc["evidence_id"], doc["revision_id"], doc["retrieved_at"]))
    return True


def link_late_events(store, events, *, observed_at):
    """New events and resumed batches inherit already-observed source changes."""
    for receipt in list_receipts(store, kind="source_change"):
        p = receipt["payload"]
        impacts = _affected(store, p["source_id"], p["native_id"])
        event_ids = {e["event_id"] for e in events}
        _link(store, receipt, [x for x in impacts if x["event_id"] in event_ids])
    for conflict in current_conflicts(store):
        existing = next((r for r in list_receipts(store, kind="claim_conflict")
                         if r["payload"]["conflict_hash"] == digest(conflict)), None)
        if existing is None:
            existing = put_receipt(store, "claim_conflict",
                {"contract": VERSION, "kind": "cross_source_conflict", "conflict_hash": digest(conflict),
                 "conflict": conflict, "invalidates_review": True, "implementation_hash": implementation_hash(),
                 "human_review_status": "pending"}, observed_at=observed_at, manage_transaction=False)
        _link(store, existing, [{"event_id": e["event_id"]} for e in conflict["events"]])


def current_conflicts(store):
    groups = {}
    rows = store.conn.execute("SELECT e.event_id,e.revision_id,COALESCE(p.payload,e.payload) FROM event_heads h "
        "JOIN events e ON h.event_id=e.event_id AND h.revision_id=e.revision_id "
        "LEFT JOIN event_packet_heads ph ON ph.event_id=e.event_id AND ph.revision_id=e.revision_id "
        "LEFT JOIN event_packets p ON p.event_id=ph.event_id AND p.revision_id=ph.revision_id AND p.packet_hash=ph.packet_hash")
    for row in rows:
        event = json.loads(row[2])
        if event.get("family") != "clinical_result" or not event.get("scope_id"):
            continue
        for claim in event["claims"]:
            if claim["predicate"] == "primary_endpoint_status" and not claim.get("historical") and not claim.get("future"):
                groups.setdefault(event["scope_id"], []).append({"event_id": row[0], "event_revision": row[1],
                    "value": claim["value"], "claim_id": claim["claim_id"], "support": claim["support"]})
    return [{"scope_id": scope, "events": sorted(items, key=lambda r: (r["event_id"], r["claim_id"]))}
            for scope, items in sorted(groups.items()) if len({r["value"] for r in items}) > 1]


def lifecycle_state(store, event, documents):
    heads, source_states, reasons = [], [], set()
    head = store.conn.execute("SELECT revision_id,changed_at FROM event_heads WHERE event_id=?", (event["event_id"],)).fetchone()
    if not head or head[0] != event["event_revision"]:
        reasons.add("superseded_event_revision")
    for doc in documents:
        current = store.conn.execute("SELECT revision_id,observed_at FROM evidence_heads WHERE evidence_id=?", (doc["evidence_id"],)).fetchone()
        heads.append({"evidence_id": doc["evidence_id"], "revision": current[0] if current else None})
        if not current or current[0] != doc["revision_id"]:
            reasons.add("source_revision_superseded")
        for source in store.conn.execute("SELECT native_id,payload,observed_at FROM source_heads WHERE source_id=?", (doc["source_policy_id"],)):
            if doc["native_id"] == source[0] or doc["native_id"].startswith(source[0] + "/"):
                source_states.append({"source_id": doc["source_policy_id"], "native_id": source[0], "state": json.loads(source[1])})
                metadata = json.loads(source[1])["metadata"]
                if doc["native_id"] == source[0] and metadata.get("url") and metadata["url"] != doc["url"]:
                    reasons.add("source_route_changed")
    linked = [get_receipt(store, r[0]) for r in store.conn.execute("SELECT receipt_id FROM event_change_links WHERE event_id=? ORDER BY receipt_id", (event["event_id"],))]
    active_changes = [r["receipt_id"] for r in linked if r["payload"].get("invalidates_review")]
    for receipt in linked:
        p = receipt["payload"]
        if p["kind"] == "confirmed_withdrawal" and any(d["revision_id"] == p.get("old_evidence_revision") for d in documents):
            reasons.add("source_withdrawn")
        if p["kind"] == "discrepancy" and p.get("invalidates_review") and (p.get("new") or {}).get("metadata"):
            for doc in documents:
                if doc["source_policy_id"] != p["source_id"] or not (doc["native_id"] == p["native_id"] or doc["native_id"].startswith(p["native_id"] + "/")):
                    continue
                verified = store.conn.execute("SELECT observed_at FROM evidence_heads WHERE evidence_id=?", (doc["evidence_id"],)).fetchone()
                if not verified or instant(verified[0]) < instant(receipt["observed_at"]):
                    reasons.add("source_metadata_reverification_required")
    if any(any(e["event_id"] == event["event_id"] for e in c["events"]) for c in current_conflicts(store)):
        reasons.add("cross_source_outcome_conflict")
    state = {"contract": VERSION, "event_id": event["event_id"], "event_revision": event["event_revision"],
             "head": list(head) if head else None, "evidence_heads": sorted(heads, key=lambda x: x["evidence_id"]),
             "source_states": sorted(source_states, key=lambda x: (x["source_id"], x["native_id"])),
             "change_refs": sorted(active_changes), "implementation_hash": implementation_hash(),
             "hard_reason_codes": sorted(reasons), "quarantined_pending_review": bool(active_changes)}
    state["state_hash"] = digest(state)
    return state


def validate_current(store, packet, registry, policies, *, now, review=None):
    from .validator import validate
    state = lifecycle_state(store, packet["event"], packet["documents"]+packet.get("scope_documents",[]))
    current_binding = digest({"lifecycle_hash": state["state_hash"], "policy_hash": digest(policies),
                              "registry_hash": registry.fingerprint()})
    usable = review if review and review.get("current_state_hash") == current_binding else None
    result = validate(packet["event"], packet["documents"], registry, policies, now=now, review=usable)
    reasons = set(result["reason_codes"]) | set(state["hard_reason_codes"])
    if state["change_refs"] and not usable:
        reasons.add("source_change_quarantine")
    if review and not usable:
        reasons.add("current_state_review_required")
    if reasons:
        result["editorial_status"] = "suppressed" if result["editorial_status"] == "suppressed" else "review_required"
        result["reviewer"] = result["reviewed_at"] = None
    origins = [resolve_origin(store, reference(d), at=now, policy_hash=digest(policies),
        allow_synthetic=all(x.get("evidence_kind") == "synthetic" for x in packet["documents"])) for d in packet["documents"]]
    result.update(reason_codes=sorted(reasons), current_state_hash=current_binding, lifecycle=state,
                  assertion_origin_units=len({x["origin_id"] for x in origins}),
                  independent_origins=None, independence_assessed=False, origin_resolution=origins)
    return result


def validate_correspondence(store, record, *, now, allow_synthetic=False):
    """Validate retained proof on both writes and reads, including generic imports."""
    from .attestations import validate_attestation
    if record.get("relation") not in RELATIONS:
        raise ValueError("unknown correspondence relation")
    source, target = record.get("from"), record.get("to")
    left, right = resolve_reference(store, source), resolve_reference(store, target)
    if reference_key(source) == reference_key(target):
        raise ValueError("self correspondence is not an independent observation")
    if left["kind"] != "document" or right["kind"] != "document":
        raise ValueError("source-supported correspondence needs retained document proofs")
    docs = {d["document"]["evidence_id"]: d["document"] for d in (left, right)}
    proofs = record.get("proofs", [])
    if not proofs or not all(isinstance(p, dict) and p.get("evidence_id") in docs and check_span(docs[p["evidence_id"]], p) for p in proofs):
        raise ValueError("exact correspondence proof spans required")
    text = " ".join(p["text"] for p in proofs)
    source_text = " ".join(p["text"] for p in proofs if p["evidence_id"] == left["document"]["evidence_id"])
    verbs = {"same_release": r"same (?:release|announcement)|original release|reproduc",
             "republishes": r"republish|reprint|originally publish|original release|reproduc",
             "independently_reports": r"independently report",
             "corrects": r"corrects|correction|amends|supersedes",
             "retracts": r"withdraws|withdrawal|retracts|retraction"}
    urls = {v.rstrip(".,;)]}") for v in re.findall(r"https://[^\s<>\"']+", source_text)}
    explicit_native = re.search(r"\bnative_id\s*[:=]\s*" + re.escape(target["native_id"]) + r"(?=$|[\s.,;)])", source_text)
    if (not (right["document"]["url"] in urls or explicit_native)
            or not re.search(verbs[record["relation"]], source_text, re.I)
            or re.search(r"\b(?:not|never|unrelated)\b", text, re.I)):
        raise ValueError("proof does not affirm exact native correspondence")
    if record["relation"] in {"corrects", "retracts"} and (
            left["document"]["reporting_party"] != right["document"]["reporting_party"]):
        raise ValueError("source cannot withdraw or amend another reporting party's assertion")
    body = {k: v for k, v in record.items() if k != "attestation_ref"}
    bindings = {"implementation_hash": implementation_hash(), "policy_hash": record.get("policy_hash")}
    if record.get("implementation_hash") != bindings["implementation_hash"]:
        raise ValueError("correspondence implementation mismatch")
    attestation = validate_attestation(store, record.get("attestation_ref"), purpose="origin_correspondence",
        subject_hash=digest(body), bindings=bindings, now=now, allow_synthetic=allow_synthetic)
    if instant(attestation["payload"]["reviewed_at"]) < max(instant(d["document"]["retrieved_at"]) for d in (left, right)):
        raise ValueError("correspondence review predates source observation")
    return left,right


def record_correspondence(store, record, *, now, allow_synthetic=False):
    """An explicit reviewed relation; titles/tickers/dates never establish a link."""
    left,right=validate_correspondence(store,record,now=now,allow_synthetic=allow_synthetic)
    target=record["to"]
    with transaction(store):
        receipt = put_receipt(store, "origin_correspondence", record, observed_at=now,
                              refs=[record["attestation_ref"]], manage_transaction=False)
        if record["relation"] in {"corrects", "retracts"}:
            observe_change(store, source_id=target["source_id"], native_id=target["native_id"],
                           old=_document_state(right["document"]), new=_document_state(left["document"]),
                           kind="confirmed_withdrawal" if record["relation"] == "retracts" else "amendment",
                           observed_at=now, proof_refs=[receipt["receipt_id"]], policy_hash=record["policy_hash"],
                           manage_transaction=False)
    return receipt


def resolve_origin(store, ref, *, at=None, policy_hash=None, allow_synthetic=False):
    resolve_reference(store, ref)
    native_key = lambda r: digest([r["source_id"], r["native_id"]])
    groups, members, used = {native_key(ref)}, {reference_key(ref): ref}, []
    links = list_receipts(store, kind="origin_correspondence")
    changed = True
    while changed:
        changed = False
        for receipt in links:
            p = receipt["payload"]
            if p.get("relation") not in {"same_release", "republishes"} or (at and instant(receipt["observed_at"]) > instant(at)):
                continue
            try:
                if p.get("implementation_hash") != implementation_hash() or (policy_hash and p.get("policy_hash") != policy_hash):
                    continue
                validate_correspondence(store,p,now=at or receipt["observed_at"],allow_synthetic=allow_synthetic)
            except (ValueError, KeyError):
                continue
            a, b = native_key(p["from"]), native_key(p["to"])
            if a in groups or b in groups:
                if a not in groups or b not in groups:
                    changed = True
                groups.update((a, b))
                members[reference_key(p["from"])] = p["from"]
                members[reference_key(p["to"])] = p["to"]
                used.append(receipt["receipt_id"])
    return {"origin_id": "assertion-origin:" + min(groups), "member_refs": [members[k] for k in sorted(members)],
            "correspondence_receipt_ids": sorted(set(used)), "reviewed": True,
            "basis": "reviewed_correspondence" if used else "exact_native_reference",
            "independence_assessed": False, "reason_codes": []}
