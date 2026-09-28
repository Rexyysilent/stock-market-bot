"""Import archives and record interpretation decisions, append-only.

Import is transactional and idempotent: the same bytes, at any path, are one
archive identity. Decisions (event memberships, evidence and event relations)
are new rows with a decision time and a registered resolver; nothing is
rewritten. Validation refuses what SQL alone cannot: unresolvable pointers,
incompatible claims, cycles, out-of-order supersession, decisions made before
their resolver existed or before the evidence was known, and measurements
presented as corroborating source content.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .adapters import NORMALIZER_VERSION, adapt, normalize_instant, resolve_pointer
from .db import INSTANT_FORMAT, STRICT_KNOWLEDGE, canonical, digest, transaction

FILING_ANCHOR_RULE = "filing-anchor-1"
DERIVATION_TYPES = ("reproduces", "derived_from", "amends", "supersedes_claim", "resolves")
ORDERED_TYPES = ("amends", "supersedes_claim", "resolves")
CLAIM_MATCHED_TYPES = ("supersedes_claim", "contradicts", "reproduces")
IDENTITY_TYPES = ("alias_of", "merge_into", "split_into")
DECISIONS = ("accepted", "candidate", "retracted")


def utc_now():
    return datetime.now(timezone.utc).strftime(INSTANT_FORMAT)


def _instant(value, name):
    normalized = normalize_instant(value)
    if normalized is None:
        raise ValueError(f"{name} needs an explicit UTC offset")
    return normalized


# ---------------------------------------------------------------- import

def import_archive(conn, path, *, indexed_at=None, _adapt=adapt):
    path = Path(path)
    data = path.read_bytes()
    archive_sha = hashlib.sha256(data).hexdigest()
    indexed_at = _instant(indexed_at, "indexed_at") if indexed_at else utc_now()
    with transaction(conn):
        existing = conn.execute("SELECT status FROM archives WHERE archive_sha=?",
                                (archive_sha,)).fetchone()
        if existing is not None:
            conn.execute("INSERT OR IGNORE INTO archive_paths VALUES (?,?,?)",
                         (archive_sha, str(path), indexed_at))
            return {"archive_sha": archive_sha, "status": "duplicate",
                    "archive_status": existing["status"], "sightings": 0}
        try:
            doc = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            doc, adapted = None, {"status": "quarantined", "reason": "unreadable_json",
                                  "adapter": "quarantine", "items": [], "components": {}}
        else:
            adapted = _adapt(doc)
        quarantined = adapted["status"] == "quarantined"
        conn.execute(
            "INSERT INTO archives VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (archive_sha, len(data), path.stem,
             None if quarantined else _text(adapted.get("generated_at_raw")),
             None if quarantined else adapted["observed_at"],
             "unknown" if quarantined else adapted["knowledge_quality"],
             None if quarantined else _text(adapted.get("schema_version")),
             None if quarantined else _text(adapted.get("pipeline_version")),
             adapted["adapter"], adapted["status"], adapted.get("reason"),
             canonical(adapted["components"]), indexed_at),
        )
        conn.execute("INSERT INTO archive_paths VALUES (?,?,?)",
                     (archive_sha, str(path), indexed_at))
        for item in adapted["items"]:
            resolve_pointer(doc, item["pointer"])  # a sighting must point at real bytes
            version_id = _insert_version(conn, item)
            _insert_sighting(conn, archive_sha, item, version_id, adapted)
    return {"archive_sha": archive_sha, "status": adapted["status"],
            "reason": adapted.get("reason"), "adapter": adapted["adapter"],
            "sightings": len(adapted["items"])}


def _text(value):
    return value if isinstance(value, str) else None


def version_identity(item):
    payload_json = canonical(item["payload"])
    content_sha = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    version_id = "ev:" + digest([item["source_key"], content_sha, NORMALIZER_VERSION])
    return version_id, content_sha, payload_json


def _insert_version(conn, item):
    version_id, content_sha, payload_json = version_identity(item)
    pub, event = item["publication"], item["event"]
    conn.execute(
        "INSERT OR IGNORE INTO evidence_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (version_id, item["native_evidence_id"], item["native_revision_id"],
         item["origin_hint"], item["source_key"], content_sha, item["kind"],
         item["subject_id"], pub[0], pub[1], event[0], event[1], pub[2], event[2],
         NORMALIZER_VERSION, payload_json),
    )
    return version_id


def _insert_sighting(conn, archive_sha, item, version_id, adapted):
    conn.execute(
        "INSERT INTO sightings VALUES (?,?,?,?,?,?)",
        (archive_sha, item["pointer"], version_id, adapted["observed_at"],
         adapted["knowledge_quality"], canonical(item["capture_policy"])),
    )


def import_paths(conn, paths, **kwargs):
    reports = [import_archive(conn, path, **kwargs) for path in paths]
    summary = {}
    for report in reports:
        summary[report["status"]] = summary.get(report["status"], 0) + 1
    return {"archives": len(reports), "by_status": summary,
            "sightings": sum(r["sightings"] for r in reports), "reports": reports}


def annotate_archive(conn, archive_sha, *, observed_at_not_before, reason, decided_at=None):
    """Record that an archive's own time label is earlier than its true capture."""
    value = _instant(observed_at_not_before, "observed_at_not_before")
    decided_at = _instant(decided_at, "decided_at") if decided_at else utc_now()
    row = conn.execute("SELECT observed_at FROM archives WHERE archive_sha=?",
                       (archive_sha,)).fetchone()
    if row is None:
        raise ValueError("unknown archive")
    if row["observed_at"] is None or value <= row["observed_at"]:
        raise ValueError("a correction may only move knowledge time later than the archive label")
    if not reason or not str(reason).strip():
        raise ValueError("an annotation needs a reason")
    annotation_id = "a:" + digest([archive_sha, "observed_at_not_before", value, reason, decided_at])
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO archive_annotations VALUES (?,?,?,?,?,?)",
                     (annotation_id, archive_sha, "observed_at_not_before", value,
                      reason, decided_at))
    return annotation_id


# ------------------------------------------------------------- knowledge

def not_before_by_archive(conn):
    result = {}
    for row in conn.execute("SELECT archive_sha, value FROM archive_annotations "
                            "WHERE kind='observed_at_not_before'"):
        result[row["archive_sha"]] = max(result.get(row["archive_sha"], ""), row["value"])
    return result


def first_known_at(conn, version_id, corrections=None):
    """Earliest strict knowledge time of a version (after corrections), or None."""
    corrections = not_before_by_archive(conn) if corrections is None else corrections
    times = [max(row["observed_at"], corrections.get(row["archive_sha"], ""))
             for row in conn.execute(
                 "SELECT archive_sha, observed_at, knowledge_quality FROM sightings "
                 "WHERE version_id=?", (version_id,))
             if row["knowledge_quality"] in STRICT_KNOWLEDGE]
    return min(times) if times else None


# ------------------------------------------------------------- decisions

def register_resolver(conn, rule_version, *, introduced_at, description):
    introduced_at = _instant(introduced_at, "introduced_at")
    row = conn.execute("SELECT * FROM resolvers WHERE rule_version=?", (rule_version,)).fetchone()
    if row is not None:
        if (row["introduced_at"], row["description"]) != (introduced_at, description):
            raise ValueError("a resolver's introduction is immutable; use a new rule_version")
        return rule_version
    with transaction(conn):
        conn.execute("INSERT INTO resolvers VALUES (?,?,?)",
                     (rule_version, introduced_at, description))
    return rule_version


def _check_resolver(conn, rule_version, decision_at):
    row = conn.execute("SELECT introduced_at FROM resolvers WHERE rule_version=?",
                       (rule_version,)).fetchone()
    if row is None:
        raise ValueError(f"unregistered resolver {rule_version!r}")
    if decision_at < row["introduced_at"]:
        raise ValueError("a decision cannot predate the resolver that made it")


def _version(conn, version_id):
    row = conn.execute("SELECT * FROM evidence_versions WHERE version_id=?",
                       (version_id,)).fetchone()
    if row is None:
        raise ValueError(f"unknown evidence version {version_id!r}")
    return row


def _claim(version_row, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/claims/"):
        raise ValueError("claim pointers look like /claims/<key>")
    try:
        claim = resolve_pointer(json.loads(version_row["payload_json"]), pointer)
    except KeyError:
        raise ValueError(f"claim pointer {pointer} does not resolve in {version_row['version_id']}")
    if not isinstance(claim, dict) or "value" not in claim:
        raise ValueError(f"{pointer} is not a claim")
    return claim


def create_event(conn, anchor_key, *, event_kind, created_at, rule_version,
                 subject_id=None, provisional=False):
    """Stable identity from the anchor; an existing anchor is reused, never rewritten."""
    created_at = _instant(created_at, "created_at")
    _check_resolver(conn, rule_version, created_at)
    event_id = "event:" + digest(anchor_key)
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?,?,?,?)",
                     (event_id, anchor_key, subject_id, event_kind,
                      1 if provisional else 0, created_at, rule_version))
    return event_id


def _check_supersedes(conn, table, key, supersedes, same, decision_at):
    if supersedes is None:
        return
    row = conn.execute(f"SELECT * FROM {table} WHERE {key}=?", (supersedes,)).fetchone()
    if row is None:
        raise ValueError("superseded decision does not exist")
    if any(row[field] != value for field, value in same.items()):
        raise ValueError("a decision can only supersede one about the same subject")
    if decision_at < row["decision_at"]:
        raise ValueError("supersession must be chronological")
    column = "supersedes_membership_id" if table == "event_memberships" else "supersedes_relation_id"
    if conn.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (supersedes,)).fetchone():
        raise ValueError("that decision is already superseded; supersession is linear")


def record_membership(conn, event_id, version_id, *, role, decision, decision_at,
                      rule_version, supersedes=None):
    decision_at = _instant(decision_at, "decision_at")
    if decision not in DECISIONS:
        raise ValueError("unknown decision")
    _check_resolver(conn, rule_version, decision_at)
    _version(conn, version_id)
    if conn.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone() is None:
        raise ValueError("unknown event")
    known = first_known_at(conn, version_id)
    if decision == "accepted" and (known is None or decision_at < known):
        raise ValueError("cannot accept membership of evidence before it was known")
    _check_supersedes(conn, "event_memberships", "membership_id", supersedes,
                      {"event_id": event_id, "version_id": version_id}, decision_at)
    membership_id = "m:" + digest([event_id, version_id, role, decision, decision_at,
                                   rule_version, supersedes])
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO event_memberships VALUES (?,?,?,?,?,?,?,?)",
                     (membership_id, event_id, version_id, role, decision, decision_at,
                      rule_version, supersedes))
    return membership_id


def _effective_edges(conn, table, types, from_col, to_col):
    rows = conn.execute(f"SELECT * FROM {table} WHERE relation_type IN "
                        f"({','.join('?' * len(types))})", types).fetchall()
    superseded = {row["supersedes_relation_id"] for row in rows if row["supersedes_relation_id"]}
    return [(row[from_col], row[to_col]) for row in rows
            if row["relation_id"] not in superseded and row["decision"] == "accepted"]


def _creates_cycle(edges, start, end):
    """True when adding start->end closes a directed cycle (multi-hop)."""
    graph = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
    stack, seen = [end], set()
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(graph.get(node, ()))
    return False


def record_evidence_relation(conn, from_version, to_version, relation_type, *,
                             decision, decision_at, rule_version,
                             from_claim=None, to_claim=None, supersedes=None):
    decision_at = _instant(decision_at, "decision_at")
    if decision not in DECISIONS:
        raise ValueError("unknown decision")
    if from_version == to_version:
        raise ValueError("a version cannot relate to itself")
    _check_resolver(conn, rule_version, decision_at)
    source, target = _version(conn, from_version), _version(conn, to_version)
    if relation_type == "supersedes_claim" and (from_claim is None or to_claim is None):
        raise ValueError("supersedes_claim needs both claim pointers")
    claims = [_claim(row, ptr) if ptr is not None else None
              for row, ptr in ((source, from_claim), (target, to_claim))]
    if relation_type in CLAIM_MATCHED_TYPES and all(claims):
        a, b = claims
        if relation_type != "reproduces" and from_claim.rsplit("/", 1)[1] != to_claim.rsplit("/", 1)[1]:
            raise ValueError("claims about different quantities are not comparable")
        if (a["unit"], a["period"]) != (b["unit"], b["period"]):
            raise ValueError("claims with different units or periods are not comparable")
    if (source["kind"] == "measurement" and target["kind"] != "measurement"
            and relation_type in DERIVATION_TYPES):
        raise ValueError("a measurement does not reproduce, amend or corroborate source content")
    known_from, known_to = first_known_at(conn, from_version), first_known_at(conn, to_version)
    if known_from and known_to and decision_at < max(known_from, known_to):
        raise ValueError("cannot decide about evidence before it was known")
    if decision == "accepted":
        if known_from is None or known_to is None:
            raise ValueError("strict knowledge time of both versions is required to accept")
        if relation_type in ORDERED_TYPES and known_from < known_to:
            raise ValueError("a correction cannot be known before what it corrects")
        if relation_type in DERIVATION_TYPES and _creates_cycle(
                _effective_edges(conn, "evidence_relations", DERIVATION_TYPES,
                                 "from_version_id", "to_version_id"),
                from_version, to_version):
            raise ValueError("relation would create a derivation cycle")
    _check_supersedes(conn, "evidence_relations", "relation_id", supersedes,
                      {"from_version_id": from_version, "to_version_id": to_version},
                      decision_at)
    relation_id = "r:" + digest([from_version, to_version, relation_type, from_claim, to_claim,
                                 decision, decision_at, rule_version, supersedes])
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO evidence_relations VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (relation_id, from_version, to_version, relation_type, from_claim,
                      to_claim, decision, decision_at, rule_version, supersedes))
    return relation_id


def record_event_relation(conn, from_event, to_event, relation_type, *, decision,
                          decision_at, rule_version, supersedes=None):
    """Alias/merge/split links between stable event IDs; IDs are never rewritten."""
    decision_at = _instant(decision_at, "decision_at")
    if decision not in DECISIONS:
        raise ValueError("unknown decision")
    if from_event == to_event:
        raise ValueError("an event cannot relate to itself")
    _check_resolver(conn, rule_version, decision_at)
    for event_id in (from_event, to_event):
        row = conn.execute("SELECT created_at FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise ValueError("unknown event")
        if decision_at < row["created_at"]:
            raise ValueError("cannot link an event before it existed")
    if (decision == "accepted" and relation_type in IDENTITY_TYPES and _creates_cycle(
            _effective_edges(conn, "event_relations", IDENTITY_TYPES,
                             "from_event_id", "to_event_id"), from_event, to_event)):
        raise ValueError("relation would create an alias/merge cycle")
    _check_supersedes(conn, "event_relations", "relation_id", supersedes,
                      {"from_event_id": from_event, "to_event_id": to_event}, decision_at)
    relation_id = "er:" + digest([from_event, to_event, relation_type, decision,
                                  decision_at, rule_version, supersedes])
    with transaction(conn):
        conn.execute("INSERT OR IGNORE INTO event_relations VALUES (?,?,?,?,?,?,?,?)",
                     (relation_id, from_event, to_event, relation_type, decision,
                      decision_at, rule_version, supersedes))
    return relation_id


def anchor_filings(conn, *, decided_at=None, rule_version=FILING_ANCHOR_RULE):
    """One event per SEC accession. Issuer and day never merge distinct filings."""
    decided_at = _instant(decided_at, "decided_at") if decided_at else utc_now()
    register_resolver(conn, rule_version, introduced_at=_resolver_start(conn, rule_version, decided_at),
                      description="one event per SEC accession number; filing is its primary assertion")
    anchored = 0
    rows = conn.execute("SELECT version_id, native_evidence_id, subject_id, payload_json "
                        "FROM evidence_versions WHERE kind='filing' "
                        "AND native_evidence_id LIKE 'sec:%' ORDER BY version_id").fetchall()
    for row in rows:
        known = first_known_at(conn, row["version_id"])
        if known is None or known > decided_at:
            continue
        form = json.loads(row["payload_json"]).get("form_type") or "unknown"
        event_id = create_event(conn, "filing:" + row["native_evidence_id"],
                                event_kind="sec_filing:" + form, created_at=decided_at,
                                rule_version=rule_version, subject_id=row["subject_id"])
        exists = conn.execute("SELECT 1 FROM event_memberships WHERE event_id=? AND version_id=? "
                              "AND rule_version=?", (event_id, row["version_id"], rule_version)).fetchone()
        if exists is None:
            record_membership(conn, event_id, row["version_id"], role="primary_assertion",
                              decision="accepted", decision_at=decided_at, rule_version=rule_version)
            anchored += 1
    return {"rule_version": rule_version, "new_memberships": anchored}


def _resolver_start(conn, rule_version, decided_at):
    row = conn.execute("SELECT introduced_at FROM resolvers WHERE rule_version=?",
                       (rule_version,)).fetchone()
    return row["introduced_at"] if row else decided_at
