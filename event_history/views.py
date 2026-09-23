"""Read-only views over the evidence history.

exact_archive   what one archive's bytes contain, verifiable against the file.
as_operated     evidence known by the cutoff, interpreted only by decisions
                made by then with resolvers that existed by then.
retrospective   evidence known by the cutoff, interpreted with chosen
                resolvers regardless of when they appeared. Labelled as a
                reconstruction; never the system's interpretation at the time.

Strict views use only sightings with an established knowledge time, after any
recorded correction of an archive's time label. Conflicting values are listed
with their attributions; nothing is voted on or averaged. Independence of
origins is not assessed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .adapters import adapt, normalize_instant
from .db import STRICT_KNOWLEDGE, canonical, digest
from .store import not_before_by_archive, version_identity

MODES = ("as_operated", "retrospective")
COVERAGE_TYPES = ("reproduces", "derived_from")


def view_digest(view):
    return digest(view)


def exact_archive(conn, archive_sha, path=None):
    row = conn.execute("SELECT * FROM archives WHERE archive_sha=?", (archive_sha,)).fetchone()
    if row is None:
        raise ValueError("unknown archive")
    result = {key: row[key] for key in row.keys() if key != "components_json"}
    result["components"] = json.loads(row["components_json"])
    result["paths"] = [r["path"] for r in conn.execute(
        "SELECT path FROM archive_paths WHERE archive_sha=? ORDER BY path", (archive_sha,))]
    result["annotations"] = [dict(r) for r in conn.execute(
        "SELECT kind, value, reason, decided_at FROM archive_annotations "
        "WHERE archive_sha=? ORDER BY decided_at", (archive_sha,))]
    sightings = conn.execute(
        "SELECT s.json_pointer, s.version_id, s.capture_policy_json, v.content_sha "
        "FROM sightings s JOIN evidence_versions v USING(version_id) "
        "WHERE s.archive_sha=? ORDER BY s.json_pointer", (archive_sha,)).fetchall()
    result["sightings"] = [{"pointer": s["json_pointer"], "version_id": s["version_id"],
                            "content_sha": s["content_sha"],
                            "capture_policy": json.loads(s["capture_policy_json"])}
                           for s in sightings]
    if path is not None:
        data = Path(path).read_bytes()
        result["bytes_verified"] = hashlib.sha256(data).hexdigest() == archive_sha
        if result["bytes_verified"] and row["status"] == "indexed":
            adapted = adapt(json.loads(data.decode("utf-8")))
            rebuilt = {item["pointer"]: version_identity(item)[0] for item in adapted["items"]}
            result["sightings_verified"] = rebuilt == {
                s["json_pointer"]: s["version_id"] for s in sightings}
    return result


def time_relation(entry, instant, field="event"):
    """before / not_before / undetermined. Unknown or overlapping bounds abstain."""
    instant = normalize_instant(instant)
    bounds = entry[field]
    if instant is None or bounds["lo"] is None or bounds["hi"] is None:
        return "undetermined"
    if bounds["hi"] < instant:
        return "before"
    if bounds["lo"] >= instant:
        return "not_before"
    return "undetermined"


def _effective(rows):
    superseded = {row["supersedes"] for row in rows if row["supersedes"]}
    return [row for row in rows if row["id"] not in superseded]


def knowledge_view(conn, cutoff, *, mode="as_operated", resolvers=None, include_legacy=False):
    normalized = normalize_instant(cutoff)
    if normalized is None:
        raise ValueError("cutoff needs an explicit UTC offset")
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    registered = {r["rule_version"]: r["introduced_at"]
                  for r in conn.execute("SELECT * FROM resolvers")}
    if mode == "as_operated":
        if resolvers is not None:
            raise ValueError("an as-operated view uses only resolvers that existed at the cutoff")
        active = {rule for rule, start in registered.items() if start <= normalized}
    else:
        active = set(registered if resolvers is None else resolvers)
        unknown = active - set(registered)
        if unknown:
            raise ValueError(f"unregistered resolvers {sorted(unknown)}")

    corrections = not_before_by_archive(conn)
    known, legacy = {}, set()
    for s in conn.execute("SELECT archive_sha, version_id, observed_at, knowledge_quality FROM sightings"):
        if s["knowledge_quality"] in STRICT_KNOWLEDGE:
            when = max(s["observed_at"], corrections.get(s["archive_sha"], ""))
            if when <= normalized:
                known[s["version_id"]] = min(known.get(s["version_id"], when), when)
        elif include_legacy:
            legacy.add(s["version_id"])
    legacy -= set(known)
    visible = set(known) | legacy

    def in_view(decision_at, rule):
        return rule in active and (mode == "retrospective" or decision_at <= normalized)

    versions = {}
    for version_id in sorted(visible):
        v = conn.execute("SELECT * FROM evidence_versions WHERE version_id=?", (version_id,)).fetchone()
        versions[version_id] = {
            "version_id": version_id, "kind": v["kind"], "source_key": v["source_key"],
            "native_evidence_id": v["native_evidence_id"],
            "native_revision_id": v["native_revision_id"], "origin_hint": v["origin_hint"],
            "subject_id": v["subject_id"], "content_sha": v["content_sha"],
            "first_known_at": known.get(version_id),
            "knowledge": "established" if version_id in known else "legacy_unknown",
            "publication": {"lo": v["publication_lo"], "hi": v["publication_hi"],
                            "precision": v["publication_precision"]},
            "event": {"lo": v["event_lo"], "hi": v["event_hi"], "precision": v["event_precision"]},
            "payload": json.loads(v["payload_json"]),
        }

    relation_rows = [dict(r, id=r["relation_id"], supersedes=r["supersedes_relation_id"])
                     for r in conn.execute("SELECT * FROM evidence_relations ORDER BY relation_id")
                     if in_view(r["decision_at"], r["rule_version"])
                     and r["from_version_id"] in versions and r["to_version_id"] in versions]
    relations = [{"from": r["from_version_id"], "to": r["to_version_id"],
                  "type": r["relation_type"], "from_claim": r["from_claim_pointer"],
                  "to_claim": r["to_claim_pointer"], "decision": r["decision"],
                  "decision_at": r["decision_at"], "rule_version": r["rule_version"]}
                 for r in _effective(relation_rows) if r["decision"] != "retracted"]
    accepted = [r for r in relations if r["decision"] == "accepted"]

    events = _events(conn, versions, in_view, normalized, mode)
    claims = _claims(versions, accepted)
    view = {
        "contract": "event-history-view-1",
        "view": mode, "cutoff": normalized, "as_operated": mode == "as_operated",
        "resolver_versions": sorted(active),
        "strict": not include_legacy,
        "evidence": [{k: value for k, value in v.items() if k != "payload"}
                     for v in versions.values()],
        "claims": claims, "relations": relations, "events": events,
        "corrections_applied": [{"archive_sha": sha, "observed_at_not_before": value}
                                for sha, value in sorted(corrections.items())],
    }
    if mode == "retrospective":
        view["label"] = ("retrospective reconstruction with resolvers "
                         f"{', '.join(sorted(active)) or 'none'}; not the system's "
                         f"interpretation at {normalized}")
    return view


def _events(conn, versions, in_view, cutoff, mode):
    rows = conn.execute("SELECT * FROM events ORDER BY event_id").fetchall()
    visible = {r["event_id"]: r for r in rows
               if in_view(r["created_at"], r["rule_version"])}
    members = [dict(m, id=m["membership_id"], supersedes=m["supersedes_membership_id"])
               for m in conn.execute("SELECT * FROM event_memberships ORDER BY membership_id")
               if m["event_id"] in visible and m["version_id"] in versions
               and in_view(m["decision_at"], m["rule_version"])]
    links = [dict(r, id=r["relation_id"], supersedes=r["supersedes_relation_id"])
             for r in conn.execute("SELECT * FROM event_relations ORDER BY relation_id")
             if r["from_event_id"] in visible and r["to_event_id"] in visible
             and in_view(r["decision_at"], r["rule_version"])]
    result = []
    for event_id, row in visible.items():
        result.append({
            "event_id": event_id, "anchor_key": row["anchor_key"],
            "event_kind": row["event_kind"], "subject_id": row["subject_id"],
            "provisional": bool(row["provisional"]),
            "members": [{"version_id": m["version_id"], "role": m["role"],
                         "decision": m["decision"], "rule_version": m["rule_version"]}
                        for m in _effective(members)
                        if m["event_id"] == event_id and m["decision"] != "retracted"],
            "links": [{"to": r["to_event_id"], "type": r["relation_type"],
                       "decision": r["decision"], "decision_at": r["decision_at"],
                       "rule_version": r["rule_version"]}
                      for r in _effective(links)
                      if r["from_event_id"] == event_id and r["decision"] != "retracted"],
        })
    return result


def _claims(versions, accepted):
    coverage, superseded_by = set(), {}
    for r in accepted:
        if r["type"] in COVERAGE_TYPES:
            coverage.add((r["from"], r["from_claim"]))
        if r["type"] == "supersedes_claim":
            superseded_by[(r["to"], r["to_claim"])] = f"{r['from']}#{r['from_claim']}"
    groups = {}
    for version_id, v in versions.items():
        claims = v["payload"].get("claims") if isinstance(v["payload"], dict) else None
        for key, claim in sorted((claims or {}).items()):
            pointer = f"/claims/{key}"
            if (version_id, pointer) in coverage or (version_id, None) in coverage:
                continue  # coverage of another source's claim, not a new assertion
            group = groups.setdefault(
                (v["subject_id"] or "", key, claim["period"], claim["unit"]),
                {"subject_id": v["subject_id"], "claim": key, "period": claim["period"],
                 "unit": claim["unit"], "values": [], "superseded": []})
            ref = {"value": claim["value"], "attributed_to": f"{version_id}#{pointer}"}
            if (version_id, pointer) in superseded_by:
                group["superseded"].append(dict(ref, superseded_by=superseded_by[(version_id, pointer)]))
            else:
                group["values"].append(ref)
    result = []
    for key in sorted(groups):
        group = groups[key]
        distinct = {v["value"] for v in group["values"]}
        group["status"] = ("conflicting" if len(distinct) > 1 else
                           "corrected" if group["superseded"] else "attributed")
        group["values"].sort(key=lambda v: v["attributed_to"])
        group["superseded"].sort(key=lambda v: v["attributed_to"])
        result.append(group)
    return result


def origin_summary(view, claim_ref):
    """Origins behind one claim: reported primary origins and coverage reports.

    Price or other measurements never count as corroborating source content,
    and the independence of reporting origins is not assessed.
    """
    version_id, _, pointer = claim_ref.partition("#")
    evidence = {e["version_id"]: e for e in view["evidence"]}
    if version_id not in evidence:
        raise ValueError("claim is not in this view")
    coverage = sorted({r["from"] for r in view["relations"]
                       if r["decision"] == "accepted" and r["type"] in COVERAGE_TYPES
                       and r["to"] == version_id and r["to_claim"] in (pointer, None)})
    origins = {evidence[version_id]["origin_hint"] or version_id}
    return {"claim": claim_ref, "primary_origins": len(origins),
            "coverage_reports": len(coverage), "coverage_versions": coverage,
            "measurement_corroboration": "not_applicable",
            "independence": "not_assessed"}
