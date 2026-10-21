"""Scoped FDA oncology notification metadata; never all-drug approval coverage."""
from datetime import datetime
import hashlib
from urllib.parse import urljoin,urlsplit
from bs4 import BeautifulSoup
from .acquisition import MAX_BYTES,Deferred
from .identities import digest,instant,time_interval
from .evidence import source_decision

INDEX='https://www.fda.gov/drugs/resources-information-approved-drugs/oncology-cancerhematologic-malignancies-approval-notifications'
SCOPE='FDA oncology/hematology notifications only; FDA does not announce every approval or label change here'


def parse_notifications(raw,*,policy,cap=200):
    if len(raw)>MAX_BYTES or type(cap) is not int or not 1<=cap<=1000: raise ValueError('invalid regulatory index budget')
    soup=BeautifulSoup(raw,'html.parser'); table=None
    for candidate in soup.find_all('table'):
        headings=[h.get_text(' ',strip=True).lower() for h in candidate.find_all('th')]
        if 'webpage' in headings and 'date' in headings: table=candidate; break
    if table is None: raise ValueError('regulatory_index_structure_missing')
    rows=[]; gaps=[]; total=0; seen=set()
    for tr in table.find_all('tr'):
        cells=tr.find_all('td')
        if not cells: continue
        total+=1
        if total>cap: gaps.append('regulatory_index_cap'); continue
        if len(cells)<3: gaps.append('malformed_regulatory_row'); continue
        link=cells[0].find('a',href=True)
        if not link: gaps.append('regulatory_link_missing'); continue
        url=urljoin(INDEX,link['href'])
        if source_decision(policy,url,'acquire'): gaps.append('regulatory_route_not_approved'); continue
        title=link.get_text(' ',strip=True); raw_date=cells[-1].get_text(' ',strip=True)
        stamp=None
        for fmt in ('%m/%d/%Y','%Y-%m-%d','%B %d, %Y'):
            try: stamp=datetime.strptime(raw_date,fmt).date().isoformat(); break
            except ValueError: pass
        if not stamp: gaps.append('regulatory_source_date_unknown')
        native='fda-notice:'+digest(url)
        if native in seen: continue
        seen.add(native)
        rows.append({'native_id':native,'url':url,'title':title,'published':None,
            'publication_date':time_interval(stamp),'source_date_raw':raw_date,
            'hydration_status':'metadata_only','regulatory_claim_status':'body_not_validated','signal_eligible':False})
    if not rows: gaps.append('regulatory_index_empty')
    return {'rows':rows,'gaps':sorted(set(gaps)),'total_rows':total,'sha256':hashlib.sha256(raw).hexdigest()}


def watch_notifications(store,client,*,now):
    instant(now); source=client.policy['policy_id']
    try: parsed=parse_notifications(client.get(INDEX,content_types=('text/html',)),policy=client.policy)
    except (Deferred,ValueError,PermissionError) as exc: parsed={'rows':[],'gaps':[str(exc)],'total_rows':None,'sha256':None}
    prior_count=store.conn.execute('SELECT count(*) FROM acquisition_queue WHERE source_id=?',(source,)).fetchone()[0]
    new=sum(store.candidate(source,r['native_id']) is None for r in parsed['rows'])
    receipt={'contract':'omni-fda-notifications-1','observed_at':now,'url':INDEX,'scope':SCOPE,
        'initial_seed':prior_count==0,'new_metadata_candidates':new,'claims_validated':0,
        'gaps':parsed['gaps'],'raw_sha256':parsed['sha256'],'rows':len(parsed['rows']),'requests':dict(client.counts)}
    receipt['receipt_id']='fda-index:'+digest(receipt)
    from .coverage import observation_from_rows
    observations=[] if parsed['sha256'] is None else [observation_from_rows(parsed['rows'],source_id=source,
        route_id='fda-oncology-index',family='oncology_notification',acquired_at=now,window_start=now,window_end=now,
        policy_hash=digest(client.policy),raw_response_hash=parsed['sha256'],query={'url':INDEX},
        gaps=parsed['gaps'],capped='regulatory_index_cap' in parsed['gaps'],end_of_results=not parsed['gaps'])]
    store.commit_cycle(cycle_id=receipt['receipt_id'],source_id=source,observed_at=now,
        documents=[],events=[],decisions=[],candidates=parsed['rows'],source_receipts=[receipt],coverage_observations=observations,
        complete=False,preserve_cursor=True,gaps=parsed['gaps'] or ['scoped_index_not_continuous_coverage'],metrics=receipt)
    return receipt
