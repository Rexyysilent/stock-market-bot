"""Conservative, deterministic event candidates. Unmatched prose stays a lead.

Each claim uses one exact sentence and one explicit scope. A known trial is not
inherited through a sentence mentioning a different trial or through background
sections. No clinical number is inferred or copied between trials.
"""
from __future__ import annotations

import re

from .evidence import check_document, source_decision, span
from .identities import digest, time_interval
from .amounts import (CONTRACT_AMOUNTS, contract_amounts, MONEY_CUE, parse_amount_sentence,
                     SCOPED_PREDICATE, SCOPED_QUALIFIERS, amount_projection, amount_semantic)
from .clinical_numerics import (PREDICATE as NUMERIC_PREDICATE, QUALIFIERS as NUMERIC_QUALIFIERS,
                               STATISTIC, parse_sentence, result_projection, semantic)
from .clinical_tables import parse_tables

VERSION = "omni-classifier-5"
TRIGGER = re.compile(r"\b(endpoint|trial|phase|approv|submit|accepted|hold|offering|financing|agreement|contract|outage|licens|pre.application|obligat|ceiling)", re.I)
PHASE = re.compile(r"\bphase\s+(1/2|2/3|1b|1|2b|2|3|4)\b", re.I)
FUTURE = re.compile(r"\b(will|plans? to|intend\w* to|expects?|anticipat\w*|may|could)\b", re.I)
HISTORICAL = re.compile(r"\b(previously|prior|earlier|historical|background)\b", re.I)


def subject_issuers(sentence, resolved):
    """Require an issuer in the grammatical lead before the event verb.

    Mentions after with/against/versus or after the event predicate are context.
    This intentionally abstains on complex prose instead of promoting a peer.
    """
    verb = re.search(r"\b(reports?|reported|announc\w*|began|begins?|will|plans?|intends?|expects?|submitted|received|raised|filed|signed|won|entered|met|did not meet)\b", sentence, re.I)
    if not verb:
        return []
    boundary = verb.start()
    modifier = re.search(r"\b(with|against|versus|unlike|compared to|including|partner of)\b", sentence[:boundary], re.I)
    if modifier:
        boundary = modifier.start()
    return sorted({m["entity_id"] for m in resolved["mentions"] if m["kind"] == "issuer" and m["end"] <= boundary})


def predicates(text):
    """Small auditable grammar; used again independently by the validator."""
    lower = text.lower()
    result = []
    future = bool(FUTURE.search(text))
    phase = PHASE.search(text)
    if phase:
        result.append(("phase", "PHASE" + phase[1].upper()))
    # Scope each endpoint clause; a secondary win cannot flip a primary miss.
    for rank in ("primary", "secondary"):
        patterns = (
            ("not_met", rf"(?:did not meet|(?:has |have |had )?not (?:met|achieved)|failed to (?:meet|achieve)|missed)\s+(?:(?:its|the|a|key)\s+)*(?:pre[- ]?specified\s+)?{rank}\s+endpoint"),
            ("not_met", rf"{rank}\s+endpoint[^.;]{{0,45}}?\b(?:was not (?:met|achieved)|not achieved|not met)\b"),
            ("met", rf"(?:met|achieved)\s+(?:(?:its|the|a|key)\s+)*(?:pre[- ]?specified\s+)?{rank}\s+endpoint"),
            ("met", rf"{rank}\s+endpoint[^.;]{{0,45}}?\b(?:was met|was achieved)\b"),
        )
        hits = [value for value, pattern in patterns if re.search(pattern, lower)]
        # Future assertions are proposed milestones, never observed outcomes.
        if hits and not future:
            result.append((rank + "_endpoint_status", "not_met" if "not_met" in hits else "met"))
    if not future and not any(p == "secondary_endpoint_status" for p, _ in result) and re.search(
        r"\bmet\s+(?:(?:its|the|a|key)\s+)*primary endpoint[^.;]{0,100}?\band\s+(?:(?:a|the|key)\s+)*secondary endpoint", lower
    ):
        result.append(("secondary_endpoint_status", "met"))
    if re.search(r"post.?hoc|ad hoc|exploratory", lower):
        result.append(("analysis_class", "exploratory"))
    elif re.search(r"pre.?specified", lower):
        result.append(("analysis_class", "prespecified"))
    if re.search(r"pre[- ]application", lower):
        result.append(("regulatory_stage", "pre_application"))
    elif re.search(r"\b(approv\w*|submit|submitted|submission|accepted|acceptance|priority review|clinical hold)\b", lower):
        if future:
            stage = "planned_submission" if re.search(r"submit|submission|filing", lower) else "planned_regulatory_milestone"
        elif re.search(r"not approved|approval (?:pending|sought)|seeks? approval", lower):
            stage = "approval_not_established"
        elif re.search(r"(?:fda|regulator|agency) (?:has )?approved|(?:was|is|has been) approved", lower):
            stage = "approved"
        elif "priority review" in lower:
            stage = "priority_review"
        elif re.search(r"accepted|acceptance", lower):
            stage = "accepted"
        elif re.search(r"submitted", lower):
            stage = "submitted"
        elif "clinical hold" in lower:
            stage = "clinical_hold"
        else:
            stage = "regulatory_lead"
        result.append(("regulatory_stage", stage))
    if re.search(r"\b(shelf|at.the.market|atm)\b", lower):
        result.append(("financing_stage", "authority"))
    elif re.search(r"\b(?:received|raised)\b[^.;]{0,50}\b(?:proceeds|cash)\b", lower) and not future:
        result.append(("financing_stage", "received_proceeds"))
    if re.search(r"\b(contract|award|agreement)\b", lower):
        stage = ("ceiling" if re.search(r"ceiling|up to|maximum", lower) else
                 "option" if "option" in lower else
                 "obligation" if re.search(r"obligated|obligation", lower) else
                 "nonbinding" if re.search(r"non.?binding|memorandum|plans?", lower) else "reported_agreement")
        result.append(("contract_stage", stage))
    # Negation, cessation and denied assertions require adjudication. Keeping a
    # lead is preferable to turning a historical approval or a cancelled award
    # into a current positive claim. This intentionally abstains on complex prose.
    uncertain = bool(re.search(r"\b(not|never|no|denied|denies|withdrawn|withdrawal|revoked|rescinded|terminated|cancelled|canceled|lifted)\b|n['’]t\b", lower))
    if uncertain:
        result = [(p, "not_established" if p.endswith("_stage") else v)
                  for p, v in result if not (p.endswith("endpoint_status") and v == "met")]
    if future:
        result = [(p, "planned_agreement" if p == "contract_stage" else v) for p, v in result]
    if not uncertain and not future and not HISTORICAL.search(text):
        stage = next((v for p, v in result if p == "contract_stage"), None)
        result.extend(contract_amounts(text, stage=stage))
    return result


def sentences(text):
    # Split at paragraph boundaries and sentence endings, retaining exact offsets.
    # Corporate abbreviations need not be resolved by inferring a missing subject.
    for paragraph in re.finditer(r"[^\n]+", text):
        offset = paragraph.start()
        for piece in re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph.group()):
            start = text.find(piece, offset, paragraph.end())
            if piece.strip():
                yield start, start + len(piece), piece
            offset = start + len(piece)


def extract(doc, registry, policy, *, known_by):
    check_document(doc)
    reason = source_decision(policy, doc["url"], "local_analysis")
    if reason or digest(policy) != doc["source_policy_fingerprint"]:
        return {"version": VERSION, "events": [], "leads": [{"reason": reason or "policy_snapshot_mismatch"}], "signal_eligible": False}
    resolved = registry.resolve(doc["text"], known_by=known_by)
    events, leads = [], []
    context = None
    context_support = None
    previous_end = 0
    tables = parse_tables(doc, registry, known_by=known_by)
    leads.extend(tables["abstentions"])
    table_rows = {(r["support"]["start"], r["support"]["end"]): r for r in tables["results"]}
    units = [(s, e, t) for s, e, t in sentences(doc["text"])
             if not any(s < hi and e > lo for lo, hi in tables["blocks"])]
    units.extend((s, e, doc["text"][s:e]) for s, e in table_rows)
    for start, end, sentence in sorted(units):
        if "\n" in doc["text"][previous_end:start]:
            context = None
            context_support = None
        previous_end = end
        if not TRIGGER.search(sentence) and not STATISTIC.search(sentence) and not MONEY_CUE.search(sentence):
            context = None
            context_support = None
            continue
        scoped = registry.resolve(sentence, known_by=known_by)
        trials = sorted({m["entity_id"] for m in scoped["mentions"] if m["kind"] == "trial"})
        explicit_trials = re.findall(r"\bNCT\d{8}\b", sentence, re.I)
        # Unknown trial identifiers remain distinct; they are never assigned to a
        # previously named known trial.
        trials = sorted(set(trials) | {"unresolved-trial:" + t.upper() for t in explicit_trials
                                      if "trial:" + t.upper() not in trials})
        if trials:
            context = trials[0] if len(trials) == 1 else None
            context_support = span(doc, start, end) if context else None
        elif HISTORICAL.search(sentence):
            context = None
            context_support = None
        # Context is allowed only for an immediately continuing endpoint sentence.
        scope = trials[0] if len(trials) == 1 else context if re.search(r"\b(?:the trial|the study|its primary|the primary)\b", sentence, re.I) else None
        table_row = table_rows.get((start, end))
        extracted = [] if table_row else predicates(sentence)
        numeric = {"results": [table_row], "abstentions": []} if table_row else parse_sentence(doc, start, end, registry, known_by=known_by)
        leads.extend(numeric["abstentions"])
        numerical_results = {r["result_id"]: r for r in numeric["results"]}
        extracted.extend((NUMERIC_PREDICATE, result_id) for result_id in numerical_results)
        amount_output = {"atoms": [], "abstentions": []} if table_row else parse_amount_sentence(doc, start, end)
        leads.extend(amount_output["abstentions"])
        amount_atoms = {a["amount_id"]: a for a in amount_output["atoms"]}
        extracted.extend((SCOPED_PREDICATE, amount_id) for amount_id in amount_atoms)
        if not extracted:
            leads.append({"reason": "unclassified_event_lead", "support": span(doc, start, end)})
            continue
        subjects = subject_issuers(sentence, scoped)
        # A source reporting party is attribution, not an assertion of ownership.
        for predicate, value in extracted:
            family = ("clinical_result" if predicate in ("phase", "primary_endpoint_status", "secondary_endpoint_status", "analysis_class", NUMERIC_PREDICATE) else
                      "regulatory" if predicate == "regulatory_stage" else "financing" if predicate == "financing_stage"
                      or predicate == SCOPED_PREDICATE and amount_atoms[value]["role"]["value"] in ("financing_authority", "gross_proceeds", "net_proceeds") else "contract")
            claim = {"predicate": predicate, "value": value, "scope_id": scope,
                     "subject_ids": subjects, "reporting_party": doc["reporting_party"],
                     "class": "attributed_report", "support": span(doc, start, end),
                     "scope_support": context_support if scope else None,
                     "historical": bool(HISTORICAL.search(sentence)),
                     "future": bool(FUTURE.search(sentence)), "units": "USD" if predicate in CONTRACT_AMOUNTS else None,
                     "numeric_result": value if predicate in CONTRACT_AMOUNTS else None,
                     "qualifiers": ["stated_contract_amount_not_revenue"] if predicate in CONTRACT_AMOUNTS else []}
            claim["claim_id"] = "claim:" + digest(claim)
            if predicate == NUMERIC_PREDICATE:
                claim["numeric_result"] = numerical_results[value]
                claim["qualifiers"] = list(NUMERIC_QUALIFIERS)
                claim["claim_id"] = "claim:" + digest({k: v for k, v in claim.items() if k != "claim_id"})
            if predicate == SCOPED_PREDICATE:
                claim["amount_atom"] = amount_atoms[value]
                claim["qualifiers"] = list(SCOPED_QUALIFIERS)
                claim["claim_id"] = "claim:" + digest({k: v for k, v in claim.items() if k != "claim_id"})
            claim["semantic_id"] = "meaning:" + digest({k: v for k, v in claim.items()
                if k not in ("support", "scope_support", "claim_id")})
            if predicate == NUMERIC_PREDICATE:
                claim["semantic_id"] = "meaning:" + digest({k: semantic(v) if k == "numeric_result" else v
                    for k, v in claim.items() if k not in ("support", "scope_support", "claim_id", "semantic_id")})
            if predicate == SCOPED_PREDICATE:
                claim["semantic_id"] = "meaning:" + digest({k: amount_semantic(v) if k == "amount_atom" else v
                    for k, v in claim.items() if k not in ("support", "scope_support", "claim_id", "semantic_id")})
            key = (family, scope or "unresolved_scope", doc["origin_cluster_id"])
            event = next((e for e in events if tuple(e["identity_components"]) == key), None)
            if event is None:
                event = {"event_id": "event:" + digest(list(key)), "identity_components": list(key),
                         "family": family, "scope_id": scope, "claims": [], "evidence_ids": [doc["evidence_id"]],
                         "origin_clusters": [doc["origin_cluster_id"]], "first_observed_at": doc["first_observed_at"],
                         "event_time": time_interval(None), "signal_eligible": False}
                events.append(event)
            event["claims"].append(claim)
    for event in events:
        # The source-native origin is a conservative milestone discriminator.
        # Different disclosures are not merged merely for sharing a trial.
        event["event_revision"] = digest(sorted(c["semantic_id"] for c in event["claims"]))
        if any(c.get("predicate") == SCOPED_PREDICATE for c in event["claims"]):
            event["scoped_amounts"] = amount_projection(event["claims"])
        if event["family"] == "clinical_result":
            trial = registry.entities.get(event["scope_id"], {})
            event["clinical_context"] = {
                "trial_id": event["scope_id"], "registry_phase": trial.get("phase"),
                "indication": trial.get("indication"), "population": None,
                "asset_ids": sorted({r["from_id"] for r in registry.data["relationships"]
                                     if r["to_id"] == event["scope_id"] and r["role"] in ("evaluated_in", "combination_and_comparator")}),
                "comparator": None, "endpoint_definitions": None, "analysis_milestone": None,
                "numerical_results": result_projection(event["claims"]), "overall_survival_status": "not_established",
                "next_checkpoint": None, "regulatory_approval": "not_established",
            }
        event["resolution"] = resolved
        event["classifier_version"] = VERSION
    return {"version": VERSION, "events": events, "leads": leads, "signal_eligible": False}
