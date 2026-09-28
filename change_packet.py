"""Issuer-change packet between two saved briefs (T8): read-only, no network.

For one subject, the packet answers "what changed since my previous brief,
and what can I verify?" Each change carries:

  what changed -> exact source, version, archive and JSON pointer
               -> limitation or conflict -> next missing evidence.

Sources come from the evidence history (event_history), read as operated at
each brief's generated_at, so later evidence or decisions never alter an
earlier packet. Numbers come only from retained claims in permitted extracts;
headline or filing metadata never supplies figures. Conflicts keep every
attribution, with no vote, average or recency rule. Origin independence is
not assessed. Price context is descriptive, never attributed to a change,
and withheld when the two price rows are not on a compatible basis.

Subject matching uses the recorded subject ids ("issuer:X", or "symbol:X" for
SEC rows, which is a listing symbol as reported). It is not a verified issuer
registry; that is T9.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from brief_tools import diff_briefs, load_brief
from event_history import knowledge_view, origin_summary
from event_history.adapters import normalize_instant
from event_history.store import not_before_by_archive
from stateutil import atomic_write_bytes, atomic_write_json

CONTRACT = "change-packet-1"
MONITORED_SOURCES = ("sec_edgar", "news")
CAPTURED_SCOPE = ("Captured scope: SEC filing rows, retained intake metadata and "
                  "permitted extracts in the evidence history; not a complete news archive.")
INTERPRETATION = ("A research reading aid over saved local data. It does not establish that "
                  "reported facts are true, that origins are independent, or that any price "
                  "move was caused by a change. It is not investment advice.")


def _limitation(code, text):
    return {"code": code, "text": text}


def _subject_ids(subject):
    subject = str(subject).strip()
    if not subject:
        raise ValueError("choose a subject, for example ALFA or issuer:ALFA")
    # Listing symbols are upper case everywhere (SEC rows, universes, prices);
    # other subject-id namespaces keep their recorded spelling.
    if ":" in subject:
        kind, _, value = subject.partition(":")
        if kind == "symbol":
            subject = f"symbol:{value.upper()}"
        return {subject}, value.upper()
    return {f"issuer:{subject.upper()}", f"symbol:{subject.upper()}"}, subject.upper()


def _subject_match(subject_id):
    if subject_id and subject_id.startswith("symbol:"):
        return "listing symbol as reported (symbol match); not a verified issuer identity"
    return "recorded subject id; not a verified issuer registry identity"


def _brief(path):
    path = Path(path)
    data = path.read_bytes()
    brief = load_brief(path)
    cutoff = normalize_instant(brief.get("generated_at"))
    if cutoff is None:
        raise ValueError(f"{path.name} has no generated_at with an explicit time zone")
    return brief, {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(),
                   "generated_at": brief.get("generated_at"), "cutoff": cutoff}


def _indexed(conn, sha):
    row = conn.execute("SELECT status, components_json FROM archives WHERE archive_sha=?",
                       (sha,)).fetchone()
    if row is None or row["status"] != "indexed":
        return False, {}
    return True, json.loads(row["components_json"])


class _Sources:
    """Evidence records with their payload summary and cutoff-bounded sightings."""

    def __init__(self, conn, view, subject_ids):
        self.conn = conn
        self.cutoff = view["cutoff"]
        self.corrections = not_before_by_archive(conn)
        self.evidence = {e["version_id"]: e for e in view["evidence"]
                         if e["subject_id"] in subject_ids}
        self._cache = {}

    def record(self, version_id):
        if version_id in self._cache:
            return self._cache[version_id]
        evidence = self.evidence[version_id]
        row = self.conn.execute("SELECT payload_json FROM evidence_versions WHERE version_id=?",
                                (version_id,)).fetchone()
        payload = json.loads(row["payload_json"])
        sightings = []
        for s in self.conn.execute(
                "SELECT s.archive_sha, s.json_pointer, s.observed_at, "
                "(SELECT path FROM archive_paths p WHERE p.archive_sha=s.archive_sha "
                " ORDER BY first_seen_at, path LIMIT 1) AS path "
                "FROM sightings s WHERE s.version_id=? ORDER BY s.observed_at, s.archive_sha",
                (version_id,)):
            known = max(s["observed_at"] or "", self.corrections.get(s["archive_sha"], ""))
            if s["observed_at"] and known <= self.cutoff:
                sightings.append({"archive_sha": s["archive_sha"], "path": s["path"],
                                  "pointer": s["json_pointer"], "known_at": known})
        record = {
            "source_key": evidence["source_key"], "kind": evidence["kind"],
            "version_id": version_id, "subject_id": evidence["subject_id"],
            "subject_match": _subject_match(evidence["subject_id"]),
            "first_known_at": evidence["first_known_at"],
            "publication": evidence["publication"], "event": evidence["event"],
            "summary": _summary(payload), "has_claims": bool(
                isinstance(payload, dict) and payload.get("claims")),
            "sightings": sightings,
        }
        self._cache[version_id] = record
        return record

    def public(self, version_id):
        record = dict(self.record(version_id))
        record.pop("has_claims")
        return record


def _summary(payload):
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    filing = " ".join(dict.fromkeys(payload[k] for k in ("form_type", "description")
                                    if isinstance(payload.get(k), str)))
    for value in (payload.get("title"), metadata.get("title"), filing):
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return None


def _claim_groups(view, subject_ids):
    return {(g["claim"], g["period"], g["unit"]): g for g in view["claims"]
            if g["subject_id"] in subject_ids}


def _ref_source(ref, evidence):
    version_id = ref["attributed_to"].split("#", 1)[0]
    return version_id, evidence[version_id]["source_key"]


def _claim_change(key, group, before, sources, view):
    name, period, unit = key
    values, superseded = [], []
    for ref in group["values"]:
        version_id, source_key = _ref_source(ref, sources.evidence)
        values.append({"value": ref["value"], "source_key": source_key,
                       "claim_ref": ref["attributed_to"]})
    for ref in group["superseded"]:
        version_id, source_key = _ref_source(ref, sources.evidence)
        by_version = ref["superseded_by"].split("#", 1)[0]
        superseded.append({"value": ref["value"], "source_key": source_key,
                           "superseded_by": sources.evidence[by_version]["source_key"]})
    if before is None:
        kind = "claim_conflicting" if group["status"] == "conflicting" else "claim_new"
    else:
        kind = {"corrected": "claim_corrected",
                "conflicting": "claim_conflicting"}.get(group["status"], "claim_changed")
    version_ids = list(dict.fromkeys(
        [v["claim_ref"].split("#", 1)[0] for v in values]
        + [r["attributed_to"].split("#", 1)[0] for r in group["superseded"]]))
    origins = []
    for role, refs in (("current", group["values"]), ("superseded", group["superseded"])):
        for ref in refs:
            summary = origin_summary(view, ref["attributed_to"])
            summary.update(role=role, value=ref["value"],
                           source_key=_ref_source(ref, sources.evidence)[1])
            summary["coverage_source_keys"] = sorted(sources.evidence[v]["source_key"]
                                                     for v in summary.pop("coverage_versions")
                                                     if v in sources.evidence)
            origins.append(summary)
    shown = ", ".join(f"{v['value']} ({v['source_key']})" for v in values)
    limitations = [_limitation("independence_not_assessed",
                               "Origin independence is not assessed; coverage reports "
                               "of a disclosure are not separate confirmations.")]
    needs = []
    if kind == "claim_corrected":
        old = ", ".join(f"{s['value']} ({s['source_key']})" for s in superseded)
        what = f"{name} for {period} corrected to {shown}; superseded {old}."
        limitations.append(_limitation(
            "superseded_claim",
            "The superseded value stays on record. Any coverage reports of the superseded "
            "value remain historical reports of that value, not of the correction."))
    elif kind == "claim_conflicting":
        what = f"{name} for {period} has conflicting attributed values: {shown}."
        limitations.append(_limitation(
            "conflict_retained",
            "Each value is kept with its attribution; no vote, average or recency rule "
            "picks one."))
        needs.append("a primary document or an explicit amendment that settles the "
                     f"{name} value")
    elif kind == "claim_new":
        what = f"{name} for {period}: {shown}."
    else:
        what = f"{name} for {period} now attributed as {shown}."
    change = {"kind": kind, "claim": name, "period": period, "unit": unit,
              "what_changed": what, "values": values, "superseded": superseded,
              "origins": origins, "sources": [sources.public(v) for v in version_ids],
              "limitations": limitations, "next_evidence": needs}
    if before is not None:
        change["previous_values"] = [
            {"value": ref["value"], "source_key": _ref_source(ref, sources.evidence)[1]}
            for ref in before["values"]]
    return change


def _record_change(record, cutoff):
    limitations, needs = [], []
    event, publication = record["event"], record["publication"]
    if event["precision"] == "day":
        limitations.append(_limitation(
            "event_time_day_only",
            "The event time is known only to the day; whether it came before or after a "
            "session open is undetermined."))
        needs.append("an exact event timestamp with its time zone")
    elif event["precision"] == "unknown" and publication["precision"] != "instant":
        limitations.append(_limitation(
            "timing_unknown", "Neither an event time nor an exact publication time was "
            "retained; the record cannot be placed against a session."))
        needs.append("an exact publication or event timestamp")
    if event["lo"] and event["lo"] > cutoff:
        day = event["lo"][:10] if event["precision"] == "instant" else "the scheduled date"
        limitations.append(_limitation(
            "scheduled_not_occurred",
            "The event is scheduled after this brief's time; neither its occurrence nor any "
            "outcome is established."))
        needs.append(f"the decision or outcome record after {day}")
    if not record["has_claims"]:
        limitations.append(_limitation(
            "no_retained_claims",
            "Metadata only; no numeric claims were retained, so no figures are reported."))
        needs.append("a permitted structured extract of the primary document")
    label = record["summary"] or record["source_key"]
    return {"kind": "new_record",
            "what_changed": f"New {record['kind']} record: {label}.",
            "limitations": limitations, "next_evidence": needs}


def _coverage_change(record, relations, sources):
    """A new report repeating a disclosure whose claim itself did not change."""
    targets = []
    for relation in relations:
        target = sources.evidence.get(relation["to"], {}).get("source_key", relation["to"])
        claim = (relation["to_claim"] or "").rpartition("/")[2] or "document"
        targets.append(f"{target} ({claim})")
    public_targets = [sources.public(r["to"]) for r in relations if r["to"] in sources.evidence]
    return {"kind": "new_coverage",
            "what_changed": f"New coverage report {record['source_key']} of {', '.join(targets)}.",
            "sources": [sources.public(record["version_id"]), *public_targets],
            "limitations": [_limitation(
                "coverage_not_confirmation",
                "A coverage report repeats an existing disclosure; it is not a separate "
                "confirmation, and the underlying claim did not change.")],
            "next_evidence": []}


def _conflict_change(relation, sources):
    ends = [relation["from"], relation["to"]]
    keys = []
    for version_id in ends:
        record = sources.record(version_id)
        keys.append(f"{record['source_key']} ({record['summary']})" if record["summary"]
                    else record["source_key"])
    limitations = [_limitation(
        "conflict_retained",
        "Both attributed reports are kept; no vote, average or recency rule picks one.")]
    if relation["decision"] == "candidate":
        limitations.append(_limitation(
            "candidate_link", "Candidate link, not reviewed; treat it as a possible conflict."))
    return {"kind": "conflict", "decision": relation["decision"],
            "what_changed": f"{keys[0]} conflicts with {keys[1]}.",
            "relation": {k: relation[k] for k in ("type", "decision", "decision_at",
                                                  "rule_version", "from_claim", "to_claim")},
            "sources": [sources.public(v) for v in ends],
            "limitations": limitations,
            "next_evidence": ["the underlying decision document, or an explicit retraction "
                              "by one of the sources"]}


def _relation_key(relation):
    return (relation["from"], relation["to"], relation["type"], relation["from_claim"],
            relation["to_claim"], relation["decision"])


def _source_state(name, entry, ticker):
    if not isinstance(entry, dict):
        return "absent"
    if name == "sec_edgar" and ticker in (entry.get("no_cik_tickers") or []):
        return "no_cik_for_subject"
    failed = entry.get("failed_requests")
    if (entry.get("fetch_error") or entry.get("exceptions")
            or (isinstance(failed, list) and failed)):
        return "degraded"
    return "reported"


def _coverage(current, components, ticker):
    health = current.get("health") if isinstance(current.get("health"), dict) else {}
    reported = health.get("sources") if isinstance(health.get("sources"), dict) else {}
    states = {name: _source_state(name, reported.get(name), ticker)
              for name in MONITORED_SOURCES}
    gaps = [f"{name} {state}" for name, state in states.items() if state != "reported"]
    for name in ("sec_filings", "evidence_intake"):
        status = (components.get(name) or {}).get("status")
        if status in ("capture_failed", "rejected"):
            gaps.append(f"{name} {status} in the evidence history")
    return {"sources": states, "history_components": components, "gaps": gaps}


def _price_rows(brief, ticker):
    sections = brief.get("sections") if isinstance(brief.get("sections"), dict) else {}
    rows = sections.get("prices") if isinstance(sections.get("prices"), list) else []
    return next((r for r in rows if isinstance(r, dict) and r.get("ticker") == ticker), None)


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _price_context(previous, current, ticker):
    old, new = _price_rows(previous, ticker), _price_rows(current, ticker)
    result = {"status": "unavailable", "reason": None, "change_pct": None,
              "previous": None, "current": None}
    for side, row in (("previous", old), ("current", new)):
        if row is not None:
            result[side] = {k: row.get(k) for k in ("price", "source_session", "source",
                                                    "session_complete", "adjustment_basis")}
    if old is None or new is None or not (_finite(old.get("price")) and _finite(new.get("price"))):
        result["reason"] = "price_missing"
    elif old.get("session_complete") is not True or new.get("session_complete") is not True:
        result["reason"] = "session_incomplete"
    elif (old.get("source") != new.get("source")
          or old.get("adjustment_basis") != new.get("adjustment_basis")):
        result["reason"] = "mixed_price_bases"
    elif old.get("source_session") == new.get("source_session") or old["price"] <= 0:
        result["reason"] = "no_comparable_sessions"
    else:
        basis = old.get("adjustment_basis") or "not recorded in the briefs"
        result.update(
            status="descriptive", change_pct=round((new["price"] / old["price"] - 1) * 100, 4),
            label=("Descriptive change between the two briefs' completed-session prices; "
                   "not attributed to any change above and not a return claim. Adjustment "
                   f"basis: {basis}. A corporate action between the sessions would make "
                   "this change not an economic return."))
    return result


def _in_universe(brief, ticker):
    universe = brief.get("universe") if isinstance(brief.get("universe"), dict) else {}
    members = set()
    for key in ("instrumented_tickers", "tickers", "editorial_only_tickers"):
        if isinstance(universe.get(key), list):
            members.update(t for t in universe[key] if isinstance(t, str))
    return ticker in members


def _subject_registry(registry, ticker, cutoff):
    """How the dated registry resolves the subject symbol at the current brief.

    `status` uses everything the registry knows now (retrospective when the
    registry learned it after the brief); `as_operated_status` uses only what
    it knew by the brief's time.
    """
    # Listing validity is an exchange date. The registry's listings are U.S.
    # (XNYS calendar), so a 01:30Z brief is still the previous New York day.
    stamp = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    day = stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    now = registry.resolve(ticker, on=day)
    then = registry.resolve(ticker, on=day, known_by=cutoff)
    result = {"registry_version": registry.version, "registry_fingerprint": registry.fingerprint(),
              "symbol": ticker, "on": day, "status": now["status"],
              "as_operated_status": then["status"], "candidates": now["candidates"],
              "instrument_id": now["instrument_id"], "issuer_id": now["issuer_id"],
              "cik": None, "legal_name": None, "type": None, "share_class": None,
              "verification": None, "venue": None}
    if now["status"] == "resolved":
        instrument = registry.instrument(now["instrument_id"])
        issuer = registry.issuers.get(instrument["issuer_id"]) or {}
        result.update(cik=issuer.get("cik"), legal_name=issuer.get("legal_name"),
                      type=instrument["type"], share_class=instrument["share_class"],
                      verification=instrument["verification"], venue=now["listing"]["venue"])
    if now["status"] == "resolved" and then["status"] != "resolved":
        result["label"] = ("retrospective: resolved with registry "
                           f"{registry.version}, which learned it after this brief")
    elif now["status"] == "resolved":
        result["label"] = "as operated: known to the registry by this brief's time"
    else:
        result["label"] = f"registry status {now['status']}"
    return result


def build_packet(conn, previous_path, current_path, subject, *, registry=None):
    """Change packet for one subject between two saved briefs (previous earlier)."""
    subject_ids, ticker = _subject_ids(subject)
    previous, prev_meta = _brief(previous_path)
    current, curr_meta = _brief(current_path)
    if prev_meta["cutoff"] >= curr_meta["cutoff"]:
        raise ValueError("the previous brief must be earlier than the current brief")
    prev_indexed, _ = _indexed(conn, prev_meta["sha256"])
    curr_indexed, components = _indexed(conn, curr_meta["sha256"])

    diff = diff_briefs(previous, current)
    warnings = list(diff["comparison_warnings"])
    for side, indexed in (("previous", prev_indexed), ("current", curr_indexed)):
        if not indexed:
            warnings.append(f"{side} brief is not in the evidence history; its records are "
                            "missing here (run python -m event_history import)")
    membership = {"previous": _in_universe(previous, ticker),
                  "current": _in_universe(current, ticker)}
    if not all(membership.values()):
        warnings.append("subject is not in both briefs' universes; coverage differs")
    registry_error = None
    if registry is None:
        import instrument_registry
        try:
            registry = instrument_registry.load_registry()
        except (OSError, ValueError) as error:
            # A stale or unreadable registry must not stop a read-only packet.
            registry_error = str(error)
    if registry is None:
        subject_registry = {"status": "registry_unavailable", "reason": registry_error,
                            "symbol": ticker, "label": "instrument registry unavailable"}
    else:
        subject_registry = _subject_registry(registry, ticker, curr_meta["cutoff"])
    if subject_registry["status"] == "ambiguous":
        warnings.append("subject symbol is ambiguous in the instrument registry "
                        f"({', '.join(subject_registry['candidates'])}); identity not established")

    prev_view = knowledge_view(conn, prev_meta["cutoff"])
    curr_view = knowledge_view(conn, curr_meta["cutoff"])
    sources = _Sources(conn, curr_view, subject_ids)
    prev_evidence = {e["version_id"] for e in prev_view["evidence"]
                     if e["subject_id"] in subject_ids}

    changes, unchanged = [], []
    prev_claims = _claim_groups(prev_view, subject_ids)
    for key, group in sorted(_claim_groups(curr_view, subject_ids).items()):
        before = prev_claims.get(key)
        if before is not None and (before["values"], before["superseded"]) == (
                group["values"], group["superseded"]):
            unchanged.append({"claim": key[0], "period": key[1], "unit": key[2],
                              "values": [{"value": r["value"],
                                          "source_key": _ref_source(r, sources.evidence)[1]}
                                         for r in group["values"]]})
            continue
        changes.append(_claim_change(key, group, before, sources, curr_view))

    coverage_of = {}
    for relation in curr_view["relations"]:
        if relation["decision"] == "accepted" and relation["type"] in ("reproduces", "derived_from"):
            coverage_of.setdefault(relation["from"], []).append(relation)
    # A claim change already lists its coverage reports under its origins.
    changed_claims = {(source["version_id"], change["claim"]) for change in changes
                      if "claim" in change for source in change["sources"]}
    records = []
    for version_id in sorted(set(sources.evidence) - prev_evidence):
        record = sources.record(version_id)
        if version_id in coverage_of:
            targets = [r for r in coverage_of[version_id]
                       if (r["to"], (r["to_claim"] or "").rpartition("/")[2]) not in changed_claims]
            if targets:
                change = _coverage_change(record, targets, sources)
                records.append((record["first_known_at"] or "", record["source_key"], change))
            continue
        if record["has_claims"]:
            continue  # shown under its claim change
        change = _record_change(record, curr_meta["cutoff"])
        change["sources"] = [sources.public(version_id)]
        records.append((record["first_known_at"] or "", record["source_key"], change))
    changes += [change for _, _, change in sorted(records, key=lambda r: r[:2])]

    # Conflicts come after the records they connect, so each side is introduced first.
    prev_relations = {_relation_key(r) for r in prev_view["relations"]}
    for relation in curr_view["relations"]:
        if (relation["type"] == "contradicts" and _relation_key(relation) not in prev_relations
                and relation["from"] in sources.evidence and relation["to"] in sources.evidence):
            changes.append(_conflict_change(relation, sources))

    coverage = _coverage(current, components, ticker)
    if changes:
        statement = f"{len(changes)} change(s) observed in this captured scope."
    else:
        statement = "No additional matching records were observed in this captured scope."
    if coverage["gaps"]:
        statement += (f" Coverage gaps: {'; '.join(coverage['gaps'])}. Absence of records from "
                      "these sources is not evidence that nothing happened.")
    return {
        "contract": CONTRACT, "subject": subject, "subject_ids": sorted(subject_ids),
        "subject_registry": subject_registry,
        "previous": prev_meta, "current": curr_meta,
        "comparability": {"configuration_comparable": not warnings, "warnings": warnings,
                          "indexed": {"previous": prev_indexed, "current": curr_indexed},
                          "subject_in_universe": membership,
                          "pipeline_versions": [previous.get("pipeline_version"),
                                                current.get("pipeline_version")]},
        "coverage": coverage, "scope_statement": f"{statement} {CAPTURED_SCOPE}",
        "changes": changes, "unchanged_claims": unchanged,
        "price_context": _price_context(previous, current, ticker),
        "interpretation": INTERPRETATION,
    }


def _source_line(source):
    sighting = source["sightings"][0] if source["sightings"] else None
    where = (f"{sighting['path']}#{sighting['pointer']}" if sighting
             else "no sighting known by the current brief")
    return (f"- {source['source_key']} ({source['kind']}), version {source['version_id'][:16]}, "
            f"first known {source['first_known_at'] or 'unknown'}: {where}")


def render_note(packet):
    """Four-part Markdown note: what changed, source/version, limitation, next evidence."""
    lines = [f"# Change note: {packet['subject']}", "",
             f"Briefs: {packet['previous']['generated_at']} -> {packet['current']['generated_at']}",
             "", packet["scope_statement"], ""]
    registry = packet.get("subject_registry")
    if registry and registry["status"] == "resolved":
        share_class = f", class {registry['share_class']}" if registry["share_class"] else ""
        lines += [f"Registry: {registry['symbol']} -> {registry['legal_name'] or 'issuer unknown'} "
                  f"(CIK {registry['cik'] or 'n/a'}{share_class}, {registry['type']}, "
                  f"{registry['verification']}); {registry['label']}.", ""]
    elif registry and registry["status"] == "registry_unavailable":
        lines += [f"Registry: unavailable ({registry['reason']}); identity not resolved.", ""]
    elif registry:
        lines += [f"Registry: {registry['symbol']} is {registry['status']} in registry "
                  f"{registry['registry_version']}.", ""]
    warnings = packet["comparability"]["warnings"]
    if warnings:
        lines += ["Comparability warnings:"] + [f"- {w}" for w in warnings] + [""]
    for number, change in enumerate(packet["changes"], 1):
        lines += [f"## {number}. What changed", "", change["what_changed"], "",
                  "**Source/version**", ""]
        lines += [_source_line(s) for s in change["sources"]]
        for origin in change.get("origins", []):
            reports = origin["coverage_reports"]
            listed = f" ({', '.join(origin['coverage_source_keys'])})" if reports else ""
            lines.append(f"- Origins of the {origin['role']} value {origin['value']} "
                         f"({origin['source_key']}): {origin['primary_origins']} disclosure "
                         f"origin, {reports} coverage reports{listed}; independence not assessed.")
        lines += ["", "**Limitation/conflict**", ""]
        lines += [f"- {l['text']}" for l in change["limitations"]] or ["- None recorded."]
        lines += ["", "**Next evidence needed**", ""]
        lines += [f"- {n}" for n in dict.fromkeys(change["next_evidence"])] or ["- None recorded."]
        lines.append("")
    if packet["unchanged_claims"]:
        lines += ["## Unchanged claims", ""]
        lines += [f"- {u['claim']} for {u['period']}: "
                  + ", ".join(f"{v['value']} ({v['source_key']})" for v in u["values"])
                  for u in packet["unchanged_claims"]]
        lines.append("")
    price = packet["price_context"]
    if price["status"] == "descriptive":
        lines += [f"Price context: {price['change_pct']:+.2f}% between the briefs' sessions. "
                  f"{price['label']}", ""]
    else:
        lines += [f"Price context: unavailable ({price['reason']}).", ""]
    lines += [packet["interpretation"], ""]
    return "\n".join(lines)


def save_note(packet, destination, *, overwrite=False):
    """Write the note (Markdown) or the packet (.json) to an explicit destination."""
    if not destination:
        raise ValueError("choose an explicit destination for the note")
    target = Path(destination)
    if target.exists() and not overwrite:
        raise FileExistsError(f"{target} exists; pass overwrite to replace it")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix.lower() == ".json":
        atomic_write_json(os.fspath(target), packet, indent=2)
    else:
        atomic_write_bytes(target, render_note(packet).encode("utf-8"))
    return target
