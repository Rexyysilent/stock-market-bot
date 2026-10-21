"""Local rights, brief, operating actuals and readiness commands."""
from __future__ import annotations

import json
from pathlib import Path

from .identities import digest

COMMANDS={"rights-import","rights-revoke","brief-preview","brief-export","history-search",
          "operating-import","economics-report","readiness-report","readiness-context"}


def _read(path):
    raw=Path(path).read_bytes()
    if len(raw)>5_000_000: raise ValueError("input exceeds 5 MB")
    return json.loads(raw)


def register_commands(sub):
    for name in ("rights-import","operating-import"):
        p=sub.add_parser(name); p.add_argument("input",type=Path); p.add_argument("--synthetic",action="store_true")
    p=sub.add_parser("rights-revoke"); p.add_argument("rights_ref"); p.add_argument("--reason",required=True)
    p=sub.add_parser("brief-preview"); p.add_argument("--scope-hash",required=True)
    p.add_argument("--purpose",choices=("review_display","local_export"),default="review_display")
    p.add_argument("--audience",default="local_operator"); p.add_argument("--synthetic",action="store_true")
    p.add_argument("--persist",action="store_true")
    p=sub.add_parser("brief-export"); p.add_argument("brief_ref"); p.add_argument("destination",type=Path)
    p.add_argument("--audience",default="local_operator"); p.add_argument("--synthetic",action="store_true")
    p=sub.add_parser("history-search"); p.add_argument("--query",default=""); p.add_argument("--limit",type=int,default=100)
    p.add_argument("--audience",default="local_operator"); p.add_argument("--synthetic",action="store_true")
    p=sub.add_parser("economics-report"); p.add_argument("--scope-hash",required=True)
    p.add_argument("--window-start",required=True); p.add_argument("--window-end",required=True)
    p.add_argument("--synthetic",action="store_true"); p.add_argument("--persist",action="store_true")
    p=sub.add_parser("readiness-report"); p.add_argument("input",type=Path); p.add_argument("--persist",action="store_true")
    p=sub.add_parser("readiness-context"); p.add_argument("protocol_id"); p.add_argument("candidates",type=Path)
    p.add_argument("--operating-window-start"); p.add_argument("--operating-window-end")


def dispatch(args,store,registry,policies,current_coverage,*,now):
    if args.command=="readiness-context":
        from .gates import context_request
        window=None
        if args.operating_window_start or args.operating_window_end:
            if not (args.operating_window_start and args.operating_window_end): raise ValueError("both operating window boundaries required")
            window={"start":args.operating_window_start,"end":args.operating_window_end}
        return context_request(store,args.protocol_id,registry,policies,current_coverage,_read(args.candidates),now=now,operating_window=window)
    if args.command=="rights-import":
        from .rights import record_rights
        return record_rights(store,_read(args.input),policies,now=now,allow_synthetic=args.synthetic)
    if args.command=="rights-revoke":
        from .rights import revoke_rights
        return revoke_rights(store,args.rights_ref,now=now,reason=args.reason)
    if args.command=="brief-preview":
        from .briefs import preview_brief
        return preview_brief(store,registry,policies,now=now,scope_hash=args.scope_hash,purpose=args.purpose,
            audience=args.audience,allow_synthetic=args.synthetic,persist=args.persist)
    if args.command=="brief-export":
        from .briefs import export_brief
        return export_brief(store,args.brief_ref,args.destination,registry,policies,now=now,audience=args.audience,allow_synthetic=args.synthetic)
    if args.command=="history-search":
        from .briefs import search_history
        return search_history(store,policies,query=args.query,now=now,audience=args.audience,allow_synthetic=args.synthetic,limit=args.limit)
    if args.command=="operating-import":
        from .operating import record_observation
        return record_observation(store,_read(args.input),now=now,allow_synthetic=args.synthetic,registry=registry,policies=policies)
    if args.command=="economics-report":
        from .operating import economics_report,record_economics_report
        function=record_economics_report if args.persist else economics_report
        return function(store,scope_hash=args.scope_hash,window_start=args.window_start,window_end=args.window_end,
            policy_hash=digest(policies),now=now,allow_synthetic=args.synthetic,registry=registry,policies=policies)
    if args.command=="readiness-report":
        from .gates import dependent_gates,record_gate_report
        data=_read(args.input); function=record_gate_report if args.persist else dependent_gates
        kwargs={"candidates":data.get("candidates",[]),"registry":registry,"policies":policies,"now":now}
        if args.persist: return function(store,data.get("evidence",{}),current_coverage,**kwargs)
        return function(data.get("evidence",{}),current_coverage,store=store,**kwargs)
    raise ValueError("unknown operating command")
