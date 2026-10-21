"""Explicit local commands for source planning, event review and release evidence."""
from __future__ import annotations

import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import sys

from .acquisition import SourceClient, Deferred
from .clinical import watch_studies
from .evidence import document
from .evaluation import evaluate,dependent_gates
from .feeds import watch_feed
from .identities import ROOT,digest,load_registry
from .review import build_brief,record_review,event_packet
from .runner import analyze,watch_edgar
from .reconciliation import reconcile_days
from .operations import operations_report,discovery_queue,safe_lead
from .pilot import pilot_plan,freeze_pilot,record_receipt,pilot_report
from .regulatory import watch_notifications
from .store import EventStore
from . import lifecycle_cli, operating_cli, prospective_cli, expansion_cli
from .verticals import cli as vertical_cli


def read_json(path):
    raw=Path(path).read_bytes()
    if len(raw)>5_000_000: raise ValueError("input exceeds 5 MB")
    return json.loads(raw)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=ROOT/"history/omni-events.db")
    parser.add_argument("--clinical-catalog",type=Path,help="explicit reviewed clinical scope catalog; absent by default")
    parser.add_argument("--sources",type=Path,default=ROOT/"registry/event-sources.json")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("plan"); sub.add_parser("status")
    sub.add_parser('operations'); sub.add_parser('pilot-plan')
    leads=sub.add_parser('leads'); leads.add_argument('--limit',type=int,default=100)
    history=sub.add_parser('history'); history.add_argument('event_id')
    freeze=sub.add_parser('pilot-freeze'); freeze.add_argument('corpus',type=Path)
    record=sub.add_parser('pilot-record'); record.add_argument('pilot_id'); record.add_argument('kind',choices=('case','run','gold','coverage')); record.add_argument('receipt',type=Path)
    report=sub.add_parser('pilot-report'); report.add_argument('pilot_id')
    ingest=sub.add_parser("ingest"); ingest.add_argument("input",type=Path); ingest.add_argument("--source",required=True)
    watch=sub.add_parser("watch"); watch.add_argument("source"); watch.add_argument("--execute",action="store_true"); watch.add_argument("--hydrate",action="store_true"); watch.add_argument("--max-pages",type=int,default=3); watch.add_argument("--user-agent")
    watch.add_argument("--reconcile",action="store_true",help="consume queued EDGAR backfill with a page overlap; never clears an unverified interval")
    watch.add_argument("--refresh-documents",action="store_true",help="explicitly recheck cached filing document manifests and bodies")
    watch.add_argument("--target-family",help="prespecified evaluation stratum; preserves every acquired native row")
    watch.add_argument("--measurement-proposal",help="exact frozen expansion proposal receipt")
    watch.add_argument("--measurement-arm",choices=("baseline","expanded"))
    clinical=sub.add_parser("clinical"); clinical.add_argument("--query",required=True); clinical.add_argument("--execute",action="store_true"); clinical.add_argument("--user-agent"); clinical.add_argument("--max-pages",type=int,default=3)
    index=sub.add_parser("reconcile-index"); index.add_argument("--start-day",required=True); index.add_argument("--end-day",required=True); index.add_argument("--execute",action="store_true"); index.add_argument("--user-agent")
    regulatory=sub.add_parser('regulatory'); regulatory.add_argument('--execute',action='store_true'); regulatory.add_argument('--user-agent')
    queue=sub.add_parser("queue"); queue.add_argument("--limit",type=int,default=25)
    packet=sub.add_parser("packet"); packet.add_argument("event_id"); packet.add_argument("revision_id")
    review=sub.add_parser("review"); review.add_argument("event_id"); review.add_argument("revision_id"); review.add_argument("--reviewer",required=True); review.add_argument("--decision",choices=("approve","reject"),required=True); review.add_argument("--note",default="")
    brief=sub.add_parser("brief"); brief.add_argument("--mode",choices=("off","shadow","reviewed"),default="shadow"); brief.add_argument("--legacy-brief",type=Path)
    evaluation=sub.add_parser("evaluate"); evaluation.add_argument("input",type=Path)
    lifecycle_cli.register_commands(sub)
    operating_cli.register_commands(sub)
    vertical_cli.register_commands(sub)
    prospective_cli.register_commands(sub)
    expansion_cli.register_commands(sub)
    args=parser.parse_args(argv)
    if args.command=="watch" and ((args.measurement_proposal is None)!=(args.measurement_arm is None)):
        parser.error("--measurement-proposal and --measurement-arm must be supplied together")
    config=read_json(args.sources); policies=config["policies"]
    now=datetime.now(timezone.utc).isoformat().replace("+00:00","Z")
    if args.command=="plan":
        result={"contract":"omni-plan-1","default_mode":config["default_mode"],"sources":policies,"feeds":config["feeds"],"issuer_feeds":config.get("issuer_feeds",[]),"network_requests":0,"scheduler_installed":False,"model_calls":0}
    elif args.command=="vertical-plan" or args.command=="vertical-parse" and not args.persist:
        result=vertical_cli.dispatch(args)
    elif args.command=="expansion-plan":
        result=expansion_cli.dispatch(args)
    elif args.command=='pilot-plan':
        from config import EDITORIAL_COVERAGE_TICKERS
        result=pilot_plan(load_registry(clinical_catalog_path=args.clinical_catalog) if args.clinical_catalog else load_registry(),policies,EDITORIAL_COVERAGE_TICKERS)
    elif args.command=="prospective-request":
        from config import EDITORIAL_COVERAGE_TICKERS
        registry=load_registry(clinical_catalog_path=args.clinical_catalog) if args.clinical_catalog else load_registry()
        result=prospective_cli.dispatch(args,None,registry,policies,EDITORIAL_COVERAGE_TICKERS,now=now)
    elif args.command in ("watch","clinical","reconcile-index","regulatory") and not args.execute:
        result={"status":"dry_run","source":getattr(args,"source",args.command),"max_pages":getattr(args,"max_pages",None),"network_requests":0,"execute_required":True}
        if args.command=="watch":
            from .runner import edgar_query_plan
            from .feeds import feed_query_plan
            routes=[r for r in config["feeds"]+config.get("issuer_feeds",[]) if r["policy_id"]==args.source]
            query=edgar_query_plan() if args.source=="sec-edgar" else feed_query_plan(routes[0]["url"]) if len(routes)==1 else None
            result.update(query_plan=query,query_hash=digest(query) if query else None,target_family=args.target_family,
                measurement_proposal=args.measurement_proposal,measurement_arm=args.measurement_arm,
                measurement_context_validated=False,native_inventory_rule="all_acquired_rows_before_classification")
    elif args.command=="store-backup":
        from .migrations import backup_existing
        result=backup_existing(args.db,args.destination)
    elif args.command=="store-restore":
        from .migrations import restore
        result=restore(args.backup,args.destination,expected_sha256=args.sha256)
    elif args.command=="evaluate":
        value=read_json(args.input)
        result=evaluate(value.get("manifest",{}),value.get("cases",[]),value.get("runs",[]),value.get("gold",[]))
        from config import EDITORIAL_COVERAGE_TICKERS
        result["dependent_gates"]=dependent_gates(value.get("operating_evidence",{}),EDITORIAL_COVERAGE_TICKERS,
                                                 candidates=value.get("expansion_candidates",[]))
    else:
        store=EventStore(args.db)
        try:
            registry=load_registry(clinical_catalog_path=args.clinical_catalog) if args.clinical_catalog else load_registry()
            if args.command in lifecycle_cli.COMMANDS: result=lifecycle_cli.dispatch(args,store,policies,now=now)
            elif args.command in operating_cli.COMMANDS:
                from config import EDITORIAL_COVERAGE_TICKERS
                result=operating_cli.dispatch(args,store,registry,policies,EDITORIAL_COVERAGE_TICKERS,now=now)
            elif args.command in vertical_cli.COMMANDS: result=vertical_cli.dispatch(args,store=store)
            elif args.command in prospective_cli.COMMANDS:
                from config import EDITORIAL_COVERAGE_TICKERS
                result=prospective_cli.dispatch(args,store,registry,policies,EDITORIAL_COVERAGE_TICKERS,now=now)
            elif args.command in expansion_cli.COMMANDS:
                from config import EDITORIAL_COVERAGE_TICKERS
                from instrument_registry import load_registry as load_instruments
                result=expansion_cli.dispatch(args,store=store,registry=registry,policies=policies,
                    instruments=load_instruments(),current_coverage=EDITORIAL_COVERAGE_TICKERS,now=now)
            elif args.command=="status": result={"inventory":store.inventory(),"pending_review":store.conn.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0]}
            elif args.command=='operations': result=operations_report(store,now=now)
            elif args.command=='leads': result=discovery_queue(store,args.limit)
            elif args.command=='history':
                from .briefs import search_history
                result=search_history(store,policies,query=args.event_id,now=now)
            elif args.command=='pilot-freeze':
                from config import EDITORIAL_COVERAGE_TICKERS
                result=freeze_pilot(store,registry,policies,EDITORIAL_COVERAGE_TICKERS,read_json(args.corpus),now=now)
            elif args.command=='pilot-record': result=record_receipt(store,args.pilot_id,registry,policies,args.kind,read_json(args.receipt),now=now)
            elif args.command=='pilot-report': result=pilot_report(store,args.pilot_id,registry,policies)
            elif args.command=='regulatory':
                from config import SEC_USER_AGENT
                client=SourceClient(store,policies['fda-oncology'],user_agent=args.user_agent or SEC_USER_AGENT,now=now)
                result=watch_notifications(store,client,now=now)
            elif args.command=="queue": result={"items":store.pending(args.limit)}
            elif args.command=="packet":
                from .briefs import safe_packet
                result=safe_packet(store,args.event_id,args.revision_id,policies,now=now)
            elif args.command=="review": result=record_review(store,args.event_id,args.revision_id,registry,policies,now=now,reviewer=args.reviewer,action=args.decision,note=args.note)
            elif args.command=="brief":
                legacy=read_json(args.legacy_brief).get("sections",{}).get("deep_dive") if args.legacy_brief else None
                from .briefs import preview_brief
                result=preview_brief(store,registry,policies,now=now,scope_hash=digest([registry.fingerprint(),digest(policies)]),mode=args.mode)
                if args.mode=="off" and legacy is not None:
                    result["focus"]=build_brief(store,registry,policies,now=now,mode="off",legacy_focus=legacy)["focus"]
            elif args.command=="ingest":
                raw=read_json(args.input); doc=document(policy=policies[args.source],**raw)
                events,decisions,leads=analyze(doc,registry,policies,now=now)
                result=store.commit_cycle(cycle_id="local:"+digest([doc,events,decisions]),source_id=args.source,observed_at=now,documents=[doc],events=events,decisions=decisions,leads=leads,registry=registry)
                result["leads"]=[safe_lead(lead,lead_id=lead["lead_id"],source_id=args.source,observed_at=now) for lead in leads]
            elif args.command=="watch":
                from config import SEC_USER_AGENT, EDITORIAL_COVERAGE_TICKERS
                client=SourceClient(store,policies[args.source],user_agent=args.user_agent or SEC_USER_AGENT,now=now)
                measurement={"target_family":args.target_family,"measurement_proposal":args.measurement_proposal,
                    "measurement_arm":args.measurement_arm,"current_coverage":list(EDITORIAL_COVERAGE_TICKERS)}
                if args.source=="sec-edgar": result=watch_edgar(store,client,registry,now=now,hydrate=args.hydrate,max_pages=args.max_pages,reconcile=args.reconcile,refresh_documents=args.refresh_documents,policies=policies,**measurement)
                else:
                    routes=[r for r in config["feeds"]+config.get("issuer_feeds",[]) if r["policy_id"]==args.source]
                    if len(routes)!=1: raise ValueError("one explicit configured source feed required")
                    from .route_contracts import ROUTES
                    route_id=routes[0].get("route_id") or next((k for k,v in ROUTES.items() if v.get("url")==routes[0]["url"]),None)
                    result=watch_feed(store,client,registry,feed_url=routes[0]["url"],now=now,hydrate=args.hydrate,route_id=route_id,policies=policies,**measurement)
            elif args.command=="clinical":
                from config import SEC_USER_AGENT
                client=SourceClient(store,policies["clinicaltrials"],user_agent=args.user_agent or SEC_USER_AGENT,now=now)
                result=watch_studies(store,client,query=args.query,now=now,max_pages=args.max_pages)
            elif args.command=="reconcile-index":
                from config import SEC_USER_AGENT
                client=SourceClient(store,policies['sec-edgar'],user_agent=args.user_agent or SEC_USER_AGENT,now=now)
                result=reconcile_days(store,client,start_day=args.start_day,end_day=args.end_day,now=now)
            else: raise ValueError("unsupported command")
        finally: store.close()
    print(json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False))
    return 0


if __name__=="__main__":
    try: raise SystemExit(main())
    except (ValueError,KeyError,PermissionError,OSError,Deferred) as exc:
        print(json.dumps({"status":"failed","error":type(exc).__name__,"detail":str(exc)[:300]}),file=sys.stderr)
        raise SystemExit(1)
