"""Version-bound OMNI-10/11/12 evidence gates. Readiness never activates anything."""
from __future__ import annotations

from .attestations import validate_attestation
from .identities import digest, instant
from .lifecycle import implementation_hash
from .receipts import get_receipt, put_receipt

REQUIRED = {
    "OMNI-10": ("prospective_evaluation_receipt", "stable_architecture_receipt", "reviewed_user_value_receipt", "vertical_source_policies"),
    "OMNI-11": ("expansion_report_receipt",),
    "OMNI-12": ("reviewed_useful_brief_receipts", "field_level_rights_receipt", "counsel_review_receipt",
                "customer_feedback_receipt", "operating_cost_receipt"),
}


def context_request(store,protocol_id,registry,policies,current_coverage,candidates,*,now,operating_window=None):
    """The exact scope for later operating observations and human gate reviews."""
    from .prospective import protocol_state
    protocol=protocol_state(store,protocol_id,registry,policies)
    coverage=list(dict.fromkeys(current_coverage)); candidates=list(dict.fromkeys(candidates))
    if protocol["coverage_fingerprint"]!=digest(coverage) or protocol["captured_coverage"]!=coverage:
        raise ValueError("prospective coverage baseline differs from current coverage")
    if instant(protocol["frozen_at"])>instant(now): raise ValueError("future prospective freeze")
    window=operating_window if operating_window is not None else {"start":protocol["window_start"],"end":protocol["window_end"]}
    if (not isinstance(window,dict) or set(window)!={"start","end"}
            or not instant(protocol["frozen_at"])<=instant(window["start"])<=instant(protocol["window_start"])
            or not instant(protocol["window_end"])<=instant(window["end"])):
        raise ValueError("operating window must explicitly contain the prospective window and start after freeze")
    scope={"protocol_id":protocol_id,"source_scope":protocol["source_scope"],
        "window_start":protocol["window_start"],"window_end":protocol["window_end"],
        "coverage_fingerprint":protocol["coverage_fingerprint"],"operating_window":window}
    return {"scope_hash":digest(scope),"protocol_id":protocol_id,"implementation_hash":implementation_hash(),
        "policy_hash":digest(policies),"registry_hash":registry.fingerprint(),
        "coverage_hash":digest(coverage),"candidate_hash":digest(candidates),"operating_window":window}


def dependent_gates(evidence, current_coverage, *, candidates=(), store=None, registry=None, policies=None, now=None):
    if not isinstance(evidence,dict) or not isinstance(candidates,(list,tuple)) or any(not isinstance(c,str) or not c.strip() for c in candidates):
        raise ValueError("explicit evidence dictionary and candidate list required")
    candidates=list(dict.fromkeys(candidates)); coverage=list(dict.fromkeys(current_coverage))
    result={key:{"status":"pending","missing":list(needs),"invalid":{},"validated_refs":[]} for key,needs in REQUIRED.items()}
    result.update(wave_b_candidates=[c for c in candidates if c not in coverage],automatic_membership_change=False,
                  automatic_activation=False,human_review_status="pending",authentication="records_not_human_or_legal_authenticity")
    if store is None or registry is None or policies is None or now is None:
        for key in REQUIRED:
            result[key]["invalid"]={name:"typed store/current version context required" for name in REQUIRED[key] if evidence.get(name)}
        return result
    try:
        context=context_request(store,evidence.get("protocol_id"),registry,policies,coverage,candidates,now=now,operating_window=evidence.get("operating_window"))
        if evidence.get("scope_hash")!=context["scope_hash"]:
            raise ValueError("scope hash must bind the exact prospective protocol, source scope and window")
    except (ValueError,KeyError,TypeError) as exc:
        for key in REQUIRED: result[key]["invalid"]["scope_hash"]=str(exc)
        return result
    def attest(ref,purpose,subject):
        return validate_attestation(store,ref,purpose=purpose,subject_hash=digest(subject),
            bindings={k:context[k] for k in ("scope_hash","implementation_hash","policy_hash")},now=now)
    def economic(ref):
        from .operating import economics_report
        env=get_receipt(store,ref,kind="operating_economics"); p=env["payload"]
        if instant(env["observed_at"])>instant(now): raise ValueError("future operating economics receipt")
        if p.get("scope_hash")!=context["scope_hash"]: raise ValueError("economics scope mismatch")
        if (p.get("window_start"),p.get("window_end"))!=(context["operating_window"]["start"],context["operating_window"]["end"]):
            raise ValueError("economics reporting window differs from the reviewed operating window")
        current=economics_report(store,scope_hash=p["scope_hash"],window_start=p["window_start"],window_end=p["window_end"],
            policy_hash=context["policy_hash"],now=now,registry=registry,policies=policies)
        for key in ("record_refs","direct_cost","useful_event_units","cost_per_useful_event","feedback_results","implementation_hash","policy_hash"):
            if current[key]!=p.get(key): raise ValueError("economics report stale or derivation mismatch")
        if current["pending_reasons"] or not current["measured_actuals"] or current["evidence_kind"]!="operator_evidence":
            raise ValueError("actual complete operating economics pending")
        return current
    def validate(name,value):
        if name=="prospective_evaluation_receipt":
            from .prospective import verify_evaluation_receipt
            result=verify_evaluation_receipt(store,value,registry,policies,now=now,require_real=True)
            if result["payload"]["protocol_id"]!=context["protocol_id"]:
                raise ValueError("prospective evaluation protocol differs from gate scope")
            report=result["current_report"]
            families=report.get("family_source_gates",[])
            if isinstance(families,dict): families=list(families.values())
            if (any(report.get(k,{}).get("status")!="eligible_for_review" for k in ("manual_pilot","recall","timing"))
                    or not families or any(g.get("status")!="eligible_for_activation_review" for g in families)
                    or report.get("pending_reason_codes") or report.get("evidence_verification",{}).get("real_prospective") is not True
                    or report.get("automatic_activation") is not False):
                raise ValueError("prospective release thresholds or untouched source-family evidence pending")
            return [value]
        if name=="stable_architecture_receipt":
            return [attest(value,"omni_architecture_review",context)["receipt_id"]]
        if name=="reviewed_user_value_receipt":
            env=get_receipt(store,value,kind="operator_attestation"); p=env["payload"]
            report_ref=p.get("operating_report_ref")
            report=economic(report_ref)
            if not report["useful_event_count"] or not report["feedback_records"]:
                raise ValueError("observed useful events and feedback required")
            return [attest(value,"omni_user_value_review",{**context,"operating_report_ref":report_ref})["receipt_id"],report_ref]
        if name=="vertical_source_policies":
            if not isinstance(value,list) or not value or len(value)!=len(set(value)):
                raise ValueError("explicit unique vertical source grants required")
            from .transport import CONTRACTS
            verified=[]; required=set(evidence.get("vertical_contract_ids",[]))
            if not required or not required.issubset(CONTRACTS): raise ValueError("explicit verified vertical contract scope required")
            seen=set()
            for ref in value:
                env=get_receipt(store,ref,kind="operator_attestation"); p=env["payload"]
                matches=[]
                for policy in policies.values():
                    for grant in policy.get("read_only_requests",[]):
                        if grant.get("approval_ref")==ref: matches.append((policy,grant))
                if len(matches)!=1: raise ValueError("transport grant absent or ambiguous in current policies")
                policy,grant=matches[0]
                body={k:v for k,v in grant.items() if k!="approval_ref"}
                validate_attestation(store,ref,purpose="vertical_transport_contract",subject_hash=digest(body),
                    bindings={"source_policy_id":policy["policy_id"],"source_policy_fingerprint":digest({k:v for k,v in policy.items() if k!="read_only_requests"})},now=now)
                contract=CONTRACTS.get(grant.get("contract_id"))
                if (not contract or grant.get("contract_hash")!=digest(contract) or grant.get("method")!=contract["method"]
                        or grant.get("url")!=contract["url"]): raise ValueError("vertical contract method/route/hash changed")
                if any(policy.get("uses",{}).get(purpose) is not True for purpose in ("acquire","store_text","local_analysis","review_display")):
                    raise ValueError("vertical source purposes remain pending")
                seen.add(grant["contract_id"]); verified.append(ref)
            if seen!=required: raise ValueError("vertical grant inventory differs from frozen scope")
            return verified
        if name=="expansion_report_receipt":
            from .expansion import validate_report
            report=validate_report(store,value,registry,policies,current_coverage=coverage,now=now)
            p=report.get("payload",report)
            proposal=get_receipt(store,p["proposal_id"],kind="expansion_proposal")["payload"]
            if (proposal["protocol_id"]!=context["protocol_id"] or p.get("candidate_symbols")!=sorted(set(candidates)-set(coverage))):
                raise ValueError("expansion protocol/candidate scope differs from requested gate scope")
            if p.get("evidence_kind")!="operator_evidence" or p.get("pending_reasons") or p.get("automatic_membership_change") is not False:
                raise ValueError("actual complete measured expansion review pending")
            return [value]
        if name=="reviewed_useful_brief_receipts":
            from .briefs import validate_brief
            if not isinstance(value,list) or not value or len(value)!=len(set(value)):
                raise ValueError("unique actual reviewed brief references required")
            operating=economic(evidence.get("operating_cost_receipt"))
            assessments=[get_receipt(store,ref,kind="operating_observation")["payload"] for ref in operating["record_refs"]]
            useful_briefs={row["brief_ref"] for row in assessments if row["kind"]=="useful_brief" and row["useful"] is True}
            if not set(value).issubset(useful_briefs):
                raise ValueError("briefs lack positive useful assessments in the aligned operating report")
            for ref in value:
                p=validate_brief(store,ref,registry,policies,now=now)["payload"]
                if p["scope_hash"]!=context["scope_hash"] or not p["cards"] or p["evidence_kind"]!="operator_evidence":
                    raise ValueError("actual reviewed brief scope/content pending")
            return value
        if name=="field_level_rights_receipt":
            from .rights import validate_rights
            if not isinstance(value,list) or not value or len(value)!=len(set(value)):
                raise ValueError("exact rights inventory required")
            briefs=evidence.get("reviewed_useful_brief_receipts",[])
            used={r for ref in briefs for r in get_receipt(store,ref,kind="reviewed_brief")["payload"]["rights_refs"]}
            if set(value)!=used: raise ValueError("rights inventory does not exactly cover supplied briefs")
            for ref in value: validate_rights(store,ref,policies,now=now)
            return value
        if name=="counsel_review_receipt":
            rights=evidence.get("field_level_rights_receipt",[])
            return [attest(value,"omni_counsel_review",{**context,"rights_refs":sorted(rights)})["receipt_id"]]
        if name=="customer_feedback_receipt":
            from .operating import validate_record
            if not isinstance(value,list) or not value or len(value)!=len(set(value)):
                raise ValueError("actual feedback record inventory required")
            operating=economic(evidence.get("operating_cost_receipt"))
            expected={ref for ref in operating["record_refs"] if get_receipt(store,ref,kind="operating_observation")["payload"]["kind"]=="customer_feedback"}
            if set(value)!=expected: raise ValueError("feedback inventory must exactly match the operating report, including negative observations")
            for ref in value:
                p=validate_record(store,ref,now=now,registry=registry,policies=policies)["payload"]
                if p["kind"]!="customer_feedback" or p["scope_hash"]!=context["scope_hash"] or p["policy_hash"]!=context["policy_hash"]:
                    raise ValueError("feedback scope/kind/version mismatch")
                if (p["window_start"],p["window_end"])!=(context["operating_window"]["start"],context["operating_window"]["end"]):
                    raise ValueError("feedback window differs from operating scope")
            return value
        if name=="operating_cost_receipt": economic(value); return [value]
        raise ValueError("unknown gate dependency")
    for gate,names in REQUIRED.items():
        for name in names:
            value=evidence.get(name)
            if value is None: continue
            try:
                refs=validate(name,value)
                result[gate]["missing"].remove(name); result[gate]["validated_refs"].extend(refs)
            except (ValueError,KeyError,TypeError,ImportError,AttributeError) as exc:
                result[gate]["invalid"][name]=str(exc)
        if not result[gate]["missing"] and not result[gate]["invalid"]:
            result[gate]["status"]="ready_for_human_review"
    # Later operating proof never retroactively grants an earlier dependency.
    for gate,prior in (("OMNI-11","OMNI-10"),("OMNI-12","OMNI-11")):
        if result[prior]["status"]!="ready_for_human_review":
            result[gate]["status"]="pending"; result[gate]["missing"].append(prior)
    return result


def record_gate_report(store,evidence,current_coverage,*,registry,policies,now,candidates=()):
    report=dependent_gates(evidence,current_coverage,candidates=candidates,store=store,registry=registry,policies=policies,now=now)
    refs=sorted({r for gate in REQUIRED for r in report[gate]["validated_refs"]})
    return put_receipt(store,"dependent_gate_report",{"contract":"omni-dependent-gates-1","evidence":evidence,
        "current_coverage":list(current_coverage),"candidates":list(candidates),"report":report,
        "implementation_hash":implementation_hash(),"policy_hash":digest(policies)},observed_at=now,refs=refs)
