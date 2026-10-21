"""Executable synthetic D-V01..20 integration and vertical contract boundaries.

These fixtures are not human gold, source approval, prospective collection, or
pilot evidence. HTTP sessions and credential handles here are test doubles.
"""
from copy import deepcopy
from contextlib import redirect_stdout
from io import BytesIO
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET

import requests

from omni.acquisition import Deferred
from omni.amounts import parse_amount_sentence
from omni.identities import digest, load_registry
from omni.receipts import put_receipt, get_receipt
from omni.store import EventStore
from omni.transport import (ReadOnlyJSONClient, CONTRACTS, NRC_URL, EIA_METADATA_URL, EIA_DATA_URL,
                            USA_AWARD_URL, USA_TRANSACTION_URL, validate_nrc_query, validate_award_query,
                            validate_eia_query, redact)
from omni.verticals.common import (atom, check_atom, checked_stakes, origin_count, mapping_visibility,
                                   revision_transition, persist_batch, deferred_lead, validate_crosswalk)
from omni.verticals.eia import pin_metadata, parse_eia_observations, collect_eia
from omni.verticals.eia860 import parse_eia860_bundle, GENERATOR_HEADERS, OWNER_HEADERS, CONTRACT as EIA860_CONTRACT
from omni.verticals.nrc import parse_nrc_metadata, licensing_stages, licensing_paragraphs, hydration_status, collect_nrc_aps
from omni.verticals.procurement import parse_transactions, parse_awards, recipient_identity, collect_procurement
from omni.verticals.cli import adapter_plan
from test_omni_events import make_doc

NOW = "2026-10-20T12:00:00Z"
CORPUS = json.loads((Path(__file__).parent / "fixtures/omni/vertical_sources.json").read_text())
FIXTURES = {f["fixture_id"]: f for f in CORPUS["fixtures"]}


def reply(payload=None, *, status=200, raw=None, headers=None):
    raw = raw if raw is not None else json.dumps(payload).encode()
    response = Mock(status_code=status, headers={"Content-Type": "application/json", **(headers or {})})
    response.iter_content.return_value = [raw]
    return response


def nrc_query():
    return {"q": "synthetic fuel", "filters": [], "anyFilters": [], "legacyLibFilter": False,
            "mainLibFilter": True, "sort": "AccessionNumber", "sortDirection": 0, "skip": 0}


def nrc_payload(**fields):
    return {"count": 1, "pageNumber": 1, "results": [{"document": {
        "AccessionNumber": "ML24017A120", "DocumentTitle": "Synthetic licensing metadata",
        "DocumentDate": "2024-01-18", "DateAddedTimestamp": "2024-01-23 08:34",
        "DocketNumber": ["SYN-DOCKET-1"], "DocumentType": ["Synthetic letter"], **fields}}]}


def metadata():
    # Names/units below are intentionally synthetic, not guessed EIA fields.
    return pin_metadata({"apiVersion": "synthetic-1", "response": {
        "frequency": [{"id": "daily", "format": "YYYY-MM-DD"}], "facets": [{"id": "syntheticUnit"}],
        "data": {"syntheticPower": {"units": "source-defined power unit"}}}},
        observed_at=NOW, evidence_kind="synthetic")


def eia_query():
    return [("frequency", "daily"), ("data[]", "syntheticPower"), ("start", "2026-10-01"), ("end", "2026-10-20"),
            ("offset", "0"), ("length", "250"), ("sort[0][column]", "period"), ("sort[0][direction]", "asc"),
            ("sort[1][column]", "syntheticUnit"), ("sort[1][direction]", "asc")]


def eia_payload(values=("0",), total=None):
    return {"apiVersion": "synthetic-1", "response": {"frequency": "daily", "dateFormat": "YYYY-MM-DD",
        "total": str(len(values) if total is None else total), "data": [
            {"period": "2026-10-16", "syntheticUnit": "SYN-UNIT-" + str(i), "syntheticPower": value}
            for i, value in enumerate(values)]}}


def transaction_query():
    return {"award_id": "CONT_AWD_SYN_001", "limit": 100, "page": 1, "sort": "action_date", "order": "asc"}


def award_query():
    return {"filters": {"award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": "2026-10-01", "end_date": "2026-10-20"}]},
            "fields": ["generated_internal_id", "Award Amount", "Recipient UEI", "Last Modified Date"],
            "limit": 100, "page": 1, "sort": "generated_internal_id", "order": "asc", "subawards": False}


def transaction_payload(value="-200000", modification="SYN-MOD-02"):
    return {"page_metadata": {"page": 1, "hasNext": False, "next": None}, "results": [{
        "id": "SYN-TRANSACTION-01", "type": "D", "action_date": "2026-10-16", "action_type": "B",
        "modification_number": modification, "federal_action_obligation": value,
        "description": "Synthetic transaction; no revenue or ceiling assertion"}]}


def workbook(sheets, headers, *, percentage_style=True, formula=False):
    """Generate minimal valid synthetic OOXML using the actual verified layout."""
    s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    p = "http://schemas.openxmlformats.org/package/2006/relationships"
    book = ET.Element("workbook", xmlns=s); ss = ET.SubElement(book, "sheets")
    rels = ET.Element("Relationships", xmlns=p)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as z:
        for i, (name, rows) in enumerate(sheets.items(), 1):
            ET.SubElement(ss, "sheet", {"name": name, "sheetId": str(i), "{" + r + "}id": "rId" + str(i)})
            ET.SubElement(rels, "Relationship", {"Id": "rId" + str(i), "Target": "worksheets/sheet" + str(i) + ".xml"})
            sheet = ET.Element("worksheet", xmlns=s); data = ET.SubElement(sheet, "sheetData")
            for n, row in enumerate([["2025 Form EIA-860 Synthetic Fixture"], list(headers), *rows], 1):
                node = ET.SubElement(data, "row", r=str(n))
                for j, value in enumerate(row):
                    column = chr(65 + j)
                    numeric = n > 2 and headers[j] == "Percent Owned"
                    c = ET.SubElement(node, "c", r=column + str(n), t="n" if numeric else "inlineStr")
                    if numeric:
                        c.set("s", "1" if percentage_style else "0")
                        ET.SubElement(c, "v").text = value
                        if formula:
                            ET.SubElement(c, "f").text = "1/2"
                    else:
                        ET.SubElement(ET.SubElement(c, "is"), "t").text = value
            z.writestr("xl/worksheets/sheet" + str(i) + ".xml", ET.tostring(sheet))
        z.writestr("xl/workbook.xml", ET.tostring(book)); z.writestr("xl/_rels/workbook.xml.rels", ET.tostring(rels))
        z.writestr("xl/styles.xml", '<styleSheet xmlns="' + s + '"><cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="10"/></cellXfs></styleSheet>')
    return buffer.getvalue()


def eia860_bundle(*, owners=(), include_owner=True, percentage_style=True, formula=False, generators=None):
    generator = ["101", "Synthetic Operator", "501", "Synthetic Plant", "SYN-GEN-01", "OP"]
    gen = workbook({"Operable": [generator] if generators is None else generators, "Proposed": [], "Retired and Canceled": []}, GENERATOR_HEADERS)
    owner = workbook({"Ownership": [[*generator, "Synthetic Owner " + str(i), str(201 + i), value] for i, value in enumerate(owners)]},
                     OWNER_HEADERS, percentage_style=percentage_style, formula=formula)
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as z:
        z.writestr(EIA860_CONTRACT["generator_file"], gen)
        if include_owner:
            z.writestr(EIA860_CONTRACT["owner_file"], owner)
    return buffer.getvalue()


class VerticalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self.tmp.name) / "vertical.db")

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def parsed_eia(self, payload):
        return parse_eia_observations(payload, metadata_snapshot=metadata(), observed_at=NOW,
                                      source_policy_fingerprint="synthetic-policy", evidence_kind="synthetic")

    def parsed_860(self, raw, **kwargs):
        return parse_eia860_bundle(raw, vintage=2025, observed_at=NOW, source_policy_fingerprint="synthetic-policy",
                                  evidence_kind="synthetic", **kwargs)

    def attestation(self, purpose, subject_hash, bindings):
        value = {"schema": "omni-operator-attestation-1", "purpose": purpose, "subject_hash": subject_hash,
                 "decision": "approved", "reviewer": "synthetic-human-fixture", "evidence_kind": "synthetic",
                 "reviewed_at": NOW, "external_reference": {"reference": "synthetic-only", "sha256": digest("synthetic attestation")}, **bindings}
        return put_receipt(self.store, "operator_attestation", value, observed_at=NOW)["receipt_id"]

    def client(self, contract_id, *, session=None, acquire=True, approved=True, secret="synthetic/secret+value", **kwargs):
        contract = CONTRACTS[contract_id]
        policy = {"policy_id": "synthetic-" + contract_id, "version": "1", "evidence": ["synthetic_fixture"],
                  "routes": [{"host": urlsplit(contract["url"]).hostname, "path_prefix": "/"}],
                  "uses": {"acquire": acquire, "store_text": True, "local_analysis": True, "review_display": True},
                  "source_class": "government", "reporting_parties": ["synthetic-source"]}
        grant = {"contract_id": contract_id, "contract_hash": digest(contract), "method": contract["method"], "url": contract["url"]}
        if approved:
            ref = self.attestation("vertical_transport_contract", digest(grant),
                                   {"source_policy_id": policy["policy_id"], "source_policy_fingerprint": digest(policy)})
            policy["read_only_requests"] = [{**grant, "approval_ref": ref}]
        resolver = Mock(return_value=[(None, None, None, None, ("93.184.216.34", 443))])
        return ReadOnlyJSONClient(self.store, policy, user_agent="Synthetic QA qa@example.org", now=NOW,
                                  session=session or Mock(), resolver=resolver, sleep=lambda _: None,
                                  credential_lookup=lambda _: secret, allow_synthetic_approvals=True, **kwargs)

    def test_d_v01_nrc_preapplication_is_not_license(self):
        fixture = FIXTURES["D-V01"]
        result = licensing_stages(fixture["input"]["text"], subject_hint=fixture["input"]["subject"])
        self.assertEqual([x["stage"] for x in result["stages"]], [fixture["expected"]["stage"]])
        self.assertEqual(result["future_events"][0]["time"]["precision"], fixture["expected"]["future_event_precision"])
        self.assertFalse(result["license_issued"])

    def test_d_v02_facility_and_historical_contexts_remain_separate(self):
        result = licensing_paragraphs(FIXTURES["D-V02"]["input"]["paragraphs"])
        self.assertFalse(result["cross_paragraph_subject_merge"])
        self.assertEqual(len({x["subject_context"] for x in result["contexts"]}), 3)
        self.assertTrue(all(not x["new_license"] and not x["new_obligation"] for x in result["contexts"]))
        self.assertEqual(result["contexts"][2]["contract_context"], "historical")

    def test_d_v03_unknown_timezone_preserved(self):
        f = FIXTURES["D-V03"]["input"]
        parsed = parse_nrc_metadata(nrc_payload(DocumentDate=f["DocumentDate"], DateAddedTimestamp=f["DateAddedTimestamp"]),
                                    query_fingerprint="synthetic-query", observed_at=f["observed_at"],
                                    source_policy_fingerprint="synthetic-policy", evidence_kind="synthetic")
        time = parsed["rows"][0]["times"]["source_posted"]
        self.assertEqual(time["raw"], f["DateAddedTimestamp"]); self.assertEqual(time["timezone"], "unknown")
        self.assertIsNone(time["normalized_instant"]); self.assertFalse(parsed["precise_latency_eligible"])

    def test_d_v04_metadata_before_document_download(self):
        f = FIXTURES["D-V04"]
        self.assertEqual(hydration_status(metadata_available=f["input"]["metadata_available"], download_status=f["input"]["download_status"]), f["expected"])

    def test_d_v05_same_unit_period_verified_lineage_counts_once(self):
        f = FIXTURES["D-V05"]["input"]
        paths = [{"path_id": name, "revision_id": "synthetic-revision-" + name, "unit_id": f["unit"], "period": f["period"]} for name in f["paths"]]
        raw = {"from_revision": paths[0]["revision_id"], "to_revision": paths[1]["revision_id"],
               "unit_id": f["unit"], "period": f["period"], "relation": "derived_from"}
        edge = {**raw, "from_path": "NRC", "to_path": "EIA", "proofs": {key: atom(raw, "/" + key) for key in raw}}
        edge["receipt_id"] = self.attestation("vertical_origin_correspondence", digest(edge),
            {"adapter_version": "omni-vertical-candidate-1", "from_revision": raw["from_revision"], "to_revision": raw["to_revision"]})
        result = origin_count(paths, reviewed_correspondence=[edge], snapshots={digest(raw): raw}, store=self.store, now=NOW, allow_synthetic=True)
        self.assertEqual(result["assertion_origins"], 1); self.assertFalse(result["independence_established"])
        damaged = deepcopy(edge); damaged["proofs"] = "synthetic"
        self.assertEqual(origin_count(paths, reviewed_correspondence=[damaged], store=self.store, now=NOW)["assertion_origins"], 2)

    def test_d_v06_empty_eia_shell_is_not_zero_or_restart(self):
        self.assertEqual(FIXTURES["D-V06"]["input"]["response_kind"], "empty_shell")
        result = self.parsed_eia({})
        self.assertEqual(result["rows"], []); self.assertIn("source_content_unavailable", result["gaps"])
        self.assertFalse(result["complete"])

    def test_d_v07_incomplete_owner_source_has_no_absence_fallback(self):
        f = FIXTURES["D-V07"]["input"]
        result = self.parsed_860(eia860_bundle(include_owner=f["owner_file_complete"]))
        self.assertIsNone(result["ownership_percent"]); self.assertIn("incomplete_ownership_source", result["gaps"])

    def test_d_v08_complete_matching_vintage_supports_draft_absence_rule(self):
        f = FIXTURES["D-V08"]
        result = self.parsed_860(eia860_bundle())
        self.assertTrue(result["complete"]); self.assertEqual(len(result["rows"]), 1)
        row = result["rows"][0]
        self.assertEqual(row["identity"]["direct_owners"][0]["direct_stake_percent"], f["expected"]["draft_snapshot_direct_stake"])
        self.assertTrue(row["identity"]["direct_owners"][0]["derived_from_absence"])
        self.assertEqual(row["times"]["as_of_basis"], f["expected"]["as_of_basis"])
        self.assertIsNone(row["identity"]["issuer_parent"]); self.assertEqual(row["human_review_status"], "pending")

    def test_d_v09_inconsistent_direct_stakes_are_rejected(self):
        f = FIXTURES["D-V09"]
        self.assertEqual(checked_stakes(f["input"]["same_basis_same_interval_stakes"]), f["expected"])
        parsed = self.parsed_860(eia860_bundle(owners=("0.6", "0.6")))
        self.assertEqual(parsed["rows"][0]["identity"]["direct_owners"], [])
        self.assertIn("overlapping_or_inconsistent_stakes", parsed["rows"][0]["reason_codes"])

    def test_d_v10_unresolved_owner_and_parent_remain_unknown(self):
        f = FIXTURES["D-V10"]
        result = recipient_identity(recipient_uei=f["input"]["recipient_uei"])
        for key, expected in f["expected"].items():
            self.assertEqual(result[key], expected)

    def test_d_v11_effective_date_never_backdates_mapping_knowledge(self):
        f = FIXTURES["D-V11"]["input"]
        result = mapping_visibility({"known_from": f["mapping_known_from"], "valid_from": f["mapping_effective_date"]},
                                    known_by=f["evaluation_known_by"], on="2026-10-01")
        self.assertFalse(result["visible"]); self.assertEqual(result["reason"], "mapping_not_yet_known")

    def test_d_v12_zero_missing_nonfinite_and_pinned_units(self):
        f = FIXTURES["D-V12"]
        rows = self.parsed_eia(eia_payload(f["input"]["values"]))["rows"]
        self.assertEqual([r["facts"]["measurement"]["value"] for r in rows], f["expected"]["normalized_values"])
        self.assertTrue(all(r["facts"]["measurement"]["unit"] == f["input"]["metadata_unit"] for r in rows))
        self.assertIn("nonfinite_or_unsupported_numeric_value", rows[2]["reason_codes"])

    def test_d_v13_cap_and_budget_keep_continuation_and_gap(self):
        f = FIXTURES["D-V13"]["input"]
        session = Mock(); session.request.return_value = reply(eia_payload(["50"] * f["returned_rows"], total=f["advertised_total"]))
        client = self.client("eia-nuclear-data", session=session, max_metadata=1)
        result = collect_eia(client, eia_query(), metadata_snapshot=metadata(), observed_at=NOW,
                             credential_ref="synthetic-handle", enabled=True, max_pages=2, evidence_kind="synthetic")
        self.assertEqual(len(result["rows"]), 250); self.assertEqual(session.request.call_count, 1)
        self.assertFalse(result["complete"]); self.assertIn("result_cap_before_coverage", result["gaps"])
        self.assertEqual(result["continuation"]["offset"], 250)

    def test_d_v14_revised_period_preserves_old_revision_and_requires_review(self):
        f = FIXTURES["D-V14"]["input"]
        old = self.parsed_eia(eia_payload([f["old_value"]])); new = self.parsed_eia(eia_payload([f["new_value"]]))
        result = revision_transition(old["rows"][0], new["rows"][0])
        self.assertTrue(all(result[k] for k in ("append_revision", "old_observation_preserved", "re_review_required")))
        first = persist_batch(self.store, old, observed_at=NOW); second = persist_batch(self.store, new, observed_at=NOW)
        self.assertNotEqual(first["payload"]["candidate_refs"], second["payload"]["candidate_refs"])
        self.assertEqual(get_receipt(self.store, first["payload"]["candidate_refs"][0])["payload"]["facts"]["measurement"]["value"], "50")
        self.assertEqual(persist_batch(self.store, old, observed_at=NOW), first)

    def test_d_v15_signed_obligation_is_separate_from_ceiling_and_option(self):
        f = FIXTURES["D-V15"]["input"]
        payload = transaction_payload(f["signed_obligation_change"], f["transaction"])
        payload["results"][0].update(ceiling=f["ceiling"], unexercised_option=f["unexercised_option"])
        parsed = parse_transactions(payload, approved_query=transaction_query(), observed_at=NOW,
                                    source_policy_fingerprint="synthetic", evidence_kind="synthetic")
        facts = parsed["rows"][0]["facts"]
        self.assertEqual(facts["obligation_change"], "-200000"); self.assertIsNone(facts["award_ceiling"])
        self.assertFalse(facts["option_exercise"]); self.assertFalse(facts["issuer_revenue_inferred"])
        self.assertIsNone(facts["currency"])

    def test_d_v16_financing_authority_is_not_cash_or_runway(self):
        text = FIXTURES["D-V16"]["input"]["text"]
        parsed = parse_amount_sentence(make_doc(text), 0, len(text))
        self.assertEqual(len(parsed["atoms"]), 1)
        atom = parsed["atoms"][0]
        self.assertEqual(atom["role"]["value"], "financing_authority"); self.assertEqual(atom["upper"]["value"], "20000000")
        self.assertIsNone(atom["inferred_cash_received"]); self.assertIsNone(atom["inferred_runway"])

    def test_d_v17_no_budget_keeps_a_visible_backlog(self):
        f = FIXTURES["D-V17"]["input"]
        client = self.client("nrc-aps-search", max_attempts=f["cycle_budget_remaining"])
        with self.assertRaisesRegex(Deferred, "budget_deferred") as caught:
            client.read_json("POST", NRC_URL, contract_id="nrc-aps-search", body=nrc_query(), credential_ref="synthetic-handle")
        lead = deferred_lead(f["candidate_id"], str(caught.exception))
        stored = put_receipt(self.store, "vertical_hydration_backlog", lead, observed_at=NOW)
        self.assertTrue(stored["payload"]["candidate_retained"]); self.assertTrue(lead["backlog_visible"])
        client.session.request.assert_not_called(); client.resolver.assert_not_called()

    def test_d_v18_credential_echo_is_redacted_before_receipts(self):
        f = FIXTURES["D-V18"]["input"]
        canary = "TEST/REDACTION+ONLY"
        payload = {"request": {"params": {"api_key": canary}}, "echo": "https://api.eia.gov/?api_key=" + canary,
                   "encoded": "TEST%2FREDACTION%2BONLY"}
        session = Mock(); session.request.return_value = reply(payload)
        client = self.client("eia-nuclear-metadata", session=session, secret=canary)
        result = client.read_json("GET", EIA_METADATA_URL, contract_id="eia-nuclear-metadata", credential_ref=f["credential_ref"])
        serialized = json.dumps(result)
        self.assertNotIn(canary, serialized); self.assertNotIn("TEST%2FREDACTION%2BONLY", serialized)
        self.assertIn("[REDACTED]", serialized); self.assertEqual(result["receipt"]["credential_ref"], f["credential_ref"])
        self.assertNotIn("api_key", result["receipt"]["url"])

    def test_d_v19_denied_purpose_precedes_dns_and_budget(self):
        f = FIXTURES["D-V19"]["input"]
        client = self.client("usa-award-search", acquire=f["acquire_approved"])
        with self.assertRaisesRegex(PermissionError, "purpose_not_approved"):
            client.read_json(f["method"], USA_AWARD_URL, contract_id="usa-award-search", body=award_query())
        client.resolver.assert_not_called(); client.session.request.assert_not_called()
        self.assertEqual(sum(client.counts.values()), 0)

    def test_d_v20_aggregate_change_cannot_establish_transaction(self):
        f = FIXTURES["D-V20"]["input"]
        payload = {"page_metadata": {"page": 1, "hasNext": False}, "results": [{"generated_internal_id": "CONT_AWD_SYN_001",
                   "Award Amount": f["aggregate_change"], "Recipient UEI": "SYN-UEI"}]}
        result = parse_awards(payload, approved_query=award_query(), observed_at=NOW, source_policy_fingerprint="synthetic")
        self.assertFalse(result["new_award_inferred"]); self.assertFalse(result["new_obligation_inferred"])
        self.assertIn("transaction_and_correction_coverage_unverified", result["gaps"])

    def test_fixture_manifest_is_complete_synthetic_and_not_gold(self):
        self.assertEqual(set(FIXTURES), {"D-V" + str(i).zfill(2) for i in range(1, 21)})
        self.assertTrue(CORPUS["not_gold"]); self.assertTrue(CORPUS["not_pilot_evidence"])
        self.assertTrue(all(f["synthetic"] and f["human_review_status"] == "pending" and not f["signal_eligible"] for f in FIXTURES.values()))

    def test_all_new_collectors_default_disabled(self):
        for name, call in (
            ("nrc-aps-search", lambda c: collect_nrc_aps(c, approved_query=nrc_query(), observed_at=NOW)),
            ("eia-nuclear-data", lambda c: collect_eia(c, eia_query(), metadata_snapshot=metadata(), observed_at=NOW)),
            ("usa-transactions", lambda c: collect_procurement(c, transaction_query(), kind="transactions", observed_at=NOW))):
            with self.subTest(name=name):
                client = self.client(name); result = call(client)
                self.assertIn("adapter_disabled", result["gaps"]); client.resolver.assert_not_called(); client.session.request.assert_not_called()
        self.assertTrue(all(not x["enabled"] for x in adapter_plan()["adapters"]))

    def test_offline_cli_plan_parse_and_draft_receipt_append(self):
        from omni.verticals.__main__ import main
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["vertical-plan"]), 0)
        self.assertEqual(json.loads(out.getvalue())["network_requests"], 0)
        local = Path(self.tmp.name) / "source.json"; local.write_text(json.dumps(nrc_payload()))
        db = Path(self.tmp.name) / "explicit-drafts.db"
        out = StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["--db", str(db), "vertical-parse", "nrc-aps", str(local), "--observed-at", NOW,
                                   "--policy-fingerprint", "synthetic-policy", "--evidence-kind", "synthetic", "--persist"]), 0)
        envelope = json.loads(out.getvalue())
        self.assertEqual(envelope["kind"], "vertical_batch"); self.assertEqual(len(envelope["payload"]["candidate_refs"]), 1)
        self.assertFalse(envelope["payload"]["complete"])

    def test_typed_transport_approval_cannot_be_truthy_or_synthetic_by_default(self):
        client = self.client("usa-transactions")
        client.allow_synthetic_approvals = False
        with self.assertRaisesRegex(PermissionError, "approval_receipt_invalid"):
            client.read_json("POST", USA_TRANSACTION_URL, contract_id="usa-transactions", body=transaction_query())
        client.policy["read_only_requests"][0]["approval_ref"] = "approved"
        with self.assertRaisesRegex(PermissionError, "approval_receipt_invalid"):
            client.read_json("POST", USA_TRANSACTION_URL, contract_id="usa-transactions", body=transaction_query())
        client.resolver.assert_not_called(); self.assertEqual(sum(client.counts.values()), 0)

    def test_transport_approval_revocation_expiry_and_changed_contract_fail_closed(self):
        client = self.client("usa-transactions")
        grant = client.policy["read_only_requests"][0]
        put_receipt(self.store, "attestation_revocation", {"attestation_ref": grant["approval_ref"]}, observed_at=NOW)
        with self.assertRaisesRegex(PermissionError, "approval_receipt_invalid"):
            client.read_json("POST", USA_TRANSACTION_URL, contract_id="usa-transactions", body=transaction_query())
        client = self.client("usa-award-search")
        grant = client.policy["read_only_requests"][0]
        original = get_receipt(self.store, grant["approval_ref"])["payload"]
        expired = put_receipt(self.store, "operator_attestation", {**original, "expires_at": NOW}, observed_at=NOW)
        grant["approval_ref"] = expired["receipt_id"]
        with self.assertRaisesRegex(PermissionError, "approval_receipt_invalid"):
            client.read_json("POST", USA_AWARD_URL, contract_id="usa-award-search", body=award_query())
        grant["contract_hash"] = digest("outdated contract")
        with self.assertRaisesRegex(PermissionError, "contract_not_approved"):
            client.read_json("POST", USA_AWARD_URL, contract_id="usa-award-search", body=award_query())
        client.resolver.assert_not_called()

    def test_document_transport_uses_document_budget(self):
        client = self.client("nrc-aps-document", max_documents=0)
        with self.assertRaisesRegex(Deferred, "budget_deferred"):
            client.read_json("GET", NRC_URL + "/ML24017A120", contract_id="nrc-aps-document", credential_ref="synthetic")
        client.session.request.assert_not_called(); client.resolver.assert_not_called()

    def test_transport_route_method_query_and_body_rejected_before_dns(self):
        for method, url, body in (("DELETE", USA_AWARD_URL, award_query()),
                                  ("POST", USA_AWARD_URL + "other", award_query()),
                                  ("POST", USA_AWARD_URL + "?unknown=1", award_query()),
                                  ("POST", USA_AWARD_URL, {**award_query(), "delete": True}),
                                  ("POST", USA_AWARD_URL, {**award_query(), "fields": [["bad"]]})):
            with self.subTest(method=method, url=url, body=body):
                client = self.client("usa-award-search")
                with self.assertRaises((ValueError, PermissionError)):
                    client.read_json(method, url, contract_id="usa-award-search", body=body)
                client.resolver.assert_not_called(); self.assertEqual(sum(client.counts.values()), 0)

    def test_transport_redirect_private_dns_limits_and_malformed_json(self):
        for response, reason in ((reply(status=302), "http_302"),
                                 (reply(raw=b"x" * 5_000_001), "response_size_limit"),
                                 (reply(raw=b'{"value":NaN}'), "malformed_json"),
                                 (reply(raw=b'{}', headers={"Content-Length": "25"}), "response_length_mismatch")):
            session = Mock(); session.request.return_value = response
            with self.assertRaisesRegex(Deferred, reason):
                self.client("usa-transactions", session=session).read_json("POST", USA_TRANSACTION_URL, contract_id="usa-transactions", body=transaction_query())
            self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        client = self.client("usa-transactions")
        client.resolver.return_value = [(None, None, None, None, ("127.0.0.1", 443))]
        with self.assertRaisesRegex(PermissionError, "nonpublic_dns_destination"):
            client.read_json("POST", USA_TRANSACTION_URL, contract_id="usa-transactions", body=transaction_query())
        client.session.request.assert_not_called()

    def test_transport_retry_budget_backoff_and_error_redaction(self):
        session = Mock(); session.request.side_effect = requests.ConnectionError("synthetic/secret+value")
        client = self.client("usa-transactions", session=session, max_attempts=2)
        with self.assertRaisesRegex(Deferred, "budget_deferred:attempt"):
            client.read_json("POST", USA_TRANSACTION_URL, contract_id="usa-transactions", body=transaction_query())
        self.assertEqual(session.request.call_count, 2)
        session = Mock(); session.request.return_value = reply(status=429, headers={"Retry-After": "120"})
        client = self.client("eia-nuclear-metadata", session=session)
        with self.assertRaisesRegex(Deferred, "source_throttled"):
            client.read_json("GET", EIA_METADATA_URL, contract_id="eia-nuclear-metadata", credential_ref="synthetic")
        other = self.client("eia-nuclear-metadata")
        with self.assertRaisesRegex(Deferred, "source_circuit_open"):
            other.read_json("GET", EIA_METADATA_URL, contract_id="eia-nuclear-metadata", credential_ref="synthetic")
        other.session.request.assert_not_called()

    def test_nrc_body_not_retained_and_document_requires_storage_purpose(self):
        session = Mock(); session.request.return_value = reply(nrc_payload(content="Do not retain indexed document body"))
        client = self.client("nrc-aps-search", session=session)
        result = client.read_json("POST", NRC_URL, contract_id="nrc-aps-search", body=nrc_query(), credential_ref="synthetic")
        self.assertNotIn("content", result["payload"]["results"][0]["document"])
        client = self.client("nrc-aps-document"); client.policy["uses"]["store_text"] = False
        with self.assertRaisesRegex(PermissionError, "purpose_not_approved"):
            client.read_json("GET", NRC_URL + "/ML24017A120", contract_id="nrc-aps-document", credential_ref="synthetic")
        client.resolver.assert_not_called()

    def test_nrc_repeated_page_and_resume_query_mismatch_remain_gaps(self):
        session = Mock(); session.request.return_value = reply(nrc_payload())
        client = self.client("nrc-aps-search", session=session)
        result = collect_nrc_aps(client, approved_query=nrc_query(), observed_at=NOW, credential_ref="synthetic", enabled=True, max_pages=2)
        self.assertIn("repeated_page", result["gaps"]); self.assertEqual(len(result["rows"]), 1)
        with self.assertRaisesRegex(ValueError, "checkpoint_query_or_shape_mismatch"):
            collect_nrc_aps(client, approved_query={**nrc_query(), "q": "another query"}, observed_at=NOW,
                            checkpoint_value=result["continuation"], enabled=True)

    def test_nrc_stages_and_negation_are_distinct(self):
        text = "The applicant submitted an application. NRC accepted the application for review. NRC issued a license."
        result = licensing_stages(text)
        self.assertEqual([s["stage"] for s in result["stages"]], ["application_submitted", "application_accepted", "authorization_issued"])
        self.assertFalse(result["license_issued"])
        self.assertEqual(licensing_stages("NRC has not issued a license.")["stages"], [])

    def test_eia_metadata_tamper_version_unit_or_identity_mismatch_abstains(self):
        snap = metadata(); snap["payload"]["response"]["data"]["syntheticPower"]["units"] = "guessed-unit"
        with self.assertRaisesRegex(ValueError, "metadata_hash_mismatch"):
            parse_eia_observations(eia_payload(), metadata_snapshot=snap, observed_at=NOW, source_policy_fingerprint="synthetic")
        payload = eia_payload(); payload["apiVersion"] = "changed"
        self.assertEqual(self.parsed_eia(payload)["rows"], [])
        payload = eia_payload(); payload["response"]["data"][0]["syntheticPower-units"] = "different-unit"
        result = self.parsed_eia(payload); self.assertIsNone(result["rows"][0]["facts"]["measurement"]["value"])
        self.assertIn("eia_unit_mismatch", result["gaps"])
        payload = eia_payload(); del payload["response"]["data"][0]["syntheticUnit"]
        self.assertEqual(self.parsed_eia(payload)["rows"], [])

    def test_eia_query_requires_pinned_identity_sort_and_forbids_keys(self):
        with self.assertRaisesRegex(ValueError, "unsupported_eia_query_fields"):
            validate_eia_query(eia_query() + [("api_key", "never-input-a-key")], metadata())
        with self.assertRaisesRegex(ValueError, "full_identity_sort_required"):
            validate_eia_query(eia_query()[:-2], metadata())

    def test_eia860_excel_percentage_units_cap_and_formula_boundary(self):
        result = self.parsed_860(eia860_bundle(owners=("0.6", "0.4")))
        shares = result["rows"][0]["identity"]["direct_owners"]
        self.assertEqual([x["direct_stake_percent"] for x in shares], ["60", "40"])
        self.assertEqual(shares[0]["percentage_cell"]["value"]["number_format"], "0.00%")
        self.assertEqual(self.parsed_860(eia860_bundle(owners=("0.6",), percentage_style=False))["rows"][0]["identity"]["direct_owners"], [])
        self.assertFalse(self.parsed_860(eia860_bundle(owners=("0.6",), formula=True))["complete"])
        capped = self.parsed_860(eia860_bundle(owners=("0.6", "0.4")), row_cap=1)
        self.assertIn("incomplete_ownership_source", capped["gaps"])
        with self.assertRaisesRegex(ValueError, "vintage_contract_unverified"):
            parse_eia860_bundle(eia860_bundle(), vintage=2024, observed_at=NOW, source_policy_fingerprint="synthetic")

    def test_crosswalk_requires_registered_entities_and_value_proofs(self):
        raw = {"from_id": "unregistered-unit", "to_id": "unregistered-parent", "role": "directly_owns",
               "source_native_id": "SYN", "source_vintage": "2025", "known_from": NOW, "direct_stake_percent": "60"}
        mapping = {**raw, "proofs": {name: atom(raw, "/" + name) for name in raw}}
        result = validate_crosswalk(mapping, snapshots={digest(raw): raw}, registry=load_registry(), known_by=NOW, on="2026-10-20")
        self.assertFalse(result["structurally_valid"]); self.assertIn("mapping_entity_unresolved", result["reason_codes"])
        bad = atom(raw, "/direct_stake_percent"); bad["support"]["raw_value"] = "100"
        self.assertFalse(check_atom(raw, bad))

    def test_procurement_pagination_and_corrections_never_prove_live_coverage(self):
        session = Mock(); first = transaction_payload(); first["page_metadata"].update(hasNext=True, next=2)
        second = transaction_payload("100", "SYN-MOD-03"); second["page_metadata"]["page"] = 2; second["results"][0]["id"] = "SYN-TRANSACTION-02"
        session.request.side_effect = [reply(first), reply(second)]
        result = collect_procurement(self.client("usa-transactions", session=session), transaction_query(), kind="transactions", observed_at=NOW, enabled=True, max_pages=2)
        self.assertEqual(len(result["rows"]), 2); self.assertTrue(result["acquired_index_set_complete"])
        self.assertFalse(result["complete"]); self.assertFalse(result["correction_surveillance_complete"])
        self.assertIn("transaction_update_deletion_window_unverified", result["gaps"])


if __name__ == "__main__":
    unittest.main()
