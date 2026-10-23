"""Local, append-only records of supplied paper-research decisions.

This journal does not execute orders, authenticate an operator, or notarize a
clock. Normal recording uses the computer's current UTC time. ``now`` exists
for deterministic tests; it is not a CLI option. Historical hypotheses belong
in retrospective input files, never in a backdated paper journal.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

from stateutil import atomic_write_json, exclusive_file_lock

from .data import canonical, digest, load_json, utc, validate_decisions, validate_study


VERSION = "portfolio-lab.journal.v1"
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_SCHEMAS = frozenset(("2.6", "2.7", "2.8", "2.9"))
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVES = 64
DATABASE = "journal.sqlite"
LOCK = ".portfolio.lock"

_SCHEMA = """
CREATE TABLE workspace (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version TEXT NOT NULL,
    study_json TEXT NOT NULL,
    study_sha256 TEXT NOT NULL,
    frozen_at TEXT NOT NULL,
    genesis_sha256 TEXT NOT NULL
);
CREATE TABLE decisions (
    sequence INTEGER PRIMARY KEY,
    decision_id TEXT NOT NULL UNIQUE,
    request_json TEXT NOT NULL,
    decision_json TEXT NOT NULL,
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL UNIQUE
);
CREATE TRIGGER workspace_no_update BEFORE UPDATE ON workspace
BEGIN SELECT RAISE(ABORT, 'workspace is immutable'); END;
CREATE TRIGGER workspace_no_delete BEFORE DELETE ON workspace
BEGIN SELECT RAISE(ABORT, 'workspace is immutable'); END;
CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decisions are append-only'); END;
"""


def _utc_now():
    return datetime.now(timezone.utc)


def _stamp(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("recording time requires an explicit timezone")
        value = value.astimezone(timezone.utc)
    else:
        if not isinstance(value, str):
            raise ValueError("timestamp must be a string with an explicit timezone")
        value = utc(value)
    return value.isoformat().replace("+00:00", "Z")


def _workspace_path(path):
    root = Path(path).expanduser().resolve()
    if root == ROOT or root in ROOT.parents:
        raise ValueError("Use a dedicated private workspace, not the repository or its parents")
    if ROOT in root.parents:
        relative = root.relative_to(ROOT)
        if relative.parts[0] not in ("workspaces", "study-runs", "output", "review-results"):
            raise ValueError("Inside the repository, paper data must use an ignored private workspace directory")
    return root


def _text(value):
    return canonical(value).decode("utf-8")


@contextmanager
def _connect(root, *, writable=False):
    path = root / DATABASE
    if path.is_symlink():
        raise ValueError("journal database must not be a symlink")
    if not path.is_file():
        raise ValueError("paper workspace is incomplete: journal database is missing")
    uri = path.as_uri() + ("?mode=rw" if writable else "?mode=ro")
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _snapshot(root, conn):
    """Validate the pinned specification and every receipt before use."""
    study_path = root / "study.json"
    if study_path.is_symlink() or not study_path.is_file():
        raise ValueError("paper workspace requires its original study.json")
    study = validate_study(load_json(study_path))
    row = conn.execute("SELECT * FROM workspace WHERE singleton=1").fetchone()
    if row is None or row["version"] != VERSION:
        raise ValueError("unsupported or incomplete paper workspace")
    if row["study_sha256"] != digest(study) or row["study_json"] != _text(study):
        raise ValueError("workspace study changed; create a new workspace")
    genesis = {"version": VERSION, "study_sha256": digest(study),
               "frozen_at": row["frozen_at"]}
    if digest(genesis) != row["genesis_sha256"]:
        raise ValueError("workspace identity or freeze time was modified")
    frozen = utc(row["frozen_at"])
    decisions, receipts = [], []
    previous = row["genesis_sha256"]
    previous_time = frozen
    for number, entry in enumerate(conn.execute("SELECT * FROM decisions ORDER BY sequence"), 1):
        decision = json.loads(entry["decision_json"])
        receipt = json.loads(entry["receipt_json"])
        request = json.loads(entry["request_json"])
        if (entry["sequence"] != number or receipt.get("sequence") != number
                or decision.get("decision_id") != entry["decision_id"]
                or receipt.get("decision_id") != entry["decision_id"]
                or receipt.get("previous_sha256") != previous
                or receipt.get("study_sha256") != row["study_sha256"]
                or receipt.get("decision_sha256") != digest(decision)
                or receipt.get("request_sha256") != digest(request)
                or digest(receipt) != entry["receipt_sha256"]):
            raise ValueError("paper journal receipt chain is inconsistent")
        recorded = utc(decision["recorded_at"])
        if (recorded < previous_time or decision.get("origin") != "paper"
                or decision.get("decided_at") != decision.get("recorded_at")
                or receipt.get("recorded_at") != decision.get("recorded_at")):
            raise ValueError("paper journal recording times are inconsistent")
        validate_decisions([decision], study)
        decisions.append(decision)
        receipts.append({**receipt, "receipt_sha256": entry["receipt_sha256"]})
        previous = entry["receipt_sha256"]
        previous_time = recorded
    return {"schema_version": VERSION, "study": study,
            "study_sha256": row["study_sha256"], "frozen_at": row["frozen_at"],
            "genesis_sha256": row["genesis_sha256"],
            "decisions": decisions, "receipts": receipts,
            "trust_boundary": "local computer clock and operator-controlled files; no independent notarization",
            "research_only": True, "broker_execution": False}


def create_workspace(path, study):
    """Freeze a supplied study in an empty, dedicated local workspace.

    Identical initialization is idempotent. A changed specification or a
    nonempty directory without the journal's marker is refused.
    """
    validate_study(study)
    # Take an immutable JSON copy before any filesystem mutation.
    study = json.loads(_text(study))
    root = _workspace_path(path)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with exclusive_file_lock(root / LOCK):
        present = {entry.name for entry in root.iterdir()} - {LOCK}
        if present:
            if not {"study.json", DATABASE}.issubset(present):
                raise ValueError("Refusing a nonempty unmarked or incomplete workspace")
            with _connect(root) as conn:
                result = _snapshot(root, conn)
            if result["study_sha256"] != digest(study):
                raise ValueError("workspace study differs; create a new workspace")
            return result
        frozen = _stamp(_utc_now())
        genesis = {"version": VERSION, "study_sha256": digest(study), "frozen_at": frozen}
        db_path = root / DATABASE
        conn = sqlite3.connect(db_path)
        try:
            db_path.chmod(0o600)
            conn.executescript(_SCHEMA)
            conn.execute("INSERT INTO workspace VALUES(1,?,?,?,?,?)",
                         (VERSION, _text(study), digest(study), frozen, digest(genesis)))
            conn.commit()
            atomic_write_json(root / "study.json", study, indent=2)
        except BaseException:
            conn.close()
            # Only remove the new files this call owns. No user data existed.
            for name in (DATABASE, DATABASE + "-journal", "study.json"):
                (root / name).unlink(missing_ok=True)
            raise
        else:
            conn.close()
        with _connect(root) as conn:
            return _snapshot(root, conn)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("archive JSON contains duplicate keys")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("archive JSON contains a nonfinite number")


def _archive(path):
    """Hash and inspect the very same bytes; paths never enter the journal."""
    import hashlib

    with Path(path).open("rb") as handle:
        raw = handle.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("source archive exceeds the journal size limit")
    try:
        data = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("source archive is not valid JSON") from exc
    if (not isinstance(data, dict) or not isinstance(data.get("schema_version"), str)
            or data["schema_version"] not in ARCHIVE_SCHEMAS):
        raise ValueError("source archive has an unsupported brief schema")
    generated_at = _stamp(data.get("generated_at"))
    health = data.get("health")
    if not isinstance(health, dict) or health.get("status") not in ("OK", "WARN"):
        raise ValueError("source archive health must be OK or WARN; ERROR is refused")
    return {"archive_sha256": hashlib.sha256(raw).hexdigest(),
            "generated_at": generated_at,
            "health_status": health["status"]}


def record_decision(path, decision, archive_paths=(), now=None):
    """Append a supplied hypothesis with the current recording time.

    ``decided_at`` and ``information_cutoff`` may describe the earlier request;
    the stored paper decision is made no earlier than this recording. Source
    evidence is read locally and first known here at recording, not retrospectively
    at its publication time. Nonempty caller-supplied evidence and recording times
    are refused; supply actual archive paths instead. Repeating the same ID and
    exact request/archive bytes returns the original decision and receipt.
    """
    if not isinstance(decision, dict):
        raise ValueError("paper decision must be an object")
    if "recorded_at" in decision:
        raise ValueError("recorded_at is set by the journal, not by a caller")
    if decision.get("origin", "paper") != "paper":
        raise ValueError("historical decisions belong in retrospective inputs")
    if decision.get("evidence", []) != []:
        raise ValueError("supply local archive paths instead of asserted paper evidence")
    archive_paths = tuple(archive_paths)
    if len(archive_paths) > MAX_ARCHIVES:
        raise ValueError("too many source archives for one decision")
    root = _workspace_path(path)
    if not root.is_dir():
        raise ValueError("initialize the paper workspace first")
    request = json.loads(_text(decision))
    evidence = [_archive(source) for source in archive_paths]
    evidence.sort(key=lambda item: item["archive_sha256"])
    if len({item["archive_sha256"] for item in evidence}) != len(evidence):
        raise ValueError("the same source archive was supplied more than once")
    # Omitted time fields remain omitted in request identity so retries made
    # later do not accidentally become different requests. Identity is content,
    # not a path or the incidental time the retry reached this machine.
    request_identity = {"decision": request, "archive_sha256":
                        [item["archive_sha256"] for item in evidence]}
    with exclusive_file_lock(root / LOCK):
        with _connect(root, writable=True) as conn:
            snapshot = _snapshot(root, conn)
            # Acquire/read/validate first. Clocking at function entry would give
            # a slow archive read or lock wait credit before its completion.
            recorded_at = _stamp(_utc_now() if now is None else now)
            requested_at = _stamp(request.get("decided_at", recorded_at))
            cutoff = _stamp(request.get("information_cutoff", requested_at))
            if utc(requested_at) > utc(recorded_at) or utc(cutoff) > utc(requested_at):
                raise ValueError("decision/cutoff cannot be in the future or reversed")
            for item in evidence:
                if utc(item["generated_at"]) > utc(recorded_at):
                    raise ValueError("source archive is future-dated")
                item["known_at"] = recorded_at
            latest = snapshot["receipts"][-1] if snapshot["receipts"] else None
            minimum = latest["recorded_at"] if latest else snapshot["frozen_at"]
            if utc(recorded_at) < utc(minimum):
                raise ValueError("recording clock precedes the study freeze or previous decision")
            stored = {**request, "origin": "paper", "decided_at": recorded_at,
                      "recorded_at": recorded_at,
                      "information_cutoff": recorded_at if evidence else cutoff,
                      "evidence": evidence}
            validate_decisions([stored], snapshot["study"])
            old = conn.execute("SELECT * FROM decisions WHERE decision_id=?",
                               (request.get("decision_id"),)).fetchone()
            if old is not None:
                if old["request_json"] != _text(request_identity):
                    raise ValueError("decision ID already exists with different content")
                return {"decision": json.loads(old["decision_json"]),
                        "receipt": {**json.loads(old["receipt_json"]),
                                    "receipt_sha256": old["receipt_sha256"]},
                        "already_recorded": True}
            receipt = {"schema_version": VERSION,
                       "sequence": len(snapshot["decisions"]) + 1,
                       "decision_id": stored["decision_id"],
                       "study_sha256": snapshot["study_sha256"],
                       "request_sha256": digest(request_identity),
                       "decision_sha256": digest(stored),
                       "previous_sha256": latest["receipt_sha256"] if latest else snapshot["genesis_sha256"],
                       "recorded_at": recorded_at,
                       "requested_decided_at": requested_at,
                       "requested_information_cutoff": cutoff,
                       "archive_checks": "exact-byte identity, supported schema, clock and top-level health only",
                       "synthetic_or_source_truth_verified": False}
            receipt_sha = digest(receipt)
            conn.execute("INSERT INTO decisions VALUES(?,?,?,?,?,?)",
                         (receipt["sequence"], stored["decision_id"], _text(request_identity),
                          _text(stored), _text(receipt), receipt_sha))
            return {"decision": stored, "receipt": {**receipt, "receipt_sha256": receipt_sha},
                    "already_recorded": False}


def read_journal(path):
    """Read and validate the journal without creating or rewriting any files."""
    root = _workspace_path(path)
    with _connect(root) as conn:
        return _snapshot(root, conn)
