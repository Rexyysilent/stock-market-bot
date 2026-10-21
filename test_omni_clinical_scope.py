"""Synthetic scope/table/effect contracts; no human gold or pilot evidence."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from omni.classifier import extract
from omni.clinical_numerics import PREDICATE, parse_sentence, result_projection
from omni.clinical_scope import VERSION, BINDING_VERSION, review_input, version_payload, referenced_documents
from omni.clinical_tables import parse_tables
from omni.evidence import check_span, span
from omni.identities import EntityRegistry, digest, load_registry
from omni.validator import validate
from test_omni_clinical_numerics import BASE
from test_omni_events import make_doc, NOW, POLICY, POLICIES

ROOT = Path(__file__).parent
TABLE_HEADER = "| Trial | Endpoint | Population | Treatment | Comparator | Timepoint | Analysis | Statistics |"
TABLE_ROW = "| KEYNOTE-942 | recurrence-free survival | intention-to-treat | intismeran plus pembrolizumab | pembrolizumab alone | 18 months | prespecified | hazard ratio 0.51; 95% CI 0.29-0.90; two-sided nominal p=0.02 |"
TABLE = TABLE_HEADER + "\n" + TABLE_ROW


def approved_synthetic_catalog():
    text = ("Synthetic protocol NCT03897881; KEYNOTE-942; phase 2b.\n"
            "Effective 2026-01-01 through 2027-01-01.\n"
            "Endpoint recurrence-free survival; alias RFS; primary.\n"
            "Definition time from randomization to recurrence or death.\n"
            "Arm intismeran plus pembrolizumab; alias combo A; treatment.\n"
            "Arm pembrolizumab alone; alias control A; comparator.\n"
            "Population intention-to-treat; alias ITT; all randomized participants; randomized.\n"
            "Cohort cohort alpha; alias cohort A; participants enrolled in synthetic cohort alpha.")
    doc = make_doc(text, native_id="synthetic-protocol-version-1", published="2026-01-01T00:00:00Z")

    def a(value, source=None):
        source = source or value
        start = text.index(source)
        return {"value": value, "support": span(doc, start, start + len(source))}

    row = {"contract": BINDING_VERSION, "trial_id": "trial:NCT03897881",
           "trial_identity": a("trial:NCT03897881", "NCT03897881"), "phase": a("PHASE2B", "phase 2b"),
           "valid_from": a("2026-01-01"), "valid_to": a("2027-01-01"), "source_native_id": doc["native_id"],
           "trial_aliases": [a("KEYNOTE-942")],
           "endpoints": [{"endpoint_id": "endpoint:synthetic-rfs-v1", "label": a("recurrence-free survival"),
                          "definition": a("time from randomization to recurrence or death"),
                          "rank": a("primary"), "aliases": [a("RFS")]}],
           "arms": [{"arm_id": "arm:synthetic-combination", "label": a("intismeran plus pembrolizumab"),
                     "role": a("treatment"), "aliases": [a("combo A")]},
                    {"arm_id": "arm:synthetic-control", "label": a("pembrolizumab alone"),
                     "role": a("comparator"), "aliases": [a("control A")]}],
           "populations": [{"population_id": "population:synthetic-itt", "label": a("intention-to-treat"),
                            "definition": a("all randomized participants"), "denominator_kind": a("randomized"),
                            "aliases": [a("ITT")]}],
           "cohorts": [{"cohort_id": "cohort:synthetic-alpha", "label": a("cohort alpha"),
                        "definition": a("participants enrolled in synthetic cohort alpha"), "aliases": [a("cohort A")]}],
           "documents": [doc], "policies": deepcopy(POLICIES)}
    seal_synthetic_version(row)
    return {"contract": VERSION, "trial_versions": [row]}


def seal_synthetic_version(row):
    row["revision_id"] = digest(version_payload(row))
    row["review"] = {"decision": "approve", "reviewer": "synthetic-test-reviewer-not-human-evidence",
                     "reviewed_at": NOW, "input_hash": review_input(row, row["documents"], row["policies"])}


class ClinicalScopeTests(unittest.TestCase):
    def setUp(self):
        self.catalog = approved_synthetic_catalog()
        self.registry = EntityRegistry(load_registry().data, clinical_catalog=self.catalog)

    def parse(self, text=BASE, registry=None, **kwargs):
        doc = make_doc(text, **kwargs)
        return doc, parse_sentence(doc, 0, len(text), registry or self.registry, known_by=NOW)

    def test_pending_catalog_cannot_supply_canonical_mapping(self):
        self.catalog["trial_versions"][0]["review"] = {"decision": "pending"}
        registry = EntityRegistry(load_registry().data, clinical_catalog=self.catalog)
        _, out = self.parse(registry=registry)
        self.assertFalse(out["results"])
        self.assertEqual(out["abstentions"][0]["reason"], "clinical_scope_version_unavailable_at_disclosure")

    def test_exact_catalog_and_alias_binding_remain_pending(self):
        doc, out = self.parse()
        self.assertEqual(len(out["results"]), 1, out)
        result = out["results"][0]
        binding = result["scope_binding"]
        self.assertEqual(binding["bindings"]["endpoint"]["canonical_id"], "endpoint:synthetic-rfs-v1")
        self.assertEqual(binding["human_review_status"], "pending")
        self.assertFalse(result["signal_eligible"])
        self.assertTrue(check_span(doc, binding["bindings"]["endpoint"]["source_mention"]))
        alias = BASE.replace("recurrence-free survival", "RFS").replace("intention-to-treat", "ITT")
        alias = alias.replace("intismeran plus pembrolizumab", "combo A").replace("pembrolizumab alone", "control A")
        _, second = self.parse(alias)
        self.assertEqual(second["results"][0]["analysis_scope_id"], result["analysis_scope_id"])
        self.assertEqual(second["results"][0]["result_id"], result["result_id"])

    def test_unknown_endpoint_arm_population_and_cohort_aliases_abstain(self):
        for old, new in (("recurrence-free survival", "event-free survival"),
                         ("intismeran plus pembrolizumab", "unsupported combination"),
                         ("intention-to-treat", "evaluable participants"),
                         ("analysis: prespecified", "cohort: cohort beta; analysis: prespecified")):
            with self.subTest(new=new):
                _, out = self.parse(BASE.replace(old, new))
                self.assertFalse(out["results"])
                self.assertTrue(any("unmapped" in r for r in out["abstentions"][0]["candidate"]["abstention_reasons"]))

    def test_cohort_alias_is_explicit_and_does_not_replace_population(self):
        _, out = self.parse(BASE.replace("analysis: prespecified", "cohort: cohort A; analysis: prespecified"))
        self.assertEqual(out["results"][0]["scope_binding"]["bindings"]["cohort"]["canonical_id"], "cohort:synthetic-alpha")
        self.assertEqual(out["results"][0]["scope_binding"]["bindings"]["analysis_population"]["canonical_id"], "population:synthetic-itt")

    def test_primary_secondary_and_arm_direction_are_checked(self):
        changes = [("rank: primary", "rank: secondary", "clinical_scope_endpoint_rank_mismatch"),
                   ("treatment: intismeran plus pembrolizumab; comparator: pembrolizumab alone",
                    "treatment: pembrolizumab alone; comparator: intismeran plus pembrolizumab", "clinical_scope_arm_role_mismatch"),
                   ("Phase 2b", "Phase 3", "clinical_scope_trial_phase_mismatch")]
        for old, new, reason in changes:
            _, out = self.parse(BASE.replace(old, new))
            self.assertFalse(out["results"])
            self.assertIn(reason, out["abstentions"][0]["candidate"]["abstention_reasons"])

    def test_legacy_current_phase_cannot_overwrite_pinned_disclosure_version(self):
        data = deepcopy(self.registry.data)
        next(e for e in data["entities"] if e["entity_id"] == "trial:NCT03897881")["phase"] = "PHASE3"
        registry = EntityRegistry(data)
        doc, out = self.parse(registry=registry)
        self.assertTrue(out["results"])
        event = extract(doc, registry, POLICY, known_by=NOW)["events"][0]
        decision = validate(event, [doc], registry, POLICIES, now=NOW)
        self.assertNotIn("trial_phase_mismatch", decision["reason_codes"])

    def test_disclosure_time_and_available_version_do_not_use_observation_time(self):
        for published in (None, "2025-12-31", "2026", "2027-01-01T00:00:00Z"):
            _, out = self.parse(published=published)
            self.assertFalse(out["results"])
        row = self.catalog["trial_versions"][0]
        row["documents"][0] = make_doc(row["documents"][0]["text"], native_id=row["source_native_id"], published="2026-10-18")
        # Update every proof to the later exact revision, then create a synthetic
        # receipt. Availability remains date-only and cannot precede 10:00 UTC.
        document = row["documents"][0]
        def refresh(value):
            if isinstance(value, dict):
                if "support" in value:
                    p=value["support"];value["support"]=span(document,p["start"],p["end"])
                for key, item in value.items():
                    if key not in ("documents", "support"): refresh(item)
            elif isinstance(value,list):
                for item in value: refresh(item)
        refresh(row);seal_synthetic_version(row)
        _, out = self.parse(registry=EntityRegistry(load_registry().data, clinical_catalog=self.catalog))
        self.assertFalse(out["results"])

    def test_overlapping_versions_and_source_policy_drift_abstain(self):
        catalog = deepcopy(self.catalog);catalog["trial_versions"].append(deepcopy(catalog["trial_versions"][0]))
        _, out = self.parse(registry=EntityRegistry(load_registry().data, clinical_catalog=catalog))
        self.assertEqual(out["abstentions"][0]["reason"], "clinical_scope_version_ambiguous")
        catalog = deepcopy(self.catalog);row=catalog["trial_versions"][0]
        row["policies"][POLICY["policy_id"]]["uses"]["local_analysis"] = False
        seal_synthetic_version(row)
        _, out=self.parse(registry=EntityRegistry(load_registry().data,clinical_catalog=catalog))
        self.assertFalse(out["results"])

    def test_pending_competing_version_cannot_leave_old_definition_in_force(self):
        draft=deepcopy(self.catalog['trial_versions'][0]);draft['review']={'decision':'pending'}
        self.catalog['trial_versions'].append(draft)
        _,out=self.parse(registry=EntityRegistry(load_registry().data,clinical_catalog=self.catalog))
        self.assertFalse(out['results'])
        self.assertEqual(out['abstentions'][0]['reason'],'clinical_scope_competing_version_review_required')

    def test_definition_sources_have_separate_current_purpose_and_lineage_checks(self):
        row=self.catalog['trial_versions'][0]
        definition_policy=deepcopy(POLICY);definition_policy['policy_id']='synthetic-protocol'
        old=row['documents'][0]
        definition_doc=make_doc(old['text'],policy=definition_policy,native_id=old['native_id'],published=old['publication']['raw'])
        def replace_proofs(value):
            if isinstance(value,dict):
                if 'support' in value:
                    proof=value['support'];value['support']=span(definition_doc,proof['start'],proof['end'])
                for key,item in value.items():
                    if key not in ('support','documents','policies'):replace_proofs(item)
            elif isinstance(value,list):
                for item in value:replace_proofs(item)
        replace_proofs(row);row['documents']=[definition_doc];row['policies']={definition_policy['policy_id']:definition_policy}
        seal_synthetic_version(row)
        registry=EntityRegistry(load_registry().data,clinical_catalog=self.catalog)
        doc=make_doc(BASE);event=extract(doc,registry,POLICY,known_by=NOW)['events'][0]
        references=referenced_documents(event,registry)
        self.assertEqual([(d['evidence_id'],d['revision_id']) for d in references],[(definition_doc['evidence_id'],definition_doc['revision_id'])])
        missing=validate(event,[doc],registry,POLICIES,now=NOW)
        self.assertIn('source_not_approved',missing['reason_codes'])
        current={**POLICIES,definition_policy['policy_id']:definition_policy}
        present=validate(event,[doc],registry,current,now=NOW)
        self.assertNotIn('outside_recency',present['reason_codes'])
        self.assertNotIn('source_not_approved',present['reason_codes'])
        current=deepcopy(current);current[definition_policy['policy_id']]['uses']['review_display']=False
        revoked=validate(event,[doc],registry,current,now=NOW)
        self.assertIn('purpose_not_approved',revoked['reason_codes'])
        self.assertIn('clinical_scope_policy_snapshot_mismatch',revoked['reason_codes'])

    def test_changed_definition_needs_new_review_and_changes_identity(self):
        _, original = self.parse()
        row=self.catalog["trial_versions"][0]
        row["endpoints"][0]["endpoint_id"]="endpoint:synthetic-revised-definition"
        row["revision_id"]=digest(version_payload(row))
        registry=EntityRegistry(load_registry().data,clinical_catalog=self.catalog)
        self.assertFalse(self.parse(registry=registry)[1]["results"])
        seal_synthetic_version(row)
        _, revised=self.parse(registry=EntityRegistry(load_registry().data,clinical_catalog=self.catalog))
        self.assertNotEqual(original["results"][0]["analysis_scope_id"],revised["results"][0]["analysis_scope_id"])

    def test_forged_span_and_real_source_using_synthetic_catalog_are_rejected(self):
        row=self.catalog["trial_versions"][0];row["endpoints"][0]["definition"]["support"]["text"]="fabricated"
        seal_synthetic_version(row)
        self.assertFalse(self.parse(registry=EntityRegistry(load_registry().data,clinical_catalog=self.catalog))[1]["results"])
        self.assertFalse(self.parse(evidence_kind="historical_replay")[1]["results"])

    def test_validator_reparses_mapping_and_missing_catalog(self):
        doc=make_doc(BASE);event=extract(doc,self.registry,POLICY,known_by=NOW)["events"][0]
        before=validate(event,[doc],self.registry,POLICIES,now=NOW)
        self.assertNotIn("unsupported_clinical_numeric_result",before["reason_codes"])
        changed=deepcopy(event)
        claim=next(c for c in changed["claims"] if c["predicate"]==PREDICATE)
        claim["numeric_result"]["scope_binding"]["bindings"]["endpoint"]["canonical_id"]="wrong"
        changed["clinical_context"]["numerical_results"]=result_projection(changed["claims"])
        self.assertIn("unsupported_clinical_numeric_result",validate(changed,[doc],self.registry,POLICIES,now=NOW)["reason_codes"])
        self.assertIn("unsupported_clinical_numeric_result",validate(event,[doc],load_registry(),POLICIES,now=NOW)["reason_codes"])

    def test_catalog_schema_and_all_accepted_result_schemas(self):
        import jsonschema
        catalog_schema=json.loads((ROOT/'schemas/omni-clinical-scope.schema.json').read_text())
        result_schema=json.loads((ROOT/'schemas/omni-clinical-numerics.schema.json').read_text())
        for schema in (catalog_schema,result_schema):jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(self.catalog,catalog_schema)
        jsonschema.validate(self.parse()[1]["results"][0],result_schema)


class ClinicalFormsTests(unittest.TestCase):
    def setUp(self):self.registry=load_registry()

    def parse(self,text):
        doc=make_doc(text)
        return doc,parse_sentence(doc,0,len(text),self.registry,known_by=NOW)

    def test_declared_clinical_scope_fixture_rules(self):
        corpus=json.loads((ROOT/'fixtures/omni/clinical_scope.json').read_text())
        self.assertTrue(corpus['synthetic']);self.assertEqual(corpus['human_review_status'],'pending')
        for row in corpus['cases']:
            with self.subTest(fixture=row['fixture_id']):
                text=row.get('text',BASE)
                for old,new in row.get('replace',[]):
                    self.assertIn(old,text);text=text.replace(old,new)
                registry=self.registry
                if row['mode'] in ('catalog','pending_catalog'):
                    catalog=approved_synthetic_catalog()
                    if row['mode']=='pending_catalog':catalog['trial_versions'][0]['review']={'decision':'pending'}
                    registry=EntityRegistry(self.registry.data,clinical_catalog=catalog)
                doc=make_doc(text)
                out=(parse_tables(doc,registry,known_by=NOW) if row['mode']=='table' else
                     parse_sentence(doc,0,len(text),registry,known_by=NOW))
                self.assertEqual(len(out['results']),row['expected_results'],out)
                if row.get('expected_reason'):
                    reasons={a['reason'] for a in out['abstentions']}
                    for lead in out['abstentions']:reasons.update((lead.get('candidate') or {}).get('abstention_reasons',[]))
                    self.assertIn(row['expected_reason'],reasons)

    def test_non_hr_measures_keep_exact_measure_units_and_signed_bounds(self):
        for expression,measure in (("risk ratio=0.51 (95% CI 0.29-0.90)","risk_ratio"),
                                   ("odds ratio=0.51 (95% CI 0.29-0.90)","odds_ratio"),
                                   ("risk difference=-5 percentage points (95% CI [-7, -3] percentage points)","risk_difference")):
            doc,out=self.parse(BASE.replace("hazard ratio 0.51 (95% CI 0.29-0.90)",expression))
            self.assertEqual(len(out["results"]),1,out)
            stats=out["results"][0]["statistics"]
            self.assertIsNone(stats["hazard_ratio"])
            self.assertEqual(stats["effect_measure"]["measure"]["value"],measure)
            self.assertTrue(check_span(doc,stats["effect_measure"]["estimate"]["support"]))

    def test_non_hr_unsupported_units_methods_and_unconsumed_statistics_abstain(self):
        for expression in ("risk difference=5%", "risk difference=5 points", "risk ratio=-1", "odds ratio=2%",
                           "relative risk=0.5", "RR=0.5", "median survival=12 months", "adjusted odds ratio=0.5",
                           "hazard ratio 0.51 and risk ratio 0.5"):
            with self.subTest(expression=expression):
                _,out=self.parse(BASE.replace("hazard ratio 0.51 (95% CI 0.29-0.90)",expression))
                self.assertFalse(out["results"])
                self.assertTrue(out["abstentions"])

    def test_scope_limits_markup_and_negated_statistic_are_explicit_refusals(self):
        for old,new in [('population: intention-to-treat','population: '+('longpopulation '*20)),
                        ('treatment: intismeran plus pembrolizumab','treatment: <b>intismeran</b> plus pembrolizumab'),
                        ('hazard ratio 0.51','no hazard ratio 0.51')]:
            _,out=self.parse(BASE.replace(old,new));self.assertFalse(out['results']);self.assertTrue(out['abstentions'])

    def test_multiplicity_methods_no_alpha_and_ci_side_remain_explicit(self):
        _,out=self.parse(BASE.replace("nominal p=0.02","Holm-adjusted p=0.02"))
        self.assertEqual(out["results"][0]["statistics"]["p_value"]["adjustment_method"]["value"],"holm")
        _,out=self.parse(BASE.replace("analysis: prespecified;","analysis: prespecified; multiplicity: no alpha allocated;"))
        self.assertEqual(out["results"][0]["scope"]["multiplicity"]["value"],"no alpha allocated")
        _,out=self.parse(BASE.replace("95% CI","two-sided 95% CI"))
        self.assertEqual(out["results"][0]["statistics"]["confidence_interval"]["sidedness"]["value"],"two_sided")
        for replacement in ("Sidak-adjusted p=0.02","Benjamini-Hochberg adjusted p=0.02","log-rank p=0.02"):
            self.assertFalse(self.parse(BASE.replace("nominal p=0.02",replacement))[1]["results"])
        contradictory=BASE.replace("analysis: prespecified;","analysis: prespecified; multiplicity: not adjusted for multiplicity;")
        self.assertFalse(self.parse(contradictory.replace("nominal p=0.02","Holm-adjusted p=0.02"))[1]["results"])

    def test_censoring_unevaluable_and_severity_never_become_efficacy_or_seriousness(self):
        for suffix,reason in ((" Eight participants were censored.","numeric_population_disposition_unsupported"),
                              (" Two participants were unevaluable.","numeric_population_disposition_unsupported"),
                              (" Grade 3 ALT elevations were reported.","numeric_safety_scope_unsupported"),
                              (" No serious adverse events occurred.","numeric_safety_scope_unsupported")):
            _,out=self.parse(BASE+suffix)
            self.assertFalse(out["results"]);self.assertEqual(out["abstentions"][0]["reason"],reason)

    def test_adjacent_disposition_or_multiplicity_caveat_cannot_be_cropped(self):
        for suffix in (' These estimates exclude unevaluable participants.', ' No alpha was allocated to these analyses.'):
            doc=make_doc(BASE+suffix);out=extract(doc,self.registry,POLICY,known_by=NOW)
            self.assertFalse(any(c['predicate']==PREDICATE for e in out['events'] for c in e['claims']))
            self.assertIn('numeric_adjacent_scope_unsupported',{lead['reason'] for lead in out['leads']})

    def test_table_complete_rows_have_exact_cell_and_header_proofs(self):
        doc=make_doc(TABLE);out=parse_tables(doc,self.registry,known_by=NOW)
        self.assertEqual(len(out["results"]),1,out)
        result=out["results"][0]
        for col in result["table_proof"]["columns"]:
            self.assertTrue(check_span(doc,col["header"]));self.assertTrue(check_span(doc,col["cell"]))
        event=extract(doc,self.registry,POLICY,known_by=NOW)["events"][0]
        decision=validate(event,[doc],self.registry,POLICIES,now=NOW)
        self.assertNotIn("unsupported_clinical_numeric_result",decision["reason_codes"])
        self.assertIn("clinical_result_incomplete",decision["reason_codes"])

    def test_table_repeated_rows_deduplicate_but_changed_rows_conflict(self):
        for last,expected in ((TABLE_ROW,1),(TABLE_ROW.replace("ratio 0.51","ratio 0.60"),2)):
            doc=make_doc(TABLE+"\n"+last);event=extract(doc,self.registry,POLICY,known_by=NOW)["events"][0]
            self.assertEqual(len(event["clinical_context"]["numerical_results"]),expected)
            decision=validate(event,[doc],self.registry,POLICIES,now=NOW)
            self.assertEqual("contradiction_unresolved" in decision["reason_codes"],expected==2)

    def test_table_footnote_merged_cells_column_swap_or_missing_scope_refuse(self):
        cases=[TABLE.replace("| Population |","| Population | Population |"),
               TABLE.replace("| intention-to-treat |","| |"),TABLE.replace("intention-to-treat","intention-to-treat*"),
               TABLE+"\n* The comparison population differed.",TABLE+"\n\nNote: p is exploratory.",
               TABLE.replace("| Timepoint |","| Followup |"),TABLE.replace("| Trial | Endpoint |","| Endpoint | Trial |"),
               TABLE.replace("hazard ratio 0.51","0.51"),"Caption: prior results\n"+TABLE,
               TABLE+"\n"+TABLE_ROW.replace("KEYNOTE-942","ditto")]
        for text in cases:
            with self.subTest(text=text[:70]):
                doc=make_doc(text);out=parse_tables(doc,self.registry,known_by=NOW)
                self.assertFalse(out["results"]);self.assertTrue(out["abstentions"])

    def test_table_validator_rejects_changed_header_and_cropped_statistics(self):
        doc=make_doc(TABLE);event=extract(doc,self.registry,POLICY,known_by=NOW)["events"][0]
        changed=deepcopy(event);claim=changed["claims"][0]
        claim["numeric_result"]["table_proof"]["columns"][0]["name"]="endpoint"
        self.assertIn("unsupported_clinical_numeric_result",validate(changed,[doc],self.registry,POLICIES,now=NOW)["reason_codes"])
        changed=deepcopy(event);claim=changed["claims"][0]
        claim["support"]=span(doc,doc["text"].index("hazard ratio"),doc["text"].rindex("|"))
        self.assertIn("unsupported_clinical_numeric_result",validate(changed,[doc],self.registry,POLICIES,now=NOW)["reason_codes"])

    def test_extended_schema_for_tables_effects_and_multiplicity(self):
        import jsonschema
        schema=json.loads((ROOT/'schemas/omni-clinical-numerics.schema.json').read_text())
        texts=[BASE.replace("hazard ratio 0.51","risk ratio=0.51"),BASE.replace("nominal p=0.02","Bonferroni-adjusted p=0.02"),BASE.replace("95% CI","two-sided 95% CI")]
        results=[self.parse(text)[1]["results"][0] for text in texts]
        results.extend(parse_tables(make_doc(TABLE),self.registry,known_by=NOW)["results"])
        for result in results:jsonschema.validate(result,schema)


if __name__=="__main__":unittest.main()
