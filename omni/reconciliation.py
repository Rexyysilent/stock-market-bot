"""Bounded EDGAR daily-index reconciliation with durable source receipts.

Coverage describes records present in an observed index, not real-time delivery
or absence of subsequent SEC corrections/removals. Feed timestamps stay intact.
"""
from datetime import date,datetime,time,timedelta
import hashlib
import re
from zoneinfo import ZoneInfo

from .acquisition import MAX_BYTES,Deferred
from .identities import digest,instant

VERSION='omni-edgar-index-1'
NY=ZoneInfo('America/New_York')


def daily_index_url(day):
    value=date.fromisoformat(day)
    return f'https://www.sec.gov/Archives/edgar/daily-index/{value.year}/QTR{(value.month-1)//3+1}/master.{value:%Y%m%d}.idx'


def parse_daily_index(raw, *, day, max_rows=50000):
    expected=date.fromisoformat(day)
    if len(raw)>MAX_BYTES: raise ValueError('index_oversized')
    try: text=raw.decode('utf-8-sig'); encoding='utf-8'
    except UnicodeDecodeError: text=raw.decode('latin-1'); encoding='latin-1'
    lines=text.splitlines()
    header=next((line for line in lines if line in ('CIK|Company Name|Form Type|Date Filed|Filename',
                                                   'CIK|Company Name|Form Type|Date Filed|File Name')),None)
    if header is None: raise ValueError('index_header_missing')
    start=lines.index(header)+1
    if start>=len(lines) or not re.fullmatch(r'-+',lines[start].strip()):
        raise ValueError('index_separator_missing')
    rows=[]; seen={}; total=0
    for line in lines[start+1:]:
        if not line.strip(): continue
        total+=1
        if total>max_rows: raise ValueError('index_row_cap_exceeded')
        parts=line.split('|')
        if len(parts)!=5: raise ValueError('malformed_index_record')
        cik,company,form,filed,path=parts
        match=re.fullmatch(r'edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt',path)
        if not cik.isdigit() or not company or not form or not match or int(cik)!=int(match[1]):
            raise ValueError('invalid_index_identity_or_path')
        filed_day=date.fromisoformat(filed)
        if filed_day>expected: raise ValueError('index_contains_future_filing_date')
        native='urn:tag:sec.gov,2008:accession-number='+match[2]
        registrant={'cik':cik,'name':company,'form':form,'filed_date':filed_day.isoformat(),
                    'submission_url':'https://www.sec.gov/Archives/'+path,'role':'index_registrant_not_claim_subject'}
        if native in seen:
            if registrant not in seen[native]['registrants']: seen[native]['registrants'].append(registrant)
            continue
        row={'native_id':native,'title':form+' - '+company,'form':form,'cik':cik,
            'url':'https://www.sec.gov/Archives/'+path[:-4]+'-index.html',
            'submission_url':'https://www.sec.gov/Archives/'+path,'published':None,
            'filed_date':filed_day.isoformat(),'index_day':day,'time_precision':'date',
            'hydration_status':'metadata_only' if form in ('8-K','8-K/A','6-K','6-K/A') else 'not_applicable',
            'registrants':[registrant],'signal_eligible':False}
        rows.append(row); seen[native]=row
    return {'rows':rows,'index_rows':total,'unique_accessions':len(rows),'encoding':encoding,
            'raw_sha256':hashlib.sha256(raw).hexdigest()}


def reconcile_days(store,client,*,start_day,end_day,now):
    start,end=date.fromisoformat(start_day),date.fromisoformat(end_day)
    if not 0<=(end-start).days<7: raise ValueError('reconciliation range must be one to seven calendar days')
    clock=instant(now); source_id=client.policy['policy_id']
    receipts=[]; candidates=[]; gaps=[]
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset); key=day.isoformat()
        if datetime.combine(day+timedelta(days=1),time(6),NY)>clock:
            gaps.append({'day':key,'reason':'index_day_not_closed'}); continue
        if day.weekday()>=5:
            receipts.append({'day':key,'status':'nonfiling_weekend','basis':'SEC business-day dissemination schedule'}); continue
        try:
            parsed=parse_daily_index(client.get(daily_index_url(key),content_types=('text/plain','application/octet-stream')),day=key)
        except (Deferred,ValueError,PermissionError) as exc:
            gaps.append({'day':key,'reason':str(exc)}); continue
        for row in parsed['rows']:
            prior=store.candidate(source_id,row['native_id'])
            if prior:
                # An index supplies filing-date precision; retain a previously
                # observed feed timestamp and already completed hydration jobs.
                row={**row,**prior,'index_day':key,'filed_date':row['filed_date']}
            candidates.append(row)
        receipts.append({'day':key,'status':'index_records_acquired','url':daily_index_url(key),
                         **{k:v for k,v in parsed.items() if k!='rows'}})
    batch={'contract':VERSION,'start_day':start_day,'end_day':end_day,'observed_at':now,
           'days':receipts,'gaps':gaps,'status':'partial' if gaps else 'index_range_acquired',
           'coverage_scope':'records listed in the retrieved daily indexes; later corrections/removals are not certified',
           'feed_watermark_advanced':False,'requests':dict(client.counts)}
    batch['receipt_id']='index:'+digest(batch)
    # Independent reconciliation cursor: never overwrite feed progress with a
    # filing date, midnight guess or an index retrieval timestamp.
    receipt=store.commit_cycle(cycle_id=batch['receipt_id'],source_id=source_id,observed_at=now,
        documents=[],events=[],decisions=[],candidates=candidates,source_receipts=[batch],
        watermark=store.cursor(source_id)['watermark'],complete=False,preserve_cursor=True,
        continuation=store.cursor(source_id)['continuation'],gaps=['index_reconciliation_separate_from_feed'],
        metrics={'index_receipt':batch['receipt_id'],'candidate_count':len(candidates)})
    return {**batch,'persisted_candidates':len(candidates),'new_events':receipt['new_events'],'external_delivery':False}
