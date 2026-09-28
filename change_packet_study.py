"""Materials for the T8 change-packet falsification study. Synthetic data only.

    python change_packet_study.py build case_a OUT_DIR [--manual]
    python change_packet_study.py score RECORDING_SHEET.csv

build   writes one participant folder: the case's source files (briefs derived
        from the synthetic demo brief, plus permitted-extract files) and, unless
        --manual, an evidence history for the packet. It never copies the
        answer key and refuses a non-empty folder.
score   applies the study's decision gates to a filled recording sheet. With
        fewer than six consenting participants the result is "pending", which
        means an unvalidated prototype, not a success.

The protocol is docs/study/CHANGE_PACKET_STUDY.md. Agent self-tests are not
participants.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import statistics
import sys
from pathlib import Path

import event_history as eh

ROOT = Path(__file__).resolve().parent
CASES_DIR = ROOT / "fixtures" / "study"
DEMO_BRIEF = ROOT / "fixtures" / "exports" / "daily_brief.demo.json"
PARTICIPANTS_REQUIRED = 6
COMPLETIONS_REQUIRED = 4
TIME_LIMIT_MINUTES = 15
REDUCTION_REQUIRED = 0.20


def _brief(case, subject, spec):
    brief = copy.deepcopy(json.loads(DEMO_BRIEF.read_text(encoding="utf-8")))
    brief["generated_at"] = spec["generated_at"]
    brief["universe"] = {"name": f"study-case-{case.lower()}", "version": "1",
                         "tickers": [subject], "focus_ticker": subject}
    sections = brief["sections"]
    sections["prices"] = spec["prices"]
    for key in ("technicals", "insider_clusters", "confluence"):
        if key in sections:
            sections[key] = []
    if "options_flow" in sections:
        sections["options_flow"] = {}
    health = brief["health"]
    health["sources"] = dict(health.get("sources") or {}, **spec["sources"])
    health["warnings"] = [f"SYNTHETIC STUDY CASE {case}. All issuers, prices, filings and "
                          "reports are fabricated test data."]
    degraded = any(isinstance(v, dict) and (v.get("fetch_error") or v.get("exceptions"))
                   for v in spec["sources"].values())
    if degraded:
        health["status"] = "WARN"
        health["warnings"].append("A synthetic source was degraded in this run.")
    return brief


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_case(case_dir, out_dir, *, with_packet=True):
    """Write one participant folder for a case; returns its paths and subject."""
    case_dir, out_dir = Path(case_dir), Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"{out_dir} is not empty; use a new folder per participant")
    spec = json.loads((case_dir / "case.json").read_text(encoding="utf-8"))
    sources = out_dir / "sources"
    written = []
    briefs = {}
    for role in ("previous", "current"):
        brief_spec = spec["briefs"][role]
        briefs[role] = _write(sources / brief_spec["file"],
                              _brief(spec["case"], spec["subject"], brief_spec))
        written.append((brief_spec["generated_at"], briefs[role]))
    for item in spec["extracts"]:
        doc = {"artifact": "evidence-extract-1", "generated_at": item["generated_at"],
               "capture_policy": {"recorded": True, "basis": "synthetic study fixture"},
               "extracts": item["extracts"]}
        written.append((item["generated_at"], _write(sources / item["file"], doc)))

    history = None
    if with_packet:
        history = out_dir / "history.db"
        conn = eh.connect(history)
        try:
            resolver = spec["resolver"]
            eh.register_resolver(conn, resolver["rule_version"],
                                 introduced_at=resolver["introduced_at"],
                                 description=resolver["description"])
            for _, path in sorted(written):
                eh.import_archive(conn, path)

            def version(source_key):
                return conn.execute("SELECT version_id FROM evidence_versions WHERE source_key=?",
                                    (source_key,)).fetchone()[0]
            for decision in spec["decisions"]:
                eh.record_evidence_relation(
                    conn, version(decision["from"]), version(decision["to"]), decision["type"],
                    decision=decision["decision"], decision_at=decision["decision_at"],
                    rule_version=resolver["rule_version"],
                    from_claim=decision.get("from_claim"), to_claim=decision.get("to_claim"))
        finally:
            conn.close()

    previous = briefs["previous"].relative_to(out_dir).as_posix()
    current = briefs["current"].relative_to(out_dir).as_posix()
    lines = [f"Synthetic study case {spec['case']} ({spec['subject']}). All data is fabricated.",
             "", f"Previous brief: {previous}", f"Current brief:  {current}",
             "Other files in sources/ are the permitted extracts captured between them.", "",
             "Task: say what changed for the issuer, what is actually supported, how many "
             "underlying origins support it, and what cannot be concluded.", ""]
    if with_packet:
        lines += ["Change packet (run from the repository folder):",
                  f"  python marketbot.py packet <this folder>/{previous} <this folder>/{current} "
                  f"--subject {spec['subject']} --history <this folder>/history.db --note",
                  "Save a note with --save <file>.md.", ""]
    else:
        lines += ["Manual condition: read the files in sources/ directly or in the viewer.", ""]
    (out_dir / "README.txt").write_text("\n".join(lines), encoding="utf-8")
    return {"subject": spec["subject"], "previous": str(briefs["previous"]),
            "current": str(briefs["current"]), "history": str(history) if history else None,
            "sources": str(sources)}


def _yes(value):
    return str(value or "").strip().lower() == "yes"


def _minutes(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def score_sheet(path):
    """Decision gates from a recording sheet: pass, fail, or pending."""
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(handle) if (r.get("participant_id") or "").strip()]
    consented = [r for r in rows if _yes(r.get("consent_recorded"))]
    packet = [r for r in consented if r.get("condition") == "packet"]
    manual = [r for r in consented if r.get("condition") == "manual"]
    participants = {r["participant_id"] for r in packet}

    completions = [r for r in packet if _yes(r.get("completed_unaided"))
                   and _yes(r.get("all_four_correct"))
                   and (_minutes(r.get("minutes")) or TIME_LIMIT_MINUTES + 1) <= TIME_LIMIT_MINUTES]
    verified = {name: [m for r in group if _yes(r.get("all_four_correct"))
                       for m in [_minutes(r.get("minutes"))] if m is not None]
                for name, group in (("packet", packet), ("manual", manual))}
    critical = [r for r in packet if _yes(r.get("critical_attributable_to_interface"))]

    gates = {"unaided_completion": {
        "count": len(completions), "required": COMPLETIONS_REQUIRED,
        "status": "pass" if len(completions) >= COMPLETIONS_REQUIRED else "fail"}}
    if verified["packet"] and verified["manual"]:
        packet_median = statistics.median(verified["packet"])
        manual_median = statistics.median(verified["manual"])
        reduction = 1 - packet_median / manual_median if manual_median else None
        gates["median_time_reduction"] = {
            "packet_median_minutes": packet_median, "manual_median_minutes": manual_median,
            "reduction": None if reduction is None else round(reduction, 4),
            "required": REDUCTION_REQUIRED,
            "status": "pass" if reduction is not None and reduction >= REDUCTION_REQUIRED else "fail"}
    else:
        gates["median_time_reduction"] = {"status": "pending",
                                          "reason": "no verified tasks in one condition"}
    gates["no_critical_interface_errors"] = {
        "count": len(critical), "status": "fail" if critical else "pass"}

    result = {"participants": len(participants), "excluded_without_consent": len(rows) - len(consented),
              "gates": gates,
              "descriptive": {
                  "help_requests": sum(int(r.get("help_requests") or 0) for r in packet),
                  "missed_critical_caveats": sum(int(r.get("missed_critical_caveats") or 0)
                                                 for r in packet),
                  "citations_retraced": sum(_yes(r.get("citation_retraced")) for r in packet)}}
    statuses = {g["status"] for g in gates.values()}
    if len(participants) < PARTICIPANTS_REQUIRED:
        result["overall"] = "pending"
        result["reason"] = (f"needs {PARTICIPANTS_REQUIRED} participants with a recorded packet "
                            f"task and consent; have {len(participants)}. An unrun study is an "
                            "unvalidated prototype, not a success.")
    elif "fail" in statuses:
        result["overall"] = "fail"
    elif "pending" in statuses:
        result["overall"] = "pending"
    else:
        result["overall"] = "pass"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("case", choices=sorted(p.name for p in CASES_DIR.iterdir() if p.is_dir()))
    build.add_argument("out", type=Path)
    build.add_argument("--manual", action="store_true", help="sources only, no evidence history")
    score = sub.add_parser("score")
    score.add_argument("sheet", type=Path)
    args = parser.parse_args(argv)
    if args.command == "build":
        result = build_case(CASES_DIR / args.case, args.out, with_packet=not args.manual)
    else:
        result = score_sheet(args.sheet)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
