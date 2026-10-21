#!/usr/bin/env python3
"""Reproduce a wholly synthetic, offline OMNI workflow in a new local folder.

Usage: python scripts/omni_synthetic_demo.py --output /tmp/new-omni-demo
The output directory must not exist and must be outside this checkout. All source
documents, reviews, rights, consent, costs and feedback are fictional exercises.
No network request, real approval, customer evidence or prospective gate credit
is created. The retained SQLite database belongs only to the chosen demo folder.
The public API is run_demo(output_directory), which returns the artifact manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from omni.attestations import VERSION as ATTESTATION_VERSION
from omni.briefs import REQUIRED, export_brief, preview_brief
from omni.evidence import document
from omni.gates import record_gate_report
from omni.identities import VERSION as IDENTITY_VERSION, EntityRegistry, digest
from omni.lifecycle import implementation_hash, reference, resolve_reference
from omni.operating import CATEGORIES, VERSION as OPERATING_VERSION
from omni.operating import cost_components, record_economics_report, record_observation
from omni.receipts import get_receipt, list_receipts, put_receipt
from omni.review import record_review
from omni.rights import VERSION as RIGHTS_VERSION, record_rights
from omni.runner import analyze
from omni.store import EventStore

VERSION = "omni-synthetic-demo-1"
NOW = "2026-10-18T12:00:00Z"
WINDOW_END = "2026-10-18T13:00:00Z"
OBSERVED = "2026-10-18T13:05:00Z"
CORRECTED = "2026-10-18T13:10:00Z"
REVIEWED = "2026-10-18T13:12:00Z"
EXPIRES = "2026-10-25T12:00:00Z"
LABEL = "pending_human_review"
REVIEWER = "synthetic-demo-operator-not-a-person"


def _check(condition, message):
    if not condition:
        raise RuntimeError("synthetic demo contract failed: " + message)


def _write(folder, name, value):
    with (folder / name).open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")


def _registry():
    known = "2026-01-01T00:00:00Z"
    evidence = ["synthetic-demo-fixture-only"]
    entities = [{"entity_id": "issuer:synthetic-demo", "kind": "issuer", "name": "Synthetic Demo Company",
                 "known_from": known, "evidence": evidence, "verification": LABEL}]
    aliases = [{"entity_id": entities[0]["entity_id"], "alias": entities[0]["name"],
                "known_from": known, "evidence": evidence}]
    relationships = []
    for name in ("ALPHA", "BETA"):
        identifier = "trial:synthetic-demo-" + name.lower()
        entities.append({"entity_id": identifier, "kind": "trial", "name": "DEMO-" + name,
                         "phase": "PHASE3", "known_from": known, "evidence": evidence, "verification": LABEL})
        aliases.append({"entity_id": identifier, "alias": "DEMO-" + name, "known_from": known, "evidence": evidence})
        relationships.append({"from_id": "issuer:synthetic-demo", "to_id": identifier, "role": "synthetic_sponsor",
                              "known_from": known, "evidence": evidence})
    return EntityRegistry({"version": IDENTITY_VERSION, "entities": entities, "aliases": aliases,
                           "relationships": relationships, "synthetic": True, "human_review_status": LABEL})


def _policy():
    return {"policy_id": "synthetic-demo-issuer", "version": VERSION, "evidence": ["synthetic-local-fixture-only"],
            "routes": [{"host": "omni-demo.example", "path_prefix": "/releases/"}],
            "source_class": "issuer", "reporting_parties": ["synthetic-demo-issuer"],
            "uses": {"acquire": True, "store_text": True, "local_analysis": True, "review_display": True,
                     "commercial_display": False, "model_processing": False},
            "synthetic": True, "human_review_status": LABEL,
            "restriction": "Fictional local documents only; no external source permission or transport grant."}


class _Workflow:
    def __init__(self, store, registry, policies):
        self.store, self.registry, self.policies = store, registry, policies
        self.policy = next(iter(policies.values()))
        self.implementation = implementation_hash()
        self.policy_hash = digest(policies)
        self.scope = digest({"demo": VERSION, "registry": registry.fingerprint(), "implementation": self.implementation})
        self.review_refs = []
        self.document_refs = []

    def attest(self, purpose, subject, bindings, *, now):
        return put_receipt(self.store, "operator_attestation", {
            "schema": ATTESTATION_VERSION, "purpose": purpose, "subject_hash": digest(subject),
            "decision": "approved", "reviewer": REVIEWER, "reviewed_at": now, "evidence_kind": "synthetic",
            "external_reference": {"reference": "fictional offline demo fixture; no real approval", "sha256": digest(subject)},
            **bindings}, observed_at=now)

    def ingest(self, name, text, *, now, published):
        doc = document(policy=self.policy, url="https://omni-demo.example/releases/" + name,
            native_id="synthetic-release-" + name, text=text, title="SYNTHETIC DEMO ONLY — " + name,
            published=published, observed_at=now, source_class="issuer", reporting_party="synthetic-demo-issuer",
            body_complete=True, evidence_kind="synthetic")
        events, decisions, leads = analyze(doc, self.registry, self.policies, now=now)
        _check(len(events) == 1, "one fictional event must be extracted per source document")
        self.store.commit_cycle(cycle_id="demo-cycle:" + digest([name, now, doc["revision_id"]]),
            source_id=self.policy["policy_id"], observed_at=now, documents=[doc], events=events,
            decisions=decisions, leads=leads, registry=self.registry)
        self.document_refs.append(reference(doc))
        return doc, events[0]

    def review(self, event, *, now):
        result = record_review(self.store, event["event_id"], event["event_revision"], self.registry,
            self.policies, now=now, reviewer=REVIEWER, action="approve",
            note="Programmatic synthetic workflow exercise; not a human editorial approval.")
        self.review_refs.append(result["review_id"])
        return result

    def grant(self, doc, *, now):
        granted = []
        for field in REQUIRED + ("correction.history",):
            body = {"contract": RIGHTS_VERSION, "source_ref": reference(doc), "field": field,
                "purpose": "local_export", "audience": "local_operator", "decision": "approved",
                "rightsholder": "fictional demo fixture author", "attribution": "exact synthetic .example source URL",
                "retention_rule": "operator-chosen disposable local demo folder only",
                "policy_hash": self.policy_hash, "implementation_hash": self.implementation,
                "transformation": "derived_summary" if field.startswith("claim.") else
                    "sanitized_history" if field in {"coverage", "correction.history", "review.decision"} else "canonical_metadata",
                "evidence_kind": "synthetic", "effective_at": now, "expires_at": EXPIRES}
            att = self.attest("field_rights", body, {"policy_hash": self.policy_hash,
                "implementation_hash": self.implementation, "source_policy_id": self.policy["policy_id"]}, now=now)
            granted.append(record_rights(self.store, {**body, "attestation_ref": att["receipt_id"]}, self.policies,
                now=now, allow_synthetic=True)["receipt_id"])
        return granted

    def brief(self, *, now):
        return preview_brief(self.store, self.registry, self.policies, now=now, scope_hash=self.scope,
            purpose="local_export", allow_synthetic=True, persist=True)

    def observe(self, kind, identifier, **values):
        body = {"contract": OPERATING_VERSION, "observation_id": "synthetic-demo-" + identifier, "kind": kind,
            "evidence_kind": "synthetic", "scope_hash": self.scope, "implementation_hash": self.implementation,
            "policy_hash": self.policy_hash, "window_start": NOW, "window_end": WINDOW_END,
            "observed_at": OBSERVED, "source_refs": [],
            "external_reference": {"reference": "fictional fixture: " + identifier, "sha256": digest([VERSION, identifier, values])},
            **values}
        att = self.attest("operating_observation", body, {"scope_hash": self.scope,
            "implementation_hash": self.implementation, "policy_hash": self.policy_hash}, now=OBSERVED)
        return record_observation(self.store, {**body, "attestation_ref": att["receipt_id"]}, now=OBSERVED,
            allow_synthetic=True, registry=self.registry, policies=self.policies)

    def economics(self, *, now):
        return record_economics_report(self.store, now=now, scope_hash=self.scope, window_start=NOW,
            window_end=WINDOW_END, policy_hash=self.policy_hash, allow_synthetic=True,
            registry=self.registry, policies=self.policies)


def run_demo(output_directory):
    """Create a new offline demonstration; never overwrite, fetch or activate."""
    output = Path(output_directory).expanduser().resolve()
    if output.exists():
        raise ValueError("output directory must not already exist")
    if output == ROOT or output.is_relative_to(ROOT):
        raise ValueError("choose a new output directory outside the checkout; generated databases must never be tracked")
    output.mkdir(parents=True, exist_ok=False)
    registry = _registry()
    policy = _policy()
    policies = {policy["policy_id"]: policy}
    _write(output, "registry.json", registry.data)
    _write(output, "source-policies.json", {"default_mode": "off", "policies": policies, "feeds": [], "synthetic": True})
    store = EventStore(output / "events.sqlite3")
    try:
        flow = _Workflow(store, registry, policies)
        setup = put_receipt(store, "synthetic_demo_setup", {"contract": VERSION, "evidence_kind": "synthetic",
            "human_review_status": LABEL, "measured_actuals": False, "scope_hash": flow.scope,
            "registry_hash": registry.fingerprint(), "policy_hash": flow.policy_hash,
            "implementation_hash": flow.implementation, "network_requests": 0,
            "real_approvals": False, "prospective_credit": False, "customer_credit": False}, observed_at=NOW)
        alpha_doc, alpha = flow.ingest("alpha", "DEMO-ALPHA met its primary endpoint.", now=NOW,
                                      published="2026-10-18T10:00:00Z")
        beta_doc, beta = flow.ingest("beta", "DEMO-BETA met its primary endpoint.", now=NOW,
                                    published="2026-10-18T10:05:00Z")
        initial_reviews = [flow.review(alpha, now=NOW), flow.review(beta, now=NOW)]
        baseline = flow.brief(now=NOW)
        _check(not baseline["payload"]["cards"] and len(baseline["payload"]["withheld"]) == 2,
               "missing field rights must withhold both otherwise reviewed cards")
        _write(output, "rights-withheld-baseline.json", baseline)
        flow.grant(alpha_doc, now=NOW)
        flow.grant(beta_doc, now=NOW)
        initial = flow.brief(now=NOW)
        _check(len(initial["payload"]["cards"]) == 2, "synthetic field grants must yield two local cards")
        initial_exports = []
        for suffix in ("json", "md", "html"):
            initial_exports.append(export_brief(store, initial["receipt_id"], output / ("reviewed-brief." + suffix),
                registry, policies, now=NOW, allow_synthetic=True)["receipt_id"])

        run_values = {"run_id": "synthetic-demo-run", "currency": "USD", "incurred_at": "2026-10-18T12:46:00Z",
            "review_minutes": "30", "review_hourly_rate": "120", "operator_minutes": "15", "operator_hourly_rate": "80",
            "vendor_amount": "10", "compute_storage_amount": "4", "other_amount": "-8",
            "allocation_basis": "one fictional run; full allocation to this exact closed reporting window"}
        run_cost = flow.observe("run_cost", "run-cost", **run_values)
        flow.observe("cost_completeness", "complete-cost-census", cost_refs=[run_cost["receipt_id"]],
                     covered_categories=sorted(CATEGORIES))
        flow.observe("review_effort", "review-clock", started_at="2026-10-18T12:01:00Z", ended_at="2026-10-18T12:31:00Z",
                     elapsed_seconds=1800.0, role="reviewer", run_id=run_values["run_id"])
        flow.observe("review_effort", "operator-clock", started_at="2026-10-18T12:31:00Z", ended_at="2026-10-18T12:46:00Z",
                     elapsed_seconds=900.0, role="operator", run_id=run_values["run_id"])
        useful = [flow.observe("useful_brief", "useful-" + name, brief_ref=initial["receipt_id"],
            event_ids=[event["event_id"]], useful=True, assessed_at="2026-10-18T12:49:00Z")
            for name, event in (("alpha", alpha), ("beta", beta))]
        flow.observe("useful_event_census", "complete-useful-census",
                     assessment_refs=[row["receipt_id"] for row in useful])
        consent_subject = {"participant_ref": "synthetic-participant-1", "task_version": "synthetic-reading-task-1",
                           "brief_ref": initial["receipt_id"], "purpose": "structured_feedback_retention"}
        consent = flow.attest("customer_feedback_consent", consent_subject, {"scope_hash": flow.scope,
            "implementation_hash": flow.implementation, "policy_hash": flow.policy_hash}, now=NOW)
        feedback = flow.observe("customer_feedback", "negative-feedback", **{k: v for k, v in consent_subject.items() if k != "purpose"},
            consent_ref=consent["receipt_id"], result="not_useful", session_started_at="2026-10-18T12:50:00Z",
            session_ended_at="2026-10-18T12:55:00Z")
        economics = flow.economics(now=OBSERVED)
        report = economics["payload"]
        _check((report["direct_cost"], report["useful_event_count"], report["cost_per_useful_event"]) == ("86", 2, "43"),
               "the workbook-compatible signed run must total 86 / 2 = 43")
        _check(not report["pending_reasons"] and report["measured_actuals"] is False
               and report["feedback_results"]["not_useful"] == 1, "synthetic economics must preserve negative feedback without actuals credit")
        _write(output, "economics.json", economics)
        _write(output, "cost-formula.json", {"contract": VERSION, "synthetic": True, "human_review_status": LABEL,
            "measured_actuals": False, "run_cost_ref": run_cost["receipt_id"], "inputs": run_values,
            "formula": "review_minutes * review_hourly_rate / 60 + operator_minutes * operator_hourly_rate / 60 + vendor_amount + compute_storage_amount + other_amount",
            "substitution": "30 * 120 / 60 + 15 * 80 / 60 + 10 + 4 - 8 = 86; 86 / 2 = 43",
            "derived_categories": {k: str(v) for k, v in cost_components(run_cost["payload"]).items()},
            "direct_cost": report["direct_cost"], "useful_event_count": report["useful_event_count"],
            "cost_per_useful_event": report["cost_per_useful_event"], "workbook_actuals_modified": False})

        corrected_doc, corrected_event = flow.ingest("alpha", "DEMO-ALPHA did not meet its primary endpoint.",
            now=CORRECTED, published="2026-10-18T13:09:00Z")
        after_change = flow.brief(now=CORRECTED)
        _check(alpha["event_id"] not in {c["event_id"] for c in after_change["payload"]["cards"]},
               "the source correction must remove the old approved card")
        _check(alpha["event_id"] in {r["event_id"] for r in store.pending()}, "correction must reopen the local review queue")
        rejected_path = output / "blocked-stale-export.json"
        try:
            export_brief(store, initial["receipt_id"], rejected_path, registry, policies, now=CORRECTED, allow_synthetic=True)
        except ValueError as error:
            refusal_reason = str(error)
        else:
            raise RuntimeError("synthetic demo contract failed: stale export was not refused")
        _check(not rejected_path.exists(), "a refused stale export must not create a file")
        changes = [row["receipt_id"] for row in list_receipts(store, kind="source_change")]
        _check(bool(changes), "source correction must retain an immutable change receipt")
        refusal = put_receipt(store, "synthetic_demo_export_refusal", {"contract": VERSION, "evidence_kind": "synthetic",
            "human_review_status": LABEL, "brief_ref": initial["receipt_id"], "source_change_refs": changes,
            "reason": refusal_reason, "destination_created": False, "measured_actuals": False},
            observed_at=CORRECTED, refs=[initial["receipt_id"], *changes])
        _write(output, "correction-withheld.json", after_change)
        _write(output, "stale-export-refusal.json", refusal)
        after_economics = flow.economics(now=CORRECTED)
        _check(bool(after_economics["payload"]["pending_reasons"]), "corrected historical uses must not silently retain a complete denominator")
        _write(output, "economics-after-correction.json", after_economics)
        fresh_review = flow.review(corrected_event, now=REVIEWED)
        flow.grant(corrected_doc, now=REVIEWED)
        fresh = flow.brief(now=REVIEWED)
        _check(len(fresh["payload"]["cards"]) == 2, "fresh synthetic review and exact revision rights must restore two cards")
        corrected_card = next(c for c in fresh["payload"]["cards"] if c["event_id"] == corrected_event["event_id"])
        _check(any(c["predicate"] == "primary_endpoint_status" and c["value"] == "not_met"
                   for c in corrected_card["claims"]), "fresh brief must preserve the corrected primary failure")
        fresh_exports = []
        for suffix in ("json", "md", "html"):
            fresh_exports.append(export_brief(store, fresh["receipt_id"], output / ("corrected-brief." + suffix),
                registry, policies, now=REVIEWED, allow_synthetic=True)["receipt_id"])

        evidence = {"scope_hash": flow.scope, "reviewed_useful_brief_receipts": [fresh["receipt_id"]],
            "field_level_rights_receipt": fresh["payload"]["rights_refs"], "customer_feedback_receipt": [feedback["receipt_id"]],
            "operating_cost_receipt": economics["receipt_id"]}
        readiness = record_gate_report(store, evidence, [], registry=registry, policies=policies, now=REVIEWED)
        _check(all(readiness["payload"]["report"][gate]["status"] == "pending" for gate in ("OMNI-10", "OMNI-11", "OMNI-12")),
               "a synthetic demo without prospective operator evidence cannot mature readiness")
        _write(output, "readiness.json", readiness)
        receipts = list_receipts(store)
        for row in receipts:
            for ref in row["refs"]:
                get_receipt(store, ref)
        for ref in flow.document_refs:
            _check(resolve_reference(store, ref)["kind"] == "document", "source reference must resolve to retained synthetic evidence")
        for ref in flow.review_refs:
            _check(store.conn.execute("SELECT 1 FROM reviews WHERE review_id=?", (ref,)).fetchone() is not None,
                   "synthetic review reference must resolve")
        _write(output, "receipts.json", {"contract": VERSION, "synthetic": True, "human_review_status": LABEL,
            "measured_actuals": False, "receipts": receipts})
        _write(output, "source-documents.json", {"contract": VERSION, "synthetic": True,
            "human_review_status": LABEL, "documents": [alpha_doc, beta_doc, corrected_doc]})
        _check(flow.implementation == implementation_hash(), "implementation changed during the demo; rerun against a stable snapshot")
        manifest = {"contract": VERSION, "synthetic": True, "human_review_status": LABEL,
            "ai_domain_labels": LABEL, "measured_actuals": False, "signal_eligible": False,
            "automatic_activation": False, "automatic_membership_change": False, "network_requests": 0,
            "real_approvals": False, "prospective_credit": False, "customer_credit": False,
            "database": "events.sqlite3", "scope_hash": flow.scope,
            "clocks": {"start": NOW, "window_end": WINDOW_END, "operating_observation": OBSERVED,
                       "source_correction": CORRECTED, "fresh_synthetic_review": REVIEWED},
            "receipt_refs": {"setup": setup["receipt_id"], "rights_withheld_baseline": baseline["receipt_id"],
                "initial_brief": initial["receipt_id"], "economics_before_correction": economics["receipt_id"],
                "economics_after_correction": after_economics["receipt_id"], "negative_feedback": feedback["receipt_id"],
                "prior_synthetic_consent": consent["receipt_id"], "stale_export_refusal": refusal["receipt_id"],
                "fresh_brief": fresh["receipt_id"], "readiness": readiness["receipt_id"]},
            "export_refs": initial_exports + fresh_exports, "review_refs": flow.review_refs, "document_refs": flow.document_refs,
            "correction_workflow": {"event_id": alpha["event_id"], "old_event_revision": alpha["event_revision"],
                "new_event_revision": corrected_event["event_revision"], "initial_review_ref": initial_reviews[0]["review_id"],
                "fresh_review_ref": fresh_review["review_id"], "source_change_refs": changes, "stale_export_created": False},
            "synthetic_run_cost": {"direct_cost": "86", "useful_event_count": 2, "cost_per_useful_event": "43",
                "currency": "USD", "measured_actuals": False, "snapshot": "before_source_correction"},
            "reference_checks": {"receipt_refs_resolve": True, "document_refs_resolve": True, "review_refs_resolve": True},
            "files": ["registry.json", "source-policies.json", "source-documents.json", "rights-withheld-baseline.json",
                "reviewed-brief.json", "reviewed-brief.md", "reviewed-brief.html", "cost-formula.json", "economics.json",
                "correction-withheld.json", "stale-export-refusal.json", "economics-after-correction.json",
                "corrected-brief.json", "corrected-brief.md", "corrected-brief.html", "readiness.json", "receipts.json",
                "events.sqlite3", "README.md", "demo-manifest.json", "artifacts.sha256"]}
    finally:
        store.close()

    _write(output, "demo-manifest.json", manifest)
    readme = """# OMNI synthetic offline demonstration

Every source document, entity, review, field grant, consent record, cost and
feedback record here is fictional. AI/domain labels remain pending human review.
No network request, real approval, customer evidence or prospective pilot credit
was created. All operating actuals flags remain false. External distribution is
not authorized by the workflow; this folder is an operator-chosen local exercise.

## Walkthrough

1. `rights-withheld-baseline.json` shows two reviewed synthetic events withheld
   because they have no field grants.
2. `reviewed-brief.json`, `.md` and `.html` contain two local cards with exact
   synthetic source attribution after explicit synthetic grants.
3. `cost-formula.json` demonstrates the operating workbook's run-cost formula:
   30 * 120 / 60 + 15 * 80 / 60 + 10 + 4 - 8 = 86 USD. The final -8 is a signed
   credit. Two explicitly assessed useful event units give 86 / 2 = 43 USD.
   `economics.json` is the synthetic snapshot before the later source correction.
   One consented fictional feedback session is preserved as `not_useful`.
4. `correction-withheld.json` and `stale-export-refusal.json` show the changed
   source reopening review and refusing the old export before creating a file.
   The blocked destination `blocked-stale-export.json` does not exist.
5. `corrected-brief.json`, `.md` and `.html` show the corrected primary failure
   after a fresh synthetic review and grants for the exact new source revision.
6. `economics-after-correction.json` retains the resulting historical denominator
   debt. Fresh review does not invent a new use within the old reporting window.
   `readiness.json` leaves OMNI-10/11/12 pending; no prospective protocol or real
   rights, counsel, customer or release evidence was manufactured.

## Reproduce and inspect

From the checkout, run:

    python scripts/omni_synthetic_demo.py --output /tmp/new-omni-demo

The output directory must be new and outside the checkout. The Python API is
`scripts.omni_synthetic_demo.run_demo(output_directory)`. It returns the manifest.
The implementation and APIs use fixed synthetic clocks on 2026-10-18. They do
not run source collectors, schedulers, model calls, account creation or delivery.

`demo-manifest.json` lists every artifact and the exact receipt, document, review
and correction references. `receipts.json` contains immutable receipt envelopes;
`events.sqlite3` retains their source and review history. All references were
resolved before completion. `artifacts.sha256` covers every other file after the
database was closed. Do not commit generated databases or demo artifacts.

Changing source code invalidates implementation-bound records. Generate a new
folder after code changes. An interrupted run may leave a partial folder; it is
never overwritten or presented as a completed demonstration.
"""
    with (output / "README.md").open("x", encoding="utf-8") as handle:
        handle.write(readme)
    files = sorted(p for p in output.iterdir() if p.is_file())
    with (output / "artifacts.sha256").open("x", encoding="utf-8") as handle:
        handle.write("# Synthetic demo artifacts; this manifest excludes itself.\n")
        for path in files:
            handle.write(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, required=True,
                        help="new directory outside the checkout; retains synthetic artifacts and SQLite database")
    args = parser.parse_args(argv)
    try:
        result = run_demo(args.output)
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, "Synthetic demo incomplete: " + str(error) + "\n")
    print(json.dumps({"status": "completed_synthetic_demo", "manifest": str(args.output.expanduser().resolve() / "demo-manifest.json"),
                      "synthetic": True, "human_review_status": LABEL, "measured_actuals": False,
                      "readiness": "pending", "artifact_count": len(result["files"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
