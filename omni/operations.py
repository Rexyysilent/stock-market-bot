"""Read-only operational evidence and correction history, without source bodies."""
import json
import re
from .identities import instant


def operations_report(store,*,now):
    instant(now)
    sources=[]
    ids={r[0] for r in store.conn.execute('SELECT DISTINCT source_id FROM source_cycles')}
    for source in sorted(ids):
        row=store.conn.execute('SELECT cycle_id,observed_at,health FROM source_cycles WHERE source_id=? ORDER BY rowid DESC LIMIT 1',(source,)).fetchone()
        health=json.loads(row[2])
        sources.append({'source_id':source,'latest_cycle':row[0],'last_observed_at':row[1],
            'latest_status':health['status'],'gaps':health['gaps'],'metrics':health['metrics'],
            'cursor':store.cursor(source),'candidate_states':store.backlog_counts(source)})
    jobs={}
    for row in store.conn.execute('SELECT payload FROM document_jobs'):
        state=json.loads(row[0])['state']; jobs[state]=jobs.get(state,0)+1
    receipt_count=store.conn.execute("SELECT count(*) FROM omni_receipts WHERE kind='operating_observation'").fetchone()[0]
    return {'contract':'omni-operations-1','generated_at':now,'inventory':store.inventory(),'sources':sources,
        'request_budget_usage':[dict(r) for r in store.conn.execute('SELECT * FROM budgets ORDER BY day,kind')],
        'document_job_states':jobs,'pending_review_total':store.conn.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0],
        'observed_trial_changes':store.conn.execute('SELECT count(*) FROM trial_changes').fetchone()[0],
        'costs':{'provider_currency_cost':None,'human_review_cost':None,'operating_receipt_count':receipt_count,
                 'reason':'Select an exact scope, reporting window and current policies with economics-report; receipt counts do not establish measured costs'},
        'pilot_eligibility':'not_established_by_operational_counts','automatic_activation':False}


def event_history(store,event_id):
    from .receipts import get_receipt
    head=store.conn.execute('SELECT revision_id,changed_at FROM event_heads WHERE event_id=?',(event_id,)).fetchone()
    if not head: raise ValueError('unknown event')
    versions=[]
    for row in store.conn.execute('SELECT revision_id,first_observed_at,payload,decision FROM events WHERE event_id=? ORDER BY rowid',(event_id,)):
        event,decision=json.loads(row[2]),json.loads(row[3])
        reviews=[{'review_id':r[0],**json.loads(r[1])} for r in store.conn.execute('SELECT review_id,payload FROM reviews WHERE event_id=? AND revision_id=? ORDER BY rowid',(event_id,row[0]))]
        versions.append({'revision_id':row[0],'current':row[0]==head[0],'event_first_observed_at':row[1],
                         'family':event['family'],'reason_codes_at_ingest':decision['reason_codes'],
                         'evidence_ids':event['evidence_ids'],'reviews':reviews,
                         'packet_revisions':[dict(p) for p in store.conn.execute(
                             'SELECT packet_hash,observed_at FROM event_packets WHERE event_id=? AND revision_id=? ORDER BY rowid',(event_id,row[0]))]})
    return {'contract':'omni-correction-history-1','event_id':event_id,'current_revision':head[0],
            'last_revision_change_at':head[1],'versions':versions,'source_bodies_included':False,
            'source_changes':[get_receipt(store,r[0]) for r in store.conn.execute(
                'SELECT receipt_id FROM event_change_links WHERE event_id=? ORDER BY receipt_id',(event_id,))]}


def safe_lead(lead,*,lead_id,source_id,observed_at):
    # Parser candidates can contain nested source spans. Both ingest diagnostics
    # and queue views expose only identifiers and reason codes. Exact source/claim
    # fields require the rights-filtered packet or reviewed-brief path.
    safe={key:lead[key] for key in ('event_id','event_revision','evidence_id','evidence_revision','evidence_kind')
          if isinstance(lead.get(key),str) and len(lead[key])<=250}
    reason=lead.get('reason')
    if isinstance(reason,str) and re.fullmatch(r'[a-z][a-z0-9_:-]{0,159}',reason):
        safe['reason']=reason
    return {**safe,'lead_id':lead_id,'source_id':source_id,'first_observed_at':observed_at,
            'signal_eligible':False,'human_review_status':'pending','source_fields_withheld':True}


def discovery_queue(store,limit=100):
    if type(limit) is not int or not 1<=limit<=1000: raise ValueError('invalid lead limit')
    rows=[]
    for row in store.conn.execute('SELECT lead_id,source_id,observed_at,payload FROM discovery_leads ORDER BY rowid LIMIT ?',(limit,)):
        rows.append(safe_lead(json.loads(row[3]),lead_id=row[0],source_id=row[1],observed_at=row[2]))
    total=store.conn.execute('SELECT count(*) FROM discovery_leads').fetchone()[0]
    return {'items':rows,'total':total,'display_deferred':max(0,total-len(rows)),'signal_eligible':False}
