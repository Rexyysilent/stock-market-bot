"""Prespecified untouched partitions with custody logs and one-use evaluation.

Sealing is a content/version commitment, not encryption or authentication. The
operator must attest external custody; software cannot prove who saw a database.
"""
from __future__ import annotations

from .identities import digest, instant
from .receipts import get_receipt, list_receipts, put_receipt
from .prospective import (
    VERSION, TRUST_BOUNDARY, _bindings, _closed, _inventory, _records, _review,
    _text, current_bindings, latest_adjudications, protocol_state, transaction)

SEAL_KIND = "prospective_untouched_seal"
ACCESS_KIND = "prospective_untouched_access"
CONSUMPTION_KIND = "prospective_untouched_consumption"


def _rows(store, protocol_id, kind, *, at=None):
    return [r for r in list_receipts(store, kind=kind)
            if r["payload"]["protocol_id"] == protocol_id
            and (at is None or instant(r["observed_at"]) <= instant(at))]


def _append(store, kind, payload, *, now, refs):
    receipt_id = "untouched:" + digest([kind, payload])
    try:
        previous = get_receipt(store, receipt_id, kind=kind)
    except (ValueError, KeyError):
        previous = None
    if previous:
        return previous
    return put_receipt(store, kind, payload, observed_at=now, refs=sorted(set(refs)),
                       receipt_id=receipt_id, manage_transaction=False)


def seal_request(store, protocol_id, registry, policies, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    if protocol["partition"] != "untouched":
        raise ValueError("only a prespecified untouched partition can be sealed")
    closed = _closed(store, protocol_id)
    if not closed:
        raise ValueError("source inventory must close before seal")
    from .publications import closed_inventory, latest_adjudications as publication_labels
    publication_census = closed_inventory(store, protocol_id)
    if not publication_census:
        raise ValueError("publication census must close before seal")
    if latest_adjudications(store, protocol_id):
        raise ValueError("partition already adjudicated before custody seal")
    if publication_labels(store, protocol_id):
        raise ValueError("publication partition already adjudicated before custody seal")
    if _rows(store, protocol_id, ACCESS_KIND):
        raise ValueError("partition already has access history before seal")
    return {"contract": VERSION, "protocol_id": protocol_id,
            "inventory_receipt_id": closed["receipt_id"],
            "inventory_hash": closed["payload"]["inventory_hash"],
            "publication_inventory_receipt_id": publication_census["receipt_id"],
            "publication_inventory_hash": publication_census["payload"]["inventory_hash"],
            "custodians": protocol["custodians"],
            "evidence_kind": protocol["evidence_kind"], "trust_boundary": TRUST_BOUNDARY,
            "seal_is_encryption": False, "external_custody_independently_proven": False,
            **_bindings(protocol)}


def seal_partition(store, protocol_id, registry, policies, review_receipt_id, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies)
    previous = _rows(store, protocol_id, SEAL_KIND)
    if previous:
        if previous[0]["payload"]["review_receipt_id"] != review_receipt_id:
            raise ValueError("partition already sealed with another custody receipt")
        return previous[0]
    subject = seal_request(store, protocol_id, registry, policies, now=now)
    review = _review(store, review_receipt_id, purpose="untouched_custody", subject=subject,
                     protocol=protocol, now=now)
    if review["payload"]["reviewer"] not in protocol["custodians"]:
        raise ValueError("seal reviewer must be a frozen custodian")
    with transaction(store):
        return _append(store, SEAL_KIND, dict(subject, sealed_at=now, review_receipt_id=review_receipt_id),
                       now=now, refs=[protocol_id, subject["inventory_receipt_id"],
                                      subject["publication_inventory_receipt_id"], review_receipt_id])


def _access_attestation(store, protocol, access, *, now):
    """Bind access to a validated exact subject in this partition."""
    purpose = {"blind_adjudication": "prospective_adjudication",
               "blind_publication_adjudication": "prospective_publication_adjudication",
               "controlled_evaluation": "untouched_evaluation",
               "custody_audit": "untouched_custody_audit"}.get(access["purpose"])
    subject = access.get("attested_subject")
    if not purpose or not isinstance(subject, dict) or subject.get("protocol_id") != protocol["protocol_id"]:
        raise ValueError("access needs the exact attested subject from this partition")
    closed = _closed(store, protocol["protocol_id"], at=now)
    if access["purpose"] == "blind_publication_adjudication":
        from .publications import closed_inventory
        publication_census = closed_inventory(store, protocol["protocol_id"], at=now)
        if not publication_census or subject.get("publication_inventory_hash") != publication_census["payload"]["inventory_hash"]:
            raise ValueError("publication access is not bound to sealed census")
    elif not closed or subject.get("inventory_hash") != closed["payload"]["inventory_hash"]:
        raise ValueError("access is not bound to sealed source inventory")
    attestation = _review(store, access["attestation_id"], purpose=purpose, subject=subject,
                          protocol=protocol, now=now)
    if (attestation["payload"]["reviewer"] != access["actor"]
            or instant(attestation["payload"]["reviewed_at"]) > instant(access["at"])):
        raise ValueError("custody actor/time do not match supplied attestation")
    return attestation


def record_access(store, protocol_id, registry, policies, access, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies, require_current=False)
    if protocol["partition"] != "untouched":
        raise ValueError("custody access log requires an untouched partition")
    actor = _text(access.get("actor"), "access actor", 120)
    at = access.get("at")
    if not instant(protocol["frozen_at"]) <= instant(at) <= instant(now):
        raise ValueError("access time outside recorded protocol lifetime")
    purpose = access.get("purpose")
    if purpose not in ("blind_adjudication", "blind_publication_adjudication", "controlled_evaluation", "custody_audit",
                       "development", "external_access"):
        raise ValueError("explicit custody access purpose required")
    seals = _rows(store, protocol_id, SEAL_KIND)
    reasons = []
    refs = [protocol_id]
    if not seals or instant(at) < instant(seals[0]["payload"]["sealed_at"]):
        reasons.append("access_before_seal")
    if actor not in protocol["custodians"]:
        reasons.append("access_actor_not_frozen_custodian")
    if purpose in ("development", "external_access"):
        reasons.append("partition_exposed_outside_controlled_evaluation")
    attestation_id = access.get("attestation_id")
    if not attestation_id:
        reasons.append("custody_access_attestation_missing")
    else:
        try:
            get_receipt(store, attestation_id, kind="operator_attestation")
            _access_attestation(store, protocol, access, now=now)
            refs.append(attestation_id)
        except (ValueError, KeyError):
            reasons.append("custody_access_attestation_unresolved")
    if any(protocol.get(k) != v for k, v in current_bindings(registry, policies).items()):
        reasons.append("access_after_implementation_or_policy_change")
    payload = {"contract": VERSION, "protocol_id": protocol_id, "actor": actor,
               "at": at, "purpose": purpose, "attestation_id": attestation_id,
               "attested_subject": access.get("attested_subject"),
               "contaminates": bool(reasons), "reason_codes": sorted(set(reasons)),
               "evidence_kind": protocol["evidence_kind"], **_bindings(protocol)}
    if seals:
        refs.append(seals[0]["receipt_id"])
    with transaction(store):
        return _append(store, ACCESS_KIND, payload, now=now, refs=refs)


def inventory_access(store, protocol_id, registry, policies, access, *, now):
    """An explicit content access is always logged; ordinary reports give counts."""
    receipt = record_access(store, protocol_id, registry, policies, access, now=now)
    closed = _closed(store, protocol_id)
    return {"access_receipt_id": receipt["receipt_id"],
            "contaminates": receipt["payload"]["contaminates"],
            "inventory": closed["payload"]["selected"] if closed else [],
            "trust_boundary": TRUST_BOUNDARY}


def untouched_state(store, protocol_id, registry, policies, *, now):
    protocol = protocol_state(store, protocol_id, registry, policies, require_current=False)
    if protocol["partition"] != "untouched":
        raise ValueError("not an untouched partition")
    reasons = []
    if any(r["payload"].get("protocol_id") == protocol_id
           and instant(r["observed_at"]) > instant(now) for r in list_receipts(store)):
        reasons.append("future_partition_receipts_present")
    seals = _rows(store, protocol_id, SEAL_KIND, at=now)
    closed = _closed(store, protocol_id, at=now)
    if not seals:
        reasons.append("partition_not_sealed")
    elif len(seals) != 1:
        reasons.append("ambiguous_partition_seal")
    else:
        seal = seals[0]["payload"]
        subject = {k: v for k, v in seal.items() if k not in ("review_receipt_id", "sealed_at")}
        try:
            _review(store, seal["review_receipt_id"], purpose="untouched_custody",
                    subject=subject, protocol=protocol, now=now)
        except ValueError:
            reasons.append("custody_attestation_stale_or_revoked")
        if not closed or seal["inventory_hash"] != closed["payload"]["inventory_hash"]:
            reasons.append("sealed_inventory_changed")
    if closed:
        current = _inventory(store, protocol, now=now)
        if digest(current["selected"]) != closed["payload"]["inventory_hash"]:
            reasons.append("origin_or_inventory_changed_after_seal")
    from .publications import closed_inventory, proposal_inventory
    publication_census = closed_inventory(store, protocol_id, at=now)
    if seals:
        seal = seals[0]["payload"]
        if not publication_census or seal["publication_inventory_hash"] != publication_census["payload"]["inventory_hash"]:
            reasons.append("sealed_publication_inventory_changed")
        elif digest(proposal_inventory(store, protocol, now=now)["units"]) != seal["publication_inventory_hash"]:
            reasons.append("publication_proposals_changed_after_seal")
    if any(protocol.get(k) != v for k, v in current_bindings(registry, policies).items()):
        reasons.append("implementation_registry_or_policy_changed")
    access = _rows(store, protocol_id, ACCESS_KIND, at=now)
    for receipt in access:
        if receipt["payload"]["contaminates"]:
            reasons.extend(receipt["payload"]["reason_codes"])
        attestation_id = receipt["payload"].get("attestation_id")
        if attestation_id:
            try:
                _access_attestation(store, protocol, receipt["payload"], now=now)
            except (ValueError, KeyError):
                reasons.append("custody_access_attestation_stale_or_revoked")
    consumptions = _rows(store, protocol_id, CONSUMPTION_KIND, at=now)
    return {"contract": VERSION, "protocol_id": protocol_id,
            "status": "contaminated_or_invalid" if reasons else "consumed" if consumptions else "sealed",
            "reason_codes": sorted(set(reasons)), "seal_receipt_id": seals[0]["receipt_id"] if len(seals) == 1 else None,
            "consumption_receipt_ids": [r["receipt_id"] for r in consumptions],
            "access_receipt_ids": [r["receipt_id"] for r in access],
            "access_log": [{k: v for k, v in r["payload"].items() if k != "attested_subject"} for r in access],
            "external_custody_independently_proven": False, "trust_boundary": TRUST_BOUNDARY,
            "automatic_activation": False}


def consumption_request(store, protocol_id, registry, policies, *, now):
    from .evaluation import wilson
    protocol = protocol_state(store, protocol_id, registry, policies)
    state = untouched_state(store, protocol_id, registry, policies, now=now)
    if state["reason_codes"]:
        raise ValueError("untouched partition is not clean: " + ", ".join(state["reason_codes"]))
    if state["consumption_receipt_ids"]:
        raise ValueError("untouched partition already consumed")
    closed = _closed(store, protocol_id)
    adjudications = latest_adjudications(store, protocol_id)
    expected = {u["unit_id"] for u in closed["payload"]["selected"]}
    actual = {r["payload"]["unit_id"] for r in adjudications}
    if expected != actual:
        raise ValueError("untouched inventory has missing adjudications")
    for receipt in adjudications:
        a = receipt["payload"]
        subject = {k: v for k, v in a.items() if k not in ("review_receipt_id", "submitted_hash")}
        _review(store, a["review_receipt_id"], purpose="prospective_adjudication",
                subject=subject, protocol=protocol, now=now)
    from .prospective import evaluation_report
    audit = evaluation_report(store, protocol_id, registry, policies, now=now)
    if audit["inventory"]["invalidated_adjudications"]:
        raise ValueError("untouched detection evidence changed or was invalidated")
    from .publications import verified_records, closed_inventory
    publication_audit, publication_labels = verified_records(store, protocol_id, registry, policies, now=now)
    if publication_audit["reason_codes"]:
        raise ValueError("untouched publication census incomplete: " + ", ".join(publication_audit["reason_codes"]))
    positives = [r["payload"] for r in adjudications if r["payload"]["event_present"]]
    proposed = [r[0]["payload"] for r in publication_labels]
    unknown = [r for r in positives if r["unknown_issuer"]]
    negative = [r for r in positives if r["negative_outcome"]]
    precision, recall = wilson(sum(r["correct"] for r in proposed), len(proposed)), wilson(sum(r["detected"] for r in positives), len(positives))
    performance_reasons = []
    if precision["n"] < 300 or precision["estimate"] is None or precision["estimate"] < .98 or precision["lower"] < .95:
        performance_reasons.append("untouched_precision_insufficient")
    if recall["n"] < 100 or recall["estimate"] is None or recall["estimate"] < .90:
        performance_reasons.append("untouched_recall_insufficient")
    if len(unknown) < protocol["minimum_unknown_issuer_gold"] or not unknown or sum(r["detected"] for r in unknown) / len(unknown) < .90:
        performance_reasons.append("untouched_unknown_issuer_recall_insufficient")
    if len(negative) < protocol["minimum_negative_outcome_gold"] or not negative or sum(r["detected"] for r in negative) / len(negative) < .90:
        performance_reasons.append("untouched_negative_outcome_recall_insufficient")
    strata = []
    for family, source_class in sorted({(s["family"], s["source_class"]) for s in protocol["source_scope"]}):
        labels = [r[0]["payload"] for r in publication_labels
                  if r[1]["families"] == [family] and r[1]["source_classes"] == [source_class]]
        stats = wilson(sum(r["correct"] for r in labels), len(labels))
        if stats["n"] < 100 or stats["estimate"] is None or stats["estimate"] < .98 or stats["lower"] < .95:
            performance_reasons.append("untouched_family_source_precision_insufficient")
        strata.append({"family": family, "source_class": source_class, "precision": stats})
    return {"contract": VERSION, "protocol_id": protocol_id, "seal_receipt_id": state["seal_receipt_id"],
            "inventory_receipt_id": closed["receipt_id"], "inventory_hash": closed["payload"]["inventory_hash"],
            "adjudication_receipt_ids": sorted(r["receipt_id"] for r in adjudications),
            "adjudication_set_hash": digest(sorted(r["receipt_id"] for r in adjudications)),
            "publication_inventory_receipt_id": closed_inventory(store, protocol_id)["receipt_id"],
            "publication_adjudication_receipt_ids": sorted(r[0]["receipt_id"] for r in publication_labels),
            "publication_adjudication_set_hash": digest(sorted(r[0]["receipt_id"] for r in publication_labels)),
            "access_receipt_ids": state["access_receipt_ids"],
            "event_unit_count": len(expected),
            "precision": precision, "recall": recall, "family_source_strata": strata,
            "unknown_issuer_recall": wilson(sum(r["detected"] for r in unknown), len(unknown)),
            "negative_outcome_recall": wilson(sum(r["detected"] for r in negative), len(negative)),
            "performance_reason_codes": sorted(set(performance_reasons)),
            "critical_errors": sum(r["payload"]["critical_error"] for r in adjudications) + sum(r["critical_error"] for r in proposed),
            "evidence_kind": protocol["evidence_kind"],
            "single_candidate_use": True, "automatic_activation": False,
            "trust_boundary": TRUST_BOUNDARY, **_bindings(protocol)}


def consume_partition(store, protocol_id, registry, policies, record, *, now):
    previous = _rows(store, protocol_id, CONSUMPTION_KIND)
    if previous:
        if previous[0]["payload"].get("review_receipt_id") == record.get("review_receipt_id"):
            return previous[0]
        raise ValueError("untouched test consumed; a new prespecified partition is required")
    protocol = protocol_state(store, protocol_id, registry, policies)
    subject = consumption_request(store, protocol_id, registry, policies, now=now)
    review = _review(store, record["review_receipt_id"], purpose="untouched_evaluation",
                     subject=subject, protocol=protocol, now=now)
    if review["payload"]["reviewer"] not in protocol["custodians"]:
        raise ValueError("controlled evaluation requires a frozen custodian")
    with transaction(store):
        access = record_access(store, protocol_id, registry, policies,
                               {"actor": review["payload"]["reviewer"], "purpose": "controlled_evaluation",
                                "at": now, "attestation_id": record["review_receipt_id"],
                                "attested_subject": subject}, now=now)
        refs = [protocol_id, subject["seal_receipt_id"], subject["inventory_receipt_id"],
                record["review_receipt_id"], access["receipt_id"]]
        refs += subject["adjudication_receipt_ids"] + subject["access_receipt_ids"]
        refs += [subject["publication_inventory_receipt_id"]] + subject["publication_adjudication_receipt_ids"]
        return _append(store, CONSUMPTION_KIND,
                       dict(subject, review_receipt_id=record["review_receipt_id"],
                            evaluation_access_receipt_id=access["receipt_id"], consumed_at=now),
                       now=now, refs=refs)


def verify_consumption(store, receipt_id, registry, policies, *, now):
    receipt = get_receipt(store, receipt_id, kind=CONSUMPTION_KIND)
    if instant(receipt["observed_at"]) > instant(now):
        raise ValueError("untouched consumption was imported after the report clock")
    payload = receipt["payload"]
    protocol = protocol_state(store, payload["protocol_id"], registry, policies)
    state = untouched_state(store, protocol["protocol_id"], registry, policies, now=now)
    if state["reason_codes"] or state["consumption_receipt_ids"] != [receipt_id]:
        raise ValueError("untouched receipt contaminated, invalid or multiply consumed")
    if protocol["evidence_kind"] != "operator_evidence":
        raise ValueError("synthetic untouched evaluation supplies no real release evidence")
    subject = {k: v for k, v in payload.items()
               if k not in ("review_receipt_id", "evaluation_access_receipt_id", "consumed_at")}
    _review(store, payload["review_receipt_id"], purpose="untouched_evaluation",
            subject=subject, protocol=protocol, now=now)
    current_ids = sorted(r["receipt_id"] for r in latest_adjudications(store, protocol["protocol_id"], at=now))
    if digest(current_ids) != payload["adjudication_set_hash"]:
        raise ValueError("untouched labels changed after evaluation")
    from .publications import verified_records
    publication_audit, labels = verified_records(store, protocol["protocol_id"], registry, policies, now=now)
    if (publication_audit["reason_codes"] or digest(sorted(r[0]["receipt_id"] for r in labels))
            != payload["publication_adjudication_set_hash"]):
        raise ValueError("untouched publication labels changed or invalidated")
    if payload["critical_errors"]:
        raise ValueError("untouched evaluation contains critical errors")
    if payload["performance_reason_codes"]:
        raise ValueError("untouched performance evidence is insufficient")
    return receipt
