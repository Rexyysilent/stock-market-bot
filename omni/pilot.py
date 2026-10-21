"""Version-bound prospective pilot receipts. This module never adjudicates a case."""
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from .identities import ROOT,digest,instant
from .review import event_packet
from .store import encode
from .evaluation import evaluate

VERSION='omni-pilot-3'


def implementation_hash(root=ROOT):
    files=sorted((root/'omni').rglob('*.py'))+[root/'source_request_gate.py',root/'editorial_focus.py']
    return digest({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def pilot_plan(registry,policies,coverage):
    return {'contract':VERSION,'implementation_hash':implementation_hash(),
        'source_scope_hash':digest(policies),'registry_fingerprint':registry.fingerprint(),
        'captured_coverage':list(coverage),'coverage_fingerprint':digest(list(coverage)),
        'status':'draft_pending_reviewed_corpus','corpus_hash':None,'frozen_at':None,
        'session_timezone':'America/New_York','session_rule':'local_run_start_date',
        'human_cases':0,'scheduled_sessions':0,'automatic_activation':False}


def freeze_pilot(store,registry,policies,coverage,corpus,*,now):
    """Explicit operator command; requires a reviewed prespecified source corpus."""
    clock=instant(now)
    if not isinstance(corpus,list) or not corpus: raise ValueError('reviewed corpus required; empty pilot stays draft')
    seen=set()
    for row in corpus:
        if (not row.get('event_id') or row['event_id'] in seen or not row.get('reviewed_by')
                or not row.get('adjudication_receipt') or not row.get('source_url')
                or row.get('prespecified') is not True or row.get('in_source_scope') is not True
                or row.get('evidence_kind') not in ('historical_replay','prospective')):
            raise ValueError('corpus row is incomplete, repeated or synthetic')
        if instant(row['reviewed_at'])>clock: raise ValueError('future corpus review')
        seen.add(row['event_id'])
    plan=pilot_plan(registry,policies,coverage)
    plan.update(status='frozen_manual_only',corpus_hash=digest(corpus),frozen_at=now)
    plan['pilot_id']='pilot:'+digest(plan)
    _append(store,plan['pilot_id'],'manifest',now,{'manifest':plan,'corpus':corpus})
    return plan


def _append(store,pilot_id,kind,at,payload):
    instant(at); record_id='pilot-record:'+digest([pilot_id,kind,payload])
    with store.conn:
        store.conn.execute('INSERT OR IGNORE INTO pilot_records VALUES(?,?,?,?,?)',(record_id,pilot_id,kind,at,encode(payload)))
    return record_id


def _manifest(store,pilot_id,registry,policies,*,require_current=True):
    row=store.conn.execute("SELECT payload FROM pilot_records WHERE pilot_id=? AND kind='manifest'",(pilot_id,)).fetchone()
    if not row: raise ValueError('unknown pilot')
    data=json.loads(row[0]); manifest=data['manifest']
    compatible=(manifest['implementation_hash']==implementation_hash() and manifest['source_scope_hash']==digest(policies)
            and manifest['registry_fingerprint']==registry.fingerprint())
    if require_current and not compatible:
        raise ValueError('pilot versions changed; freeze a new pilot and retain old evidence')
    data['current_versions_match']=compatible
    return data


def record_case(store,pilot_id,registry,policies,adjudication,*,now):
    """Record a supplied human decision bound to exact observed evidence."""
    manifest=_manifest(store,pilot_id,registry,policies)['manifest']
    a=adjudication
    if (not a.get('reviewer') or not a.get('adjudication_receipt') or type(a.get('correct')) is not bool
            or type(a.get('critical_error')) is not bool or a.get('meaningfully_exercised') is not True):
        raise ValueError('explicit human adjudication and receipt required')
    packet=event_packet(store,a['event_id'],a['event_revision']); event=packet['event']
    bound=digest({'event':event,'documents':packet['documents']})
    if a.get('input_hash')!=bound: raise ValueError('adjudication evidence hash mismatch')
    if any(d['evidence_kind']!='prospective' for d in packet['documents']):
        raise ValueError('synthetic or replay evidence cannot become prospective pilot proof')
    observed=packet['first_observed_at']
    if not instant(manifest['frozen_at'])<=instant(observed)<=instant(a['reviewed_at'])<=instant(now):
        raise ValueError('pilot observation or adjudication outside frozen interval')
    subjects={s for c in event['claims'] for s in c['subject_ids']}
    covered=set()
    for symbol in manifest['captured_coverage']:
        resolution=registry.resolve('NASDAQ:'+symbol+' NYSE:'+symbol,known_by=manifest['frozen_at'])
        covered.update(m['entity_id'] for m in resolution['mentions'] if m['kind']=='issuer')
    outside=bool(subjects) and not (subjects & covered)
    reviews=store.conn.execute("SELECT review_id,payload FROM reviews WHERE event_id=? AND revision_id=? ORDER BY rowid DESC LIMIT 1",(a['event_id'],a['event_revision'])).fetchone()
    review_receipt=None
    if reviews:
        review=json.loads(reviews[1])
        if review['decision']=='approve' and review['input_hash']==bound: review_receipt=reviews[0]
    case={k:manifest[k] for k in ('implementation_hash','corpus_hash','source_scope_hash')}
    case.update(event_case_id=a['event_id'],event_revision=a['event_revision'],evidence_kind='prospective',
        adjudicator=a['reviewer'],adjudication_receipt=a['adjudication_receipt'],correct=a['correct'],
        critical_error=a['critical_error'],meaningfully_exercised=True,observed_at=observed,
        outside_cohort=outside,unknown_issuer=not subjects,family=event['family'],
        source_class=packet['documents'][0]['source_class'],proposed_publication=bool(a.get('proposed_publication')),
        review_receipt=review_receipt,published=False,input_hash=bound)
    prior=store.conn.execute("SELECT payload FROM pilot_records WHERE pilot_id=? AND kind='case'",(pilot_id,)).fetchall()
    if any(json.loads(r[0])['event_case_id']==case['event_case_id'] for r in prior):
        raise ValueError('event case already adjudicated; retain correction history instead of increasing n')
    record_id=_append(store,pilot_id,'case',now,case)
    return {'record_id':record_id,'case':case,'automatic_activation':False}


def pilot_report(store,pilot_id,registry,policies):
    data=_manifest(store,pilot_id,registry,policies,require_current=False); manifest=data['manifest']
    cases=[json.loads(r[0]) for r in store.conn.execute("SELECT payload FROM pilot_records WHERE pilot_id=? AND kind='case' ORDER BY rowid",(pilot_id,))]
    records={kind:[json.loads(r[0]) for r in store.conn.execute('SELECT payload FROM pilot_records WHERE pilot_id=? AND kind=? ORDER BY rowid',(pilot_id,kind))] for kind in ('run','gold','coverage')}
    # The expected set must come from the frozen corpus, not the adjudicated
    # subset. Unreviewed gold remains unknown and keeps completeness pending.
    manifest=dict(manifest,gold_event_ids=sorted(g['event_id'] for g in data['corpus']))
    for row in records['gold']:
        # Legacy gold receipts are also frozen-corpus audits; absence of a track
        # marker must never turn old replay evidence into prospective recall.
        row['evaluation_track']='frozen_corpus_audit'
    if records['coverage']: manifest=dict(manifest,source_coverage_receipt=records['coverage'][-1]['receipt_hash'])
    report=evaluate(manifest,cases,records['run'],records['gold'])
    if not data['current_versions_match']:
        report['manual_pilot']['status']='pending'
        report['manual_pilot']['reason_codes'].append('implementation_or_policy_changed')
        for row in report['family_source_gates']: row['status']='remain_manual'
    report.update(pilot_id=pilot_id,frozen_gold_records=len(data['corpus']),
                  pending=[key for key,kind in (('scheduled_run_receipts','run'),('gold_detection_adjudications','gold'),('source_coverage_review','coverage')) if not records[kind]],
                  human_identity_authentication='operator supplied; not independently authenticated')
    report['current_versions_match']=data['current_versions_match']
    if report['recall']['n_unadjudicated']:
        report['pending'].append('gold_adjudications_incomplete')
    return report


def record_receipt(store,pilot_id,registry,policies,kind,receipt,*,now):
    """Bind externally supplied operational/adjudication receipts to this pilot.

    Hashes preserve the supplied attestation; they do not authenticate humans or
    make an external scheduler receipt trustworthy without operator review.
    """
    if kind=='case': return record_case(store,pilot_id,registry,policies,receipt,now=now)
    data=_manifest(store,pilot_id,registry,policies); manifest=data['manifest']; r=dict(receipt)
    if not r.get('reviewed_by') or not r.get('evidence_receipt'):
        raise ValueError('operator review and underlying receipt required')
    if not instant(manifest['frozen_at'])<=instant(r['reviewed_at'])<=instant(now):
        raise ValueError('receipt review outside pilot interval')
    if kind in ('run','coverage'):
        ids=r.get('source_cycle_ids')
        if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)): raise ValueError('distinct observed source cycles required')
        cycles=[]
        for cycle_id in ids:
            row=store.conn.execute('SELECT observed_at,health FROM source_cycles WHERE cycle_id=?',(cycle_id,)).fetchone()
            if not row or instant(row[0])<instant(manifest['frozen_at']): raise ValueError('unknown or pre-pilot source cycle')
            cycles.append((row[0],json.loads(row[1])))
        if any(c[1]['status']!='complete' for c in cycles): raise ValueError('partial source cycles cannot certify pilot coverage')
        if any(instant(t)>instant(r['reviewed_at']) for t,_ in cycles):
            raise ValueError('source cycle observed after receipt review')
        if kind=='run':
            if r.get('execution_mode') not in ('manual','scheduled') or not r.get('run_id') or not r.get('session'):
                raise ValueError('run identity, mode and captured session required')
            if not instant(manifest['frozen_at'])<=instant(r['started_at'])<=instant(r['completed_at'])<=instant(now):
                raise ValueError('run timing outside pilot interval')
            if any(not instant(r['started_at'])<=instant(t)<=instant(r['completed_at']) for t,_ in cycles):
                raise ValueError('source cycle outside recorded run')
            if r['execution_mode']=='scheduled' and not r.get('scheduler_receipt'):
                raise ValueError('scheduled run requires an external scheduler receipt')
            expected_session=instant(r['started_at']).astimezone(ZoneInfo(manifest.get('session_timezone','America/New_York'))).date().isoformat()
            if r['session']!=expected_session:
                raise ValueError('session must equal the frozen timezone run-start date')
            if instant(r['completed_at'])>instant(r['reviewed_at']):
                raise ValueError('run completed after receipt review')
            previous_runs=[json.loads(row[0]) for row in store.conn.execute(
                "SELECT payload FROM pilot_records WHERE pilot_id=? AND kind='run'",(pilot_id,))]
            if any(set(ids)&set(row.get('source_cycle_ids',[])) for row in previous_runs):
                raise ValueError('source cycle already bound to a pilot run')
            r.update({key:manifest[key] for key in ('implementation_hash','corpus_hash','source_scope_hash')})
            r.update(eligible=r['execution_mode']=='scheduled',receipt=digest(receipt))
    elif kind=='gold':
        original=next((g for g in data['corpus'] if g['event_id']==r.get('event_id')),None)
        if not original or type(r.get('detected')) is not bool: raise ValueError('gold decision must reference the frozen corpus')
        if r['detected']:
            packet=event_packet(store,r['detected_event_id'],r['detected_event_revision'])
            if any(d['evidence_kind'] in ('synthetic','unverified_local_input') for d in packet['documents']):
                raise ValueError('synthetic or unverified detection cannot support gold')
            bound=digest({'event':packet['event'],'documents':packet['documents']})
            if r.get('detected_input_hash')!=bound:
                raise ValueError('gold detection evidence hash mismatch')
            matches=[d for d in packet['documents'] if d['url']==original['source_url']]
            if not matches or (original.get('source_native_id') and not any(
                    d['native_id']==original['source_native_id'] for d in matches)):
                raise ValueError('gold detection source identity mismatch')
            if instant(packet['first_observed_at'])>instant(r['reviewed_at']):
                raise ValueError('gold detection observed after review')
            if any(json.loads(row[0]).get('detected_event_id')==r['detected_event_id'] for row in store.conn.execute(
                    "SELECT payload FROM pilot_records WHERE pilot_id=? AND kind='gold'",(pilot_id,))):
                raise ValueError('detected event already matched to a gold event unit')
        # A prelisted corpus is an audit/calibration set. A future source-first
        # blinded sampler needs its own frozen protocol and independent inventory.
        r.update(evaluation_track='frozen_corpus_audit',evidence_kind=original['evidence_kind'])
        r.update(corpus_hash=manifest['corpus_hash'],source_scope_hash=manifest['source_scope_hash'],
            implementation_hash=manifest['implementation_hash'],
            prespecified=True,in_source_scope=True,unknown_issuer=original.get('unknown_issuer') is True,
            adjudication_receipt=digest(receipt))
    else: raise ValueError('unsupported pilot receipt kind')
    r['receipt_hash']=digest(receipt)
    identity_key='run_id' if kind=='run' else 'event_id' if kind=='gold' else None
    if identity_key and any(json.loads(row[0])[identity_key]==r[identity_key] for row in store.conn.execute(
            'SELECT payload FROM pilot_records WHERE pilot_id=? AND kind=?',(pilot_id,kind))):
        raise ValueError('duplicate pilot observation')
    return {'record_id':_append(store,pilot_id,kind,now,r),'automatic_activation':False}
