"""Synthetic scoped money controls; values are not issuer revenue or cash."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from omni.amounts import (SCOPED_PREDICATE, parse_amount_sentence, amount_projection,
                          contract_amounts)
from omni.classifier import extract
from omni.evidence import check_span, span
from omni.identities import load_registry
from omni.validator import validate
from test_omni_events import make_doc, NOW, POLICY, POLICIES

ROOT=Path(__file__).parent


def describe(atom):
    return {key:(atom[key] or {}).get("value") for key in
            ("role","currency","operator","value","lower","upper","additivity")}


class ScopedAmountTests(unittest.TestCase):
    def setUp(self):self.registry=load_registry()

    def parse(self,text,**kwargs):
        doc=make_doc(text,**kwargs)
        return doc,parse_amount_sentence(doc,0,len(text))

    def event(self,text):
        doc=make_doc(text);events=extract(doc,self.registry,POLICY,known_by=NOW)["events"]
        return doc,next(e for e in events if any(c["predicate"]==SCOPED_PREDICATE for c in e["claims"]))

    def test_postposed_bounds_never_become_exact_amounts(self):
        prefix = "Moderna announced a contract obligation of USD 10 million"
        _, control = self.parse(prefix + ".")
        self.assertEqual(control["atoms"][0]["operator"]["value"], "equal")
        for suffix in (" or more", " or less", " at minimum", " at maximum", " minimum", " maximum"):
            with self.subTest(suffix=suffix):
                text = prefix + suffix + "."
                doc, result = self.parse(text)
                self.assertEqual(contract_amounts(text, stage="obligation"), [])
                self.assertEqual(result["atoms"], [])
                self.assertEqual(result["abstentions"][0]["reason"], "amount_operator_unsupported")
                events = extract(doc, self.registry, POLICY, known_by=NOW)["events"]
                self.assertFalse(any(c["predicate"] in {SCOPED_PREDICATE, "contract_obligation_amount"}
                                     for event in events for c in event["claims"]))

    def test_twelve_existing_scenarios_keep_legacy_and_declared_new_outcomes(self):
        fixture=json.loads((ROOT/'fixtures/omni/coverage_rules.json').read_text())
        self.assertEqual(len(fixture['amount_cases']),12)
        for row in fixture['amount_cases']:
            with self.subTest(fixture=row['fixture_id']):
                self.assertEqual([list(x) for x in contract_amounts(row['text'],stage=row['stage'])],row['expected_existing_results'])
                _,out=self.parse(row['text'])
                self.assertEqual([describe(a) for a in out['atoms']],row['expected_scoped_atoms'])
                if row.get('expected_scoped_reason'):
                    self.assertEqual(out['abstentions'][0]['reason'],row['expected_scoped_reason'])

    def test_extended_positive_and_ambiguous_fixtures(self):
        fixture=json.loads((ROOT/'fixtures/omni/scoped_amounts.json').read_text())
        self.assertTrue(fixture['synthetic']);self.assertEqual(fixture['human_review_status'],'pending')
        for row in fixture['cases']:
            with self.subTest(fixture=row['fixture_id']):
                _,out=self.parse(row['text'])
                self.assertEqual([describe(a) for a in out['atoms']],row['expected_atoms'])
                if row.get('expected_reason'):self.assertEqual(out['abstentions'][0]['reason'],row['expected_reason'])

    def test_every_material_atom_has_exact_revision_support_and_schema(self):
        import jsonschema
        schema=json.loads((ROOT/'schemas/omni-scoped-amount.schema.json').read_text())
        jsonschema.Draft202012Validator.check_schema(schema)
        texts=[row['text'] for row in json.loads((ROOT/'fixtures/omni/coverage_rules.json').read_text())['amount_cases']]
        texts.extend(row['text'] for row in json.loads((ROOT/'fixtures/omni/scoped_amounts.json').read_text())['cases'])
        for text in texts:
            doc,out=self.parse(text)
            for atom in out['atoms']:
                jsonschema.validate(atom,schema)
                def walk(value):
                    if isinstance(value,dict):
                        if 'support' in value:self.assertTrue(check_span(doc,value['support']))
                        for key,child in value.items():
                            if key!='support':walk(child)
                    elif isinstance(value,list):
                        for item in value:walk(item)
                walk(atom)
                self.assertEqual(atom['source_revision_id'],doc['revision_id'])
                for key in ('inferred_revenue','inferred_cash_received','inferred_runway'):self.assertIsNone(atom[key])

    def test_included_and_incremental_options_bind_the_source_target_without_sum(self):
        for word,relation in [('including','included'),('additional','incremental')]:
            _,out=self.parse('Synthetic contract ceiling is USD 5 million; '+word+' USD 1 million options.')
            self.assertEqual(len(out['atoms']),2,out)
            option=out['atoms'][1]
            self.assertEqual(option['additivity']['value'],relation)
            self.assertEqual(option['additivity_target']['value']['value'],'5000000')
            self.assertEqual(option['value']['value'],'1000000')
            self.assertNotIn('aggregate',option)

    def test_currency_period_identity_and_option_status_unknowns_are_not_filled(self):
        _,out=self.parse('Synthetic contract has $5 million options.')
        atom=out['atoms'][0]
        for key in ('currency','period','contract_or_financing_id','option_status'):self.assertIsNone(atom[key])
        self.assertEqual(atom['missing_fields'],['currency','period','contract_or_financing_id'])
        _,out=self.parse('Synthetic contract id: SYN-1; exercised option is CAD 1.25 million for 2 years.')
        atom=out['atoms'][0]
        self.assertEqual(atom['contract_or_financing_id']['value'],'SYN-1')
        self.assertEqual(atom['option_status']['value'],'exercised')
        self.assertEqual(atom['period']['kind']['value'],'duration')
        self.assertEqual(atom['period']['value']['value'],'2')

    def test_current_obligation_change_and_reduction_are_separate_kinds(self):
        for text,kind,value in [('current obligation of USD 2 million','current','2000000'),
                                ('obligation change of USD -2 million','transaction_change','-2000000'),
                                ('deobligation of USD 2 million','reduction','2000000')]:
            _,out=self.parse('Synthetic contract reports '+text+'.')
            self.assertEqual(out['atoms'][0]['obligation_kind']['value'],kind)
            self.assertEqual(out['atoms'][0]['value']['value'],value)

    def test_condition_words_do_not_assert_exercise_or_amount_frequency(self):
        _,out=self.parse('Synthetic contract option is USD 1 million if exercised per year.')
        atom=out['atoms'][0]
        self.assertEqual(atom['condition']['value'],'if exercised per year')
        self.assertIsNone(atom['option_status']);self.assertIsNone(atom['period'])
        text='Synthetic contract ceiling is USD 5 million subject to '+('milestones '*20)+'.'
        self.assertEqual(self.parse(text)[1]['abstentions'][0]['reason'],'amount_condition_limit')

    def test_negated_or_unsupported_realization_never_creates_an_amount_claim(self):
        for text in ('Moderna denied a contract ceiling of USD 5 million.',
                     "Moderna didn't report a contract ceiling of USD 5 million.",
                     'Moderna reports expected net proceeds of USD 5 million.',
                     'Moderna reports USD 5 million to be obligated under the contract.'):
            with self.subTest(text=text):
                doc,out=self.parse(text);self.assertFalse(out['atoms']);self.assertTrue(out['abstentions'])
                event_output=extract(doc,self.registry,POLICY,known_by=NOW)
                self.assertFalse(any(c['predicate']==SCOPED_PREDICATE or c['predicate'].endswith('_amount')
                                     for e in event_output['events'] for c in e['claims']))

    def test_bounds_ranges_and_approximations_never_supply_legacy_exact_scalar(self):
        for phrase,operator in [('less than USD 5 million','<'),('at most USD 5 million','<='),
                                ('at least USD 5 million','>='),('more than USD 5 million','>'),
                                ('between USD 3 million and USD 5 million','range'),('about USD 5 million','approximate')]:
            text='Synthetic contract ceiling is '+phrase+'.'
            _,out=self.parse(text);atom=out['atoms'][0]
            self.assertEqual(atom['operator']['value'],operator)
            if operator!='approximate':self.assertIsNone(atom['value'])
            self.assertEqual(contract_amounts(text,stage='ceiling'),[])

    def test_numeric_projection_is_present_and_validator_reparses_full_sentence(self):
        doc,event=self.event('Moderna reports contract ceiling of USD 5 million, including USD 1 million options.')
        self.assertEqual(len(event['scoped_amounts']),2)
        decision=validate(event,[doc],self.registry,POLICIES,now=NOW)
        self.assertNotIn('unsupported_scoped_amount',decision['reason_codes'])
        self.assertNotIn('amount_context_not_supported',decision['reason_codes'])
        self.assertIn('review_required',decision['reason_codes'])
        for mutation in ('role','value','currency','upper','condition','period','inferred_cash_received','projection'):
            changed=deepcopy(event)
            if mutation=='projection':changed['scoped_amounts'][0]['value']['value']='6000000'
            else:
                claim=next(c for c in changed['claims'] if c['predicate']==SCOPED_PREDICATE)
                claim['amount_atom'][mutation]={'value':'invented','support':claim['support']}
            outcome=validate(changed,[doc],self.registry,POLICIES,now=NOW)
            self.assertTrue({'unsupported_scoped_amount','amount_context_not_supported'} & set(outcome['reason_codes']),mutation)
        changed=deepcopy(event);claim=next(c for c in changed['claims'] if c['predicate']==SCOPED_PREDICATE)
        end=doc['text'].index(', including')
        claim['support']=span(doc,0,end)
        self.assertIn('unsupported_scoped_amount',validate(changed,[doc],self.registry,POLICIES,now=NOW)['reason_codes'])

    def test_same_scope_conflicts_and_distinct_roles_do_not_share_outcomes(self):
        doc,event=self.event('Moderna reports contract ceiling of USD 5 million and ceiling of USD 6 million.')
        self.assertIn('contradiction_unresolved',validate(event,[doc],self.registry,POLICIES,now=NOW)['reason_codes'])
        doc,event=self.event('Moderna reports contract obligation of USD 2 million against the ceiling of USD 5 million.')
        self.assertNotIn('contradiction_unresolved',validate(event,[doc],self.registry,POLICIES,now=NOW)['reason_codes'])

    def test_revised_or_replayed_evidence_cannot_replace_exact_support(self):
        doc,event=self.event('Moderna reports gross proceeds of USD 5 million and net proceeds of USD 4 million.')
        newer=make_doc(doc['text'].replace('USD 5','USD 6'))
        self.assertIn('unsupported_claim',validate(event,[newer],self.registry,POLICIES,now=NOW)['reason_codes'])
        _,copy_event=self.event(doc['text'])
        projected=amount_projection(event['claims']+copy_event['claims'])
        self.assertEqual(len(projected),2)

    def test_future_proceeds_are_planned_and_atm_authority_is_not_received_cash(self):
        doc,event=self.event('Moderna expects gross proceeds of USD 5 million.')
        self.assertIn('planned_not_actual',validate(event,[doc],self.registry,POLICIES,now=NOW)['reason_codes'])
        doc,event=self.event('Moderna may sell up to $20 million under an ATM facility.')
        atom=event['scoped_amounts'][0]
        self.assertEqual(atom['role']['value'],'financing_authority')
        self.assertEqual(atom['upper']['value'],'20000000')
        self.assertIsNone(atom['currency']);self.assertIsNone(atom['inferred_cash_received'])


if __name__=='__main__':unittest.main()
