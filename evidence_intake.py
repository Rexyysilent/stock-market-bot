"""OMNI-02 local metadata intake. No network, model, body store or signal writer."""
from collections import Counter, defaultdict, deque
from copy import deepcopy
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from coverage_policy import coverage_policy_manifest, source_use_decision

VERSION = "omni02-metadata-1"
MAX_RECORDS = 2000
MAX_BYTES = 2_000_000
MAX_TEXT = 4096
SOURCES = {"Official Feeds": "official_feeds", "FMP": "fmp",
           "Alpha Vantage News": "alpha_vantage", "GDELT": "gdelt",
           "Google News": "google_news"}
FIELDS = ("title", "link", "canonical_url", "published", "source_time_kind",
          "provider_seen_at", "provider", "publisher", "publisher_domain",
          "source_class", "source_record_id", "tickers", "ticker_metadata_kind")
BODY_USES = ("raw_storage", "model_processing", "commercial_redistribution",
             "excerpt_redistribution")
SENSITIVE = re.compile(r"(?i)(key|token|secret|password|signature|credential|authorization)")
TERMINALS = ("selected", "relevance_dropped", "stale_dropped",
             "duplicate_dropped", "cap_dropped")


def selection_signature(rows):
    return digest([{k: row.get(k) for k in (
        "source_record_id", "title", "link", "canonical_url", "as_of",
        "lane", "score_components", "universe_tickers", "provider",
    )} for row in rows])


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _url(value):
    if value is None:
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return None
        query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                 if not SENSITIVE.search(k)]
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))
    except (TypeError, ValueError):
        return None


def _metadata(row):
    if not isinstance(row, dict):
        return None, ["malformed_record"]
    output, transformations = {}, []
    for field in FIELDS:
        if field not in row:
            continue
        value = row[field]
        if field == "tickers":
            if not isinstance(value, list) or len(value) > 100 or any(
                    not isinstance(t, str) or len(t) > 32 for t in value):
                return None, ["malformed_metadata"]
            output[field] = list(value)
        elif value is not None and (not isinstance(value, str) or len(value) > MAX_TEXT):
            return None, ["metadata_size_or_type_limit"]
        else:
            output[field] = value
        if field in ("link", "canonical_url"):
            cleaned = _url(value)
            if cleaned != value:
                transformations.append(field + "_sanitized")
            output[field] = cleaned
    if not isinstance(output.get("title"), str) or not output["title"].strip():
        return None, ["missing_title"]
    return output, transformations


def _publication(meta):
    raw = meta.get("published")
    if meta.get("source_time_kind") != "published" or not raw:
        return {"published_at": None, "published_date": None, "precision": "unknown"}
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        try:
            date.fromisoformat(raw)
            return {"published_at": None, "published_date": raw, "precision": "date"}
        except ValueError:
            pass
    try:
        try:
            stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            stamp = parsedate_to_datetime(raw)
        if stamp.tzinfo is not None:
            return {"published_at": stamp.astimezone(timezone.utc).isoformat(),
                    "published_date": None, "precision": "timestamp"}
    except (TypeError, ValueError, OverflowError):
        pass
    return {"published_at": None, "published_date": None, "precision": "unknown"}


def _selector_hash():
    return hashlib.sha256((Path(__file__).parent / "agents/news_agent.py").read_bytes()).hexdigest()


def _safe_fingerprint(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def capture(results, now, coverage_mode):
    """Copy only allowed metadata BEFORE selection. Summary/body text is omitted."""
    stamp = now.astimezone(timezone.utc).isoformat()
    manifest = {
        "version": VERSION, "mode": "shadow", "status": "ok",
        "evaluation_at": stamp, "captured_at": datetime.now(timezone.utc).isoformat(),
        "first_observed_at": None, "first_observed_scope": "not_established",
        "coverage_mode": coverage_mode, "selector_hash": _selector_hash(),
        "policy_fingerprint": coverage_policy_manifest(coverage_mode)["fingerprint"],
        "storage": "existing_local_metadata_only", "event_classification": "not_performed",
        "signal_eligible": False, "providers": [], "records": [],
        "omitted_reasons": {}, "replay_status": "complete",
    }
    omitted, byte_count, occurrence = Counter(), 0, 0
    for result in results:
        source = SOURCES.get(result.name, "unknown")
        approved = source_use_decision(source, "existing_local_metadata")["allowed"]
        # Batched adapters may expose extra occurrences suppressed before ranking.
        rows = result.intake_records if getattr(result, "intake_records", None) is not None else result.records
        metadata = result.metadata or {}
        gaps = []
        if result.status == "error" or metadata.get("partial_failures"):
            gaps.append("source_failure")
        if metadata.get("coverage") == "fmp_articles":
            gaps.append("provider_entitlement_gap")
        if metadata.get("result_limit_reached"):
            gaps.append("possible_result_cap_truncation")
        parser_drops = metadata.get("malformed_record_count")
        if isinstance(parser_drops, int) and parser_drops > 0:
            gaps.append("malformed_or_unusable_record")
        manifest["providers"].append({
            "name": str(result.name)[:120], "source_policy_id": source,
            "status": result.status, "returned_count": len(rows),
            "ranking_input_count": len(result.records),
            "metadata_storage_allowed": approved,
            "parser_drop_count": parser_drops if isinstance(parser_drops, int) else None,
            "result_limit": metadata.get("result_limit") if isinstance(metadata.get("result_limit"), int) else None,
            "gaps": gaps, "watermark": None, "source_interval": None,
            "reconciliation": "deferred_no_additional_requests" if gaps else "not_established",
            "complete_source_coverage": False,
            "request_scope_fingerprint": _safe_fingerprint(metadata.get("request_scope_fingerprint")),
        })
        ranked = Counter(_key(r) for r in result.records)
        for raw in rows:
            occurrence += 1
            if not approved:
                omitted["source_policy_denied"] += 1
                continue
            meta, transformations = _metadata(raw)
            if meta is None:
                omitted[transformations[0]] += 1
                continue
            size = len(json.dumps(meta, ensure_ascii=False).encode())
            if len(manifest["records"]) >= MAX_RECORDS or byte_count + size > MAX_BYTES:
                omitted["intake_budget_exceeded"] += 1
                continue
            byte_count += size
            identity = [source, meta.get("source_record_id") or meta.get("canonical_url")
                        or [meta.get("title"), meta.get("published")]]
            revision = {k: v for k, v in meta.items() if k != "provider_seen_at"}
            canonical = meta.get("canonical_url") or meta.get("link")
            eid = "evidence:" + digest(identity)
            key = _key(raw)
            ranking_input = ranked[key] > 0
            if ranking_input:
                ranked[key] -= 1
            manifest["records"].append({
                "occurrence": occurrence, "evidence_id": eid,
                "source_policy_id": source,
                "revision_id": digest(revision),
                "origin_cluster_id": "origin:" + digest(canonical or identity),
                "origin_basis": "same_canonical_reference" if canonical else "unresolved",
                "origin_verified_as_event": False,
                "directness": ("official_adapter" if source == "official_feeds" else
                               "discovery" if source in ("google_news", "gdelt") else "aggregated"),
                "publication": _publication(meta), "original_issuer": None,
                "content_availability": ("truncated_or_blocked" if raw.get("content_truncated") or raw.get("blocked")
                                         else "metadata_with_unretained_summary" if raw.get("summary")
                                         else "headline_metadata_only"),
                "body_complete": False, "retrieved_content_hash": None,
                "raw_storage_pointer": None, "rights_status": "unknown",
                "uses": {purpose: False for purpose in BODY_USES},
                "signal_eligible": False, "review_status": "unreviewed",
                "ranking_input": ranking_input,
                "transformations": transformations, "metadata": meta,
                "decision": None,
            })
    manifest["input_occurrence_count"] = occurrence
    manifest["omitted_reasons"] = dict(omitted)
    if omitted or any(r["transformations"] for r in manifest["records"]):
        manifest["replay_status"] = "incomplete"
    return manifest


def _key(row):
    return tuple(row.get(k) for k in ("source_record_id", "title", "link", "provider", "published"))


def finish(manifest, processed, google_requested=False):
    """Attach observed decisions without promoting any retained candidate."""
    result = deepcopy(manifest)
    terminals = defaultdict(deque)
    for name in TERMINALS:
        for row in processed[name]:
            terminals[_key(row)].append({
                "stage": name,
                "reason": row.get("drop_reason") or ("selected" if name == "selected" else "unknown"),
                "lane": row.get("lane"),
            })
    for row in result["records"]:
        matches = terminals[_key(row["metadata"])]
        if not row["ranking_input"]:
            row["decision"] = {"stage": "adapter", "reason": "adapter_duplicate", "lane": None}
        elif matches:
            row["decision"] = matches.popleft()
        else:
            row["decision"] = {"stage": "unresolved", "reason": "terminal_identity_unresolved", "lane": None}
            result["replay_status"] = "incomplete"
    result["google_requested"] = bool(google_requested)
    result["google_scope"] = "legacy_conditional_acquisition"
    result["loss_counts"] = dict(Counter(row["decision"]["reason"] for row in result["records"]))
    result["retained_count"] = len(result["records"])
    result["selection_signature"] = selection_signature(processed["selected"])
    result["manifest_hash"] = digest(result)
    return result


def validate_manifest(manifest):
    try:
        return _validate_manifest(manifest)
    # Validation is a trust boundary for replay files.  Malformed containers
    # (for example a list where an object is required) must produce a stable
    # validation error rather than leaking AttributeError to the caller.
    except (AttributeError, TypeError, ValueError, KeyError, OverflowError):
        return ["invalid evidence-intake shape"]


def _validate_manifest(manifest):
    """Fail closed on corrupted replay data, permission claims or unknown versions."""
    errors = []
    if not isinstance(manifest, dict) or manifest.get("version") != VERSION:
        return ["unsupported evidence-intake version"]
    if manifest.get("mode") != "shadow" or manifest.get("signal_eligible") is not False:
        errors.append("intake must remain shadow and signal-ineligible")
    if manifest.get("status") == "error":
        if set(manifest) != {"version", "mode", "status", "signal_eligible", "reason"}:
            errors.append("invalid failed-intake diagnostic")
        return errors
    if manifest.get("status") != "ok":
        errors.append("invalid intake status")
    if (manifest.get("storage") != "existing_local_metadata_only"
            or manifest.get("event_classification") != "not_performed"
            or manifest.get("first_observed_scope") != "not_established"
            or manifest.get("first_observed_at") is not None):
        errors.append("invalid intake scope")
    rows = manifest.get("records")
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        return errors + ["invalid evidence record count"]
    if manifest.get("manifest_hash") != digest({k: v for k, v in manifest.items() if k != "manifest_hash"}):
        errors.append("manifest hash mismatch")
    occurrences = set()
    for row in rows:
        if not isinstance(row, dict):
            errors.append("invalid evidence row")
            continue
        meta = row.get("metadata")
        if set(row) != {
            "occurrence", "evidence_id", "source_policy_id", "revision_id", "origin_cluster_id", "origin_basis",
            "origin_verified_as_event", "directness", "publication", "original_issuer",
            "content_availability", "body_complete", "retrieved_content_hash",
            "raw_storage_pointer", "rights_status", "uses", "signal_eligible",
            "review_status", "ranking_input", "transformations", "metadata", "decision",
        }:
            errors.append("unexpected evidence fields")
        if not isinstance(meta, dict) or set(meta) - set(FIELDS) or _metadata(meta) != (meta, []):
            errors.append("invalid or unpermitted metadata")
            continue
        if row.get("occurrence") in occurrences:
            errors.append("duplicate occurrence")
        if type(row.get("occurrence")) is not int or row["occurrence"] < 1:
            errors.append("invalid occurrence")
        occurrences.add(row.get("occurrence"))
        if not source_use_decision(row.get("source_policy_id"), "existing_local_metadata")["allowed"]:
            errors.append("source metadata purpose not approved")
        if row.get("origin_verified_as_event") is not False or row.get("original_issuer") is not None:
            errors.append("intake cannot establish an event or issuer claim")
        identity = [row.get("source_policy_id"), meta.get("source_record_id") or meta.get("canonical_url")
                    or [meta.get("title"), meta.get("published")]]
        if row.get("evidence_id") != "evidence:" + digest(identity):
            errors.append("evidence identity mismatch")
        canonical = meta.get("canonical_url") or meta.get("link")
        if row.get("origin_cluster_id") != "origin:" + digest(canonical or identity):
            errors.append("origin reference mismatch")
        if row.get("publication") != _publication(meta):
            errors.append("publication precision mismatch")
        if row.get("revision_id") != digest({k: v for k, v in meta.items() if k != "provider_seen_at"}):
            errors.append("revision hash mismatch")
        if (row.get("signal_eligible") is not False or row.get("review_status") != "unreviewed"
                or row.get("uses") != {purpose: False for purpose in BODY_USES}
                or row.get("raw_storage_pointer") is not None
                or row.get("retrieved_content_hash") is not None or row.get("body_complete") is not False):
            errors.append("unapproved evidence use or content claim")
    if manifest.get("retained_count") != len(rows):
        errors.append("retained count mismatch")
    if sum(len(json.dumps(r["metadata"], ensure_ascii=False).encode()) for r in rows) > MAX_BYTES:
        errors.append("metadata budget exceeded")
    if any(type(v) is not int or v < 0 for v in manifest.get("omitted_reasons", {}).values()):
        errors.append("invalid omission counter")
    if set(manifest) != {
        "version", "mode", "status", "evaluation_at", "captured_at",
        "first_observed_at", "first_observed_scope",
        "coverage_mode", "selector_hash", "policy_fingerprint", "storage",
        "event_classification", "signal_eligible", "providers", "records",
        "omitted_reasons", "replay_status", "input_occurrence_count",
        "google_requested", "google_scope", "loss_counts", "retained_count",
        "selection_signature", "manifest_hash",
    }:
        errors.append("unexpected intake fields")
    if manifest.get("coverage_mode") not in ("off", "shadow", "active"):
        errors.append("invalid coverage mode")
    if manifest.get("replay_status") not in ("complete", "incomplete"):
        errors.append("invalid replay status")
    if manifest.get("loss_counts") != dict(Counter(r["decision"]["reason"] for r in rows)):
        errors.append("loss counts mismatch")
    if manifest.get("input_occurrence_count") != len(rows) + sum(manifest.get("omitted_reasons", {}).values()):
        errors.append("intake accounting mismatch")
    if not isinstance(manifest.get("selection_signature"), str) or len(manifest["selection_signature"]) != 64:
        errors.append("missing selection signature")
    providers = manifest.get("providers")
    if not isinstance(providers, list):
        return errors + ["missing provider coverage"]
    for provider in providers:
        if set(provider) != {
            "name", "source_policy_id", "status", "returned_count", "ranking_input_count",
            "metadata_storage_allowed", "parser_drop_count", "result_limit", "gaps",
            "watermark", "source_interval", "reconciliation", "complete_source_coverage",
            "request_scope_fingerprint",
        }:
            errors.append("unexpected provider coverage fields")
        if provider.get("complete_source_coverage") is not False:
            errors.append("complete source coverage is not established")
        if provider.get("metadata_storage_allowed") != source_use_decision(
                provider.get("source_policy_id"), "existing_local_metadata")["allowed"]:
            errors.append("provider storage permission mismatch")
        if provider.get("request_scope_fingerprint") != _safe_fingerprint(provider.get("request_scope_fingerprint")):
            errors.append("invalid acquisition fingerprint")
    if sum(p["returned_count"] for p in providers) != manifest.get("input_occurrence_count"):
        errors.append("provider input accounting mismatch")
    return errors


def replay(manifest):
    """Re-run only selection from retained metadata, with networking disabled by design."""
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("; ".join(errors))
    if manifest.get("status") != "ok" or manifest.get("replay_status") != "complete":
        raise ValueError("incomplete intake cannot support exact replay")
    if manifest["selector_hash"] != _selector_hash():
        raise ValueError("selector version mismatch")
    if manifest["policy_fingerprint"] != coverage_policy_manifest(manifest["coverage_mode"])["fingerprint"]:
        raise ValueError("policy version mismatch")
    from agents.news_agent import NewsAgent
    from config import SIGNAL_ELIGIBLE_TICKER_SET, EDITORIAL_COVERAGE_TICKER_SET
    mode = manifest["coverage_mode"]
    class ReplayAgent(NewsAgent):
        @classmethod
        def _mapping_universe(cls):
            return SIGNAL_ELIGIBLE_TICKER_SET if mode == "off" else EDITORIAL_COVERAGE_TICKER_SET
    # Shadow production uses the off interpretation; no global mode mutation.
    if mode == "shadow":
        mode = "off"
    agent = ReplayAgent(now=datetime.fromisoformat(manifest["evaluation_at"]),
                        providers=[], evidence_intake_mode="off")
    raw = [deepcopy(row["metadata"]) for row in manifest["records"] if row["ranking_input"]]
    processed = agent._process_pool(raw, shadow_projection=False)
    expected = Counter(tuple(sorted(row["decision"].items())) for row in manifest["records"]
                       if row["ranking_input"])
    observed = Counter(tuple(sorted({"stage": name, "reason": row.get("drop_reason") or
                                    ("selected" if name == "selected" else "unknown"),
                                    "lane": row.get("lane")}.items()))
                       for name in TERMINALS for row in processed[name])
    # OMNI-01 changes some dropped-only labels for shadow diagnostics.
    if manifest["coverage_mode"] == "shadow":
        expected = Counter((dict(k)["stage"], dict(k)["reason"], dict(k)["lane"])
                           for k, count in expected.items() for _ in range(count)
                           if dict(k)["stage"] == "selected")
        observed = Counter((dict(k)["stage"], dict(k)["reason"], dict(k)["lane"])
                           for k, count in observed.items() for _ in range(count)
                           if dict(k)["stage"] == "selected")
    return {"selection_matches": selection_signature(processed["selected"]) == manifest["selection_signature"],
            "matches_decision_counts": expected == observed,
            "selected_source_ids": [r.get("source_record_id") for r in processed["selected"]],
            "scope": "selection_metadata_only", "network_requests": 0}
