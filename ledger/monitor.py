"""Frozen local verification workspaces and immutable, linked review receipts.

No scheduler, provider request or model update is enabled by initialization.
Reviews are cache-only unless ``fetch=True`` is explicit. The local clock and
operator-controlled files are not independently attested. Policy time is never
backdated to make historical archives prospective.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from stateutil import atomic_write_json, exclusive_file_lock
from .db import connect
from .ingest import _archive_cohort, _run_evidence, ingest_file
from .verification import VerificationPolicy, build_verification, render_markdown


VERSION = "signal-verification-monitor-1"
REPORT_VERSION = "signal-verification-monitor-report-1"
ROOT = Path(__file__).resolve().parents[1]
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVES = 20_000
MAX_REPORT_BYTES = 128 * 1024 * 1024
ARCHIVE_SCHEMAS = frozenset(("2.0", "2.5", "2.6", "2.7", "2.8", "2.9"))
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REVIEW = re.compile(r"review-[0-9]{6}-[0-9a-f]{32}\.(json|md)\Z")


def _now():
    return datetime.now(timezone.utc)


def _instant(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("a timezone-qualified ISO timestamp is required") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("a timezone-qualified ISO timestamp is required")
    return parsed.astimezone(timezone.utc)


def _stamp(value):
    return _instant(value).isoformat().replace("+00:00", "Z")


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _sealed(value, field):
    return {**value, field: _sha(value)}


def _check_seal(value, field):
    if not isinstance(value, dict) or value.get(field) != _sha({k: v for k, v in value.items() if k != field}):
        raise ValueError(f"monitor {field} integrity check failed")


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _nonfinite(_):
    raise ValueError("nonfinite JSON number")


def _decode(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_nonfinite)


def _read(path, limit=MAX_REPORT_BYTES):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"missing or unsafe monitor file: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("monitor file exceeds its size limit")
    return raw


def _write_once(path, payload):
    """Commit bytes atomically without ever replacing an existing destination."""
    if not isinstance(payload, bytes):
        raise ValueError("immutable payload must be bytes")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _workspace(path):
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("monitor workspace must not be a symlink")
    root = candidate.resolve()
    if root == ROOT or root in ROOT.parents:
        raise ValueError("use a dedicated private monitor workspace")
    if ROOT in root.parents and root.relative_to(ROOT).parts[0] not in (
        "workspaces", "study-runs", "output", "review-results"
    ):
        raise ValueError("inside the repository, use an ignored private workspace directory")
    return root


def _source(path, workspace):
    source = Path(path).expanduser().resolve()
    if source == workspace or source in workspace.parents or workspace in source.parents:
        raise ValueError("archive source and monitor workspace must not overlap")
    return source


def _head(sequence, receipt_sha, manifest_sha):
    return _sealed({"schema_version": VERSION, "sequence": sequence,
                    "receipt_sha256": receipt_sha, "manifest_sha256": manifest_sha}, "head_sha256")


def _load(root):
    manifest = _decode(_read(root / "monitor.json"))
    _check_seal(manifest, "manifest_sha256")
    if manifest.get("schema_version") != VERSION:
        raise ValueError("unsupported monitor schema")
    policy = VerificationPolicy.from_dict(_decode(_read(root / "policy.json")))
    if policy.sha256 != manifest["policy_sha256"] or policy.to_dict() != manifest["policy"]:
        raise ValueError("frozen monitor policy was changed")
    frozen = _instant(manifest["frozen_at"])
    if _instant(policy.declared_at) < frozen:
        raise ValueError("frozen policy was backdated")
    for name in ("reports", "receipts", "inputs"):
        if (root / name).is_symlink():
            raise ValueError("monitor directories must not be symlinks")
    head = _decode(_read(root / "head.json"))
    _check_seal(head, "head_sha256")
    if head.get("manifest_sha256") != manifest["manifest_sha256"] or type(head.get("sequence")) is not int:
        raise ValueError("monitor head belongs to a different manifest")
    receipts, reports, referenced, accepted, source_paths = [], [], set(), {}, {}
    previous, previous_at, previous_cutoff = manifest["manifest_sha256"], frozen, frozen
    for path in sorted((root / "receipts").glob("*.json")):
        if path.name != f"{len(receipts) + 1:06d}.json":
            raise ValueError("monitor receipt sequence is missing or malformed")
        receipt = _decode(_read(path))
        _check_seal(receipt, "receipt_sha256")
        if (receipt.get("schema_version") != VERSION or receipt.get("sequence") != len(receipts) + 1
                or receipt.get("previous_receipt_sha256") != previous
                or receipt.get("manifest_sha256") != manifest["manifest_sha256"]
                or receipt.get("policy_sha256") != policy.sha256):
            raise ValueError("monitor receipt chain is inconsistent")
        at, cutoff = _instant(receipt["recorded_at"]), _instant(receipt["as_of"])
        if at < previous_at or cutoff < previous_cutoff or cutoff > at:
            raise ValueError("monitor receipt times are inconsistent")
        documents = {}
        for kind in ("json", "markdown"):
            filename = receipt[f"{kind}_file"]
            suffix = ".json" if kind == "json" else ".md"
            if not isinstance(filename, str) or not _REVIEW.fullmatch(filename) or not filename.endswith(suffix):
                raise ValueError("invalid monitor report reference")
            raw = _read(root / "reports" / filename)
            if hashlib.sha256(raw).hexdigest() != receipt[f"{kind}_bytes_sha256"]:
                raise ValueError("committed monitor report was modified")
            documents[kind] = raw
            referenced.add(filename)
        report = _decode(documents["json"])
        _check_seal(report, "report_sha256")
        if (report.get("sequence") != receipt["sequence"] or report.get("policy_sha256") != policy.sha256
                or report.get("manifest_sha256") != manifest["manifest_sha256"]
                or report.get("report_sha256") != receipt["report_sha256"]
                or report.get("as_of") != receipt["as_of"]):
            raise ValueError("monitor report and receipt disagree")
        for row in report["archive_inventory"]["records"]:
            if row["status"] in ("accepted", "duplicate"):
                accepted[row["sha256"]] = row
                if row.get("origin", "source_archive") == "source_archive":
                    key = (report["archive_inventory"]["source"], row["file"])
                    prior = source_paths.get(key)
                    if prior is not None and prior != row["sha256"]:
                        raise ValueError("committed source archive identity changed")
                    source_paths[key] = row["sha256"]
        receipts.append(receipt)
        reports.append(report)
        previous, previous_at, previous_cutoff = receipt["receipt_sha256"], at, cutoff
    chain = [manifest["manifest_sha256"], *[r["receipt_sha256"] for r in receipts]]
    if not 0 <= head["sequence"] < len(chain) or head["receipt_sha256"] != chain[head["sequence"]]:
        raise ValueError("monitor head points to a missing or altered receipt")
    for identity in accepted:
        if not isinstance(identity, str) or not _HASH.fullmatch(identity):
            raise ValueError("invalid retained archive identity")
        if hashlib.sha256(_read(root / "inputs" / f"{identity}.json", MAX_ARCHIVE_BYTES)).hexdigest() != identity:
            raise ValueError("retained monitor archive was modified")
    orphans = sorted(path.name for path in (root / "reports").iterdir()
                     if path.name not in referenced) if (root / "reports").exists() else []
    return {"manifest": manifest, "policy": policy, "head": head, "receipts": receipts,
            "reports": reports, "accepted": accepted, "orphans": orphans,
            "source_paths": source_paths}


def _status(root, state):
    last = state["reports"][-1] if state["reports"] else None
    manifest = state["manifest"]
    return {"schema_version": VERSION, "workspace": str(root),
            "status": last["status"] if last else "awaiting_archives",
            "frozen_at": manifest["frozen_at"], "policy": state["policy"].to_dict(),
            "policy_sha256": state["policy"].sha256, "archive_dir": manifest["archive_dir"],
            "min_observation_days": manifest["min_observation_days"],
            "review_count": len(state["receipts"]), "retained_archives": len(state["accepted"]),
            "latest_review_at": last["recorded_at"] if last else None,
            "latest_report": state["receipts"][-1]["json_file"] if last else None,
            "orphan_reports": state["orphans"],
            "head_recovery_pending": state["head"]["sequence"] < len(state["receipts"]),
            "independent_timestamp_attestation": False}


def freeze_monitor(workspace, policy, archive_dir, min_observation_days=28):
    """Freeze a new private study; earlier requested declarations move to now.

    Repeating identical initialization is idempotent. Changing an existing
    policy or minimum window is refused. Physical archive location can later
    be overridden on ``run_monitor`` without changing measurement identity.
    """
    root = _workspace(workspace)
    source = _source(archive_dir, root)
    requested = policy if isinstance(policy, VerificationPolicy) else VerificationPolicy.from_dict(policy)
    if type(min_observation_days) is not int or not 1 <= min_observation_days <= 3660:
        raise ValueError("min_observation_days must be an integer from 1 through 3660")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if (root / ".monitor.lock").is_symlink():
        raise ValueError("monitor lock must not be a symlink")
    with exclusive_file_lock(root / ".monitor.lock"):
        if (root / "monitor.json").exists():
            state = _load(root)
            manifest = state["manifest"]
            if (requested.sha256 not in (manifest["requested_policy_sha256"], manifest["policy_sha256"])
                    or min_observation_days != manifest["min_observation_days"]
                    or str(source) != manifest["archive_dir"]):
                raise ValueError("monitor is frozen; create a new workspace for changed assumptions")
            return _status(root, state)
        if any(path.name != ".monitor.lock" for path in root.iterdir()):
            raise ValueError("refusing a nonempty unmarked or incomplete monitor workspace")
        frozen = _now()
        effective = VerificationPolicy.from_dict({**requested.to_dict(),
            "declared_at": _stamp(max(frozen, _instant(requested.declared_at)))})
        manifest = _sealed({"schema_version": VERSION, "frozen_at": _stamp(frozen),
            "archive_dir": str(source), "min_observation_days": min_observation_days,
            "requested_policy_sha256": requested.sha256, "policy": effective.to_dict(),
            "policy_sha256": effective.sha256,
            "trust_boundary": "local clock and operator-controlled files; no independent attestation"}, "manifest_sha256")
        for name in ("reports", "receipts", "inputs"):
            (root / name).mkdir(mode=0o700)
        _write_once(root / "policy.json", _bytes(effective.to_dict()) + b"\n")
        _write_once(root / "head.json", _bytes(_head(0, manifest["manifest_sha256"], manifest["manifest_sha256"])) + b"\n")
        _write_once(root / "monitor.json", _bytes(manifest) + b"\n")
        return _status(root, _load(root))


def _inventory(root, source, *, cutoff, now, conn, previous, source_paths=None,
               retained=False):
    rows, accepted, encountered = [], dict(previous), set()
    if not source.exists():
        state, paths = "directory_missing", []
    elif not source.is_dir():
        state, paths = "not_a_directory", []
    else:
        state, paths = "available", []
        try:
            for path in source.rglob("*.json"):
                paths.append(path)
                if len(paths) > MAX_ARCHIVES:
                    raise ValueError("archive inventory exceeds its bounded file count")
            paths.sort()
        except OSError:
            state, paths = "directory_unreadable", []
    for path in paths:
        if retained and path.parent == source and path.stem in previous:
            continue
        record = {"file": ("inputs/" if retained else "") + str(path.relative_to(source)),
                  "status": "skipped", "reason": None,
                  "origin": "recovered_uncommitted_input" if retained else "source_archive"}
        try:
            if retained and (path.parent != source or not _HASH.fullmatch(path.stem)):
                raise ValueError("invalid_retained_archive_name")
            raw = _read(path, MAX_ARCHIVE_BYTES)
            identity = hashlib.sha256(raw).hexdigest()
            record["sha256"] = identity
            if retained and identity != path.stem:
                raise ValueError("retained_archive_identity_mismatch")
            prior = (source_paths or {}).get((str(source), record["file"]))
            if prior is not None and prior != identity:
                raise ValueError("archive_changed_after_capture")
            data = _decode(raw)
            if not isinstance(data, dict):
                raise ValueError("archive_not_object")
            if data.get("schema_version") not in ARCHIVE_SCHEMAS:
                raise ValueError("unsupported_archive_schema")
            generated = _instant(data.get("generated_at"))
            record["generated_at"] = _stamp(generated)
            evidence = _run_evidence(data, record["generated_at"], identity)
            if evidence["synthetic_status"] in ("declared_synthetic", "invalid_marker"):
                raise ValueError("synthetic_archive" if evidence["synthetic_status"] == "declared_synthetic"
                                 else "invalid_synthetic_marker")
            if generated > now:
                raise ValueError("future_archive")
            if generated > cutoff:
                raise ValueError("after_review_cutoff")
            health = data.get("health")
            status = health.get("status") if isinstance(health, dict) else None
            if status == "ERROR":
                raise ValueError("failed_archive")
            if status not in ("OK", "WARN"):
                raise ValueError("missing_or_invalid_health")
            if not isinstance(health.get("errors"), list):
                raise ValueError("missing_or_invalid_health_errors")
            if health.get("errors"):
                raise ValueError("failed_archive")
            stages = health.get("sources", {})
            stages = stages.get("pipeline_stages", {}) if isinstance(stages, dict) else {}
            if isinstance(stages, dict) and stages.get("failed"):
                raise ValueError("failed_archive")
            if not isinstance(data.get("sections"), dict):
                raise ValueError("missing_sections")
            era = data.get("pipeline_version")
            if not isinstance(era, str) or not era:
                raise ValueError("missing_pipeline_version")
            cohort = _archive_cohort(data, era)
            if cohort["status"] != "valid":
                raise ValueError(cohort["reason"] or "unusable_cohort")
            destination = root / "inputs" / f"{identity}.json"
            if destination.exists():
                if _read(destination, MAX_ARCHIVE_BYTES) != raw:
                    raise ValueError("retained_archive_identity_mismatch")
            else:
                _write_once(destination, raw)
            try:
                ingest_file(destination, conn)
            except Exception:
                conn.rollback()
                raise ValueError("archive_ingest_failed") from None
            record.update(status="duplicate" if identity in encountered else "accepted",
                          health=status, reason=None, run_id=identity)
            encountered.add(identity)
            accepted[identity] = record
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            record["reason"] = str(exc)[:300] or type(exc).__name__
        rows.append(record)
    return {"source": str(source), "state": state, "scanned": len(rows),
            "accepted_files": sum(row["status"] == "accepted" for row in rows),
            "duplicate_files": sum(row["status"] == "duplicate" for row in rows),
            "skipped_files": sum(row["status"] == "skipped" for row in rows),
            "skip_reasons": dict(sorted(Counter(row["reason"] for row in rows if row["status"] == "skipped").items())),
            "retained_unique_archives": len(accepted), "records": rows}, accepted


def _window(manifest, policy, accepted, cutoff):
    declared = _instant(policy.declared_at)
    dates = sorted({_instant(row["generated_at"]).date() for row in accepted.values()
                    if declared <= _instant(row["generated_at"]) <= cutoff})
    elapsed = max(0.0, (cutoff - _instant(manifest["frozen_at"])).total_seconds() / 86400)
    span = (dates[-1] - dates[0]).days + 1 if dates else 0
    return {"minimum_calendar_days": manifest["min_observation_days"],
            "elapsed_calendar_days": elapsed, "post_freeze_archive_dates": len(dates),
            "first_post_freeze_archive_date": dates[0].isoformat() if dates else None,
            "last_post_freeze_archive_date": dates[-1].isoformat() if dates else None,
            "observed_calendar_span_days": span,
            "minimum_window_elapsed": elapsed >= manifest["min_observation_days"],
            "archive_span_reached": span >= manifest["min_observation_days"],
            "statistical_power_established": False}


def _markdown(report):
    inventory, window = report["archive_inventory"], report["observation_window"]
    lines = ["# Frozen verification review", "", f"Status: **{report['status']}**.", "",
             f"Policy: {report['policy']['policy_id']}. Outcome cutoff: {report['as_of']}.",
             f"Retained {inventory['retained_unique_archives']} unique usable archives; "
             f"skipped {inventory['skipped_files']} current files. Source state: {inventory['state']}.",
             f"Observed {window['post_freeze_archive_dates']} post-freeze archive dates spanning "
             f"{window['observed_calendar_span_days']} calendar days. Minimum window: "
             f"{window['minimum_calendar_days']} calendar days.", "",
             "Elapsed time and archive counts are operational evidence, not proof of predictive skill. "
             "This workspace does not independently attest timestamps.", ""]
    if inventory["skip_reasons"]:
        lines += ["## Archive exclusions", ""]
        lines += [f"- {reason}: {count}" for reason, count in inventory["skip_reasons"].items()]
        lines.append("")
    if report["verification"] is not None:
        lines.append(render_markdown(report["verification"]))
    else:
        lines += ["No verification result was produced in this review. See the explicit status and errors.", ""]
    return "\n".join(lines)


def run_monitor(workspace, fetch=False, as_of=None, archive_dir=None):
    """Ingest usable archives and append a review; source relocation is allowed.

    A failed evaluation is recorded as a failed review. A crash before receipt
    commit can leave orphan report files; these are preserved, disclosed and
    never reused or overwritten. Tampered committed artifacts stop the review.
    """
    if type(fetch) is not bool:
        raise ValueError("fetch must be explicitly boolean")
    root = _workspace(workspace)
    if not (root / "monitor.json").is_file():
        raise FileNotFoundError("freeze a monitor workspace before running it")
    if (root / ".monitor.lock").is_symlink():
        raise ValueError("monitor lock must not be a symlink")
    with exclusive_file_lock(root / ".monitor.lock"):
        state = _load(root)
        manifest, policy = state["manifest"], state["policy"]
        started = _now()
        if state["receipts"] and started < _instant(state["receipts"][-1]["recorded_at"]):
            raise ValueError("monitor clock moved backward since the previous review")
        cutoff = _instant(as_of) if as_of is not None else started
        if cutoff > started or cutoff < _instant(manifest["frozen_at"]):
            raise ValueError("review cutoff must be between actual freeze time and now")
        if state["reports"] and cutoff < _instant(state["reports"][-1]["as_of"]):
            raise ValueError("monitor review cutoffs cannot move backward")
        source = _source(archive_dir if archive_dir is not None else manifest["archive_dir"], root)
        database = root / "ledger.db"
        if database.is_symlink():
            raise ValueError("monitor database must not be a symlink")
        conn = connect(database)
        try:
            for identity in state["accepted"]:
                ingest_file(root / "inputs" / f"{identity}.json", conn)
            inventory, accepted = _inventory(root, source, cutoff=cutoff, now=started,
                                              conn=conn, previous=state["accepted"],
                                              source_paths=state["source_paths"])
            # A crash can occur either before or after input ingestion, before
            # its receipt commits. Recover from all unregistered retained bytes,
            # not just rows that happened to reach SQLite. Every candidate must
            # pass the same archive checks plus its content-addressed filename;
            # failed recovery candidates remain visible as exclusions.
            recovered, accepted = _inventory(root, root / "inputs", cutoff=cutoff,
                                              now=started, conn=conn, previous=accepted,
                                              retained=True)
            inventory["records"].extend(recovered["records"])
            for count in ("scanned", "accepted_files", "duplicate_files", "skipped_files"):
                inventory[count] += recovered[count]
            inventory["skip_reasons"] = dict(sorted((Counter(inventory["skip_reasons"])
                                                     + Counter(recovered["skip_reasons"])).items()))
            inventory["recovered_uncommitted_archives"] = recovered["accepted_files"]
            inventory["retained_unique_archives"] = len(accepted)
            actual_runs = {row["run_id"]: row["brief_sha256"]
                           for row in conn.execute("SELECT run_id,brief_sha256 FROM runs")}
            if actual_runs != {identity: identity for identity in accepted}:
                raise ValueError("monitor ledger contains unregistered or altered runs")
        finally:
            conn.close()
        errors, verification = [], None
        if _instant(policy.declared_at) > cutoff:
            status = "awaiting_declaration"
        else:
            try:
                verification = build_verification(database, policy=policy, as_of=as_of, fetch=fetch)
                cutoff = _instant(verification["as_of"])
                status = "collecting"
            except Exception as exc:
                errors.append({"stage": "verification", "error_type": type(exc).__name__})
                status = "failed"
        window = _window(manifest, policy, accepted, cutoff)
        if status == "collecting":
            if not accepted:
                status = "awaiting_archives"
            elif not window["post_freeze_archive_dates"]:
                status = "historical_only"
            elif window["minimum_window_elapsed"] and window["archive_span_reached"]:
                prospective = [row for row in verification["observations"]
                               if row["policy_timing"] == "predeclared_policy" and row["status"] == "observed"]
                status = "ready_for_review" if prospective else "awaiting_outcomes"
        recorded = _now()
        if recorded < started or recorded < cutoff:
            raise ValueError("monitor clock moved backward during the review")
        sequence = len(state["receipts"]) + 1
        report = _sealed({"schema_version": REPORT_VERSION, "sequence": sequence,
            "manifest_sha256": manifest["manifest_sha256"], "policy": policy.to_dict(),
            "policy_sha256": policy.sha256, "started_at": _stamp(started),
            "recorded_at": _stamp(recorded), "as_of": _stamp(cutoff), "fetch_enabled": fetch,
            "status": status, "archive_inventory": inventory, "observation_window": window,
            "verification": verification, "errors": errors, "orphan_reports": state["orphans"],
            "independent_timestamp_attestation": False,
            "predictive_accuracy_established": False}, "report_sha256")
        stem = f"review-{sequence:06d}-{uuid4().hex}"
        json_name, markdown_name = stem + ".json", stem + ".md"
        payload, markdown = _bytes(report) + b"\n", _markdown(report).encode("utf-8")
        if len(payload) > MAX_REPORT_BYTES or len(markdown) > MAX_REPORT_BYTES:
            raise ValueError("monitor report exceeds the supported snapshot size")
        _write_once(root / "reports" / json_name, payload)
        _write_once(root / "reports" / markdown_name, markdown)
        previous = state["receipts"][-1]["receipt_sha256"] if state["receipts"] else manifest["manifest_sha256"]
        receipt = _sealed({"schema_version": VERSION, "sequence": sequence,
            "manifest_sha256": manifest["manifest_sha256"], "policy_sha256": policy.sha256,
            "previous_receipt_sha256": previous, "recorded_at": _stamp(recorded),
            "as_of": _stamp(cutoff), "archive_dir": str(source), "report_sha256": report["report_sha256"],
            "json_file": json_name, "markdown_file": markdown_name,
            "json_bytes_sha256": hashlib.sha256(payload).hexdigest(),
            "markdown_bytes_sha256": hashlib.sha256(markdown).hexdigest()}, "receipt_sha256")
        _write_once(root / "receipts" / f"{sequence:06d}.json", _bytes(receipt) + b"\n")
        atomic_write_json(root / "head.json", _head(sequence, receipt["receipt_sha256"], manifest["manifest_sha256"]))
        return {"status": status, "report": report, "receipt": receipt,
                "paths": {"json": str(root / "reports" / json_name),
                          "markdown": str(root / "reports" / markdown_name),
                          "receipt": str(root / "receipts" / f"{sequence:06d}.json")}}


def monitor_status(workspace):
    """Verify every committed report/receipt and return a compact status."""
    root = _workspace(workspace)
    if not (root / "monitor.json").is_file():
        raise FileNotFoundError("monitor workspace does not exist")
    if (root / ".monitor.lock").is_symlink():
        raise ValueError("monitor lock must not be a symlink")
    with exclusive_file_lock(root / ".monitor.lock"):
        return _status(root, _load(root))
