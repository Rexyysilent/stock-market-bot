"""Small, local-first entry point. Planning and viewing require only Python.

Examples:
  python marketbot.py plan --universe profiles/us-core.example.json
  python marketbot.py run --universe profiles/us-core.example.json --workspace workspaces/core
  python marketbot.py ledger update --universe profiles/us-core.example.json --workspace workspaces/core
  python marketbot.py inspect workspaces/core/daily_brief.json
  python marketbot.py diff previous.json current.json
  python marketbot.py demo
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from stateutil import atomic_write_json, exclusive_file_lock
from universe_profile import apply_profile, fingerprint, load_profile, profile_plan

ROOT = Path(__file__).resolve().parent


def prepare_workspace(path, profile):
    """Pin one universe per workspace; never adopt an unmarked state directory."""
    root = Path(path).resolve()
    if root == ROOT or root in ROOT.parents:
        raise ValueError("Use a dedicated workspace, not the repository or its parents")
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(root / ".profile.lock"):
        manifest = root / "universe.json"
        if manifest.exists():
            if fingerprint(load_profile(manifest)) != fingerprint(profile):
                raise ValueError("Workspace universe differs. Use a new workspace; baselines must not be mixed")
        else:
            existing = [entry for entry in root.iterdir() if entry.name != ".profile.lock"]
            if existing:
                raise ValueError("Refusing an unmarked, nonempty workspace. Create a new empty directory")
            atomic_write_json(manifest, profile, indent=2)
    return root


def _require_contact(value):
    """Reject missing/template contact strings before any acquisition begins."""
    value = value or ""
    if (not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", value)
            or re.search(r"@(example\.(com|org|net)|email\.com|yourdomain\.)", value, re.I)):
        raise ValueError("Set SEC_USER_AGENT to your application name and real contact email in .env")


def run_profile(profile, workspace, command="run", ledger_command=None):
    """Invoke existing implementations after configuration, in an isolated cwd.

    This launcher owns the process configuration. Call it once per process.
    Direct legacy entry points are unchanged and do not acquire its outer lock.
    """
    import config
    if command == "run":
        _require_contact(config.SEC_USER_AGENT)
    root = prepare_workspace(workspace, profile)
    apply_profile(config, profile)
    config.BRIEF_ARCHIVE_DIR = str(root / "archive" / "briefs")
    # Do not accidentally mirror a newly selected universe into another workspace.
    config.BRIEF_ARCHIVE_MIRROR_DIR = None
    original_cwd = Path.cwd()
    previous_cache = os.environ.get("YFINANCE_CACHE_DIR")
    os.environ["YFINANCE_CACHE_DIR"] = str(root / "state" / "yfinance_cache")
    try:
        os.chdir(root)
        with exclusive_file_lock(root / ".operation.lock"):
            if command == "run":
                from export_for_gemini import generate_daily_brief
                generate_daily_brief()
            else:
                from ledger.__main__ import main as ledger_main
                return ledger_main([ledger_command])
    finally:
        os.chdir(original_cwd)
        if previous_cache is None:
            os.environ.pop("YFINANCE_CACHE_DIR", None)
        else:
            os.environ["YFINANCE_CACHE_DIR"] = previous_cache
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run", "ledger"):
        item = sub.add_parser(name)
        if name == "ledger":
            item.add_argument("ledger_command", choices=("ingest", "update", "rebuild"))
        item.add_argument("--universe", type=Path, required=True)
        if name != "plan":
            item.add_argument("--workspace", type=Path, required=True)
    sub.add_parser("demo", help="Open the local viewer with synthetic data, without fetching providers")
    inspect = sub.add_parser("inspect", help="Read-only quality summary; not a schema or market-validity certificate")
    inspect.add_argument("brief", type=Path)
    diff = sub.add_parser("diff", help="Describe changes between two local briefs")
    diff.add_argument("previous", type=Path)
    diff.add_argument("current", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in ("inspect", "diff"):
            from brief_tools import inspect_brief, diff_briefs, load_brief
            output = (inspect_brief(load_brief(args.brief)) if args.command == "inspect"
                      else diff_briefs(load_brief(args.previous), load_brief(args.current)))
            print(json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False))
        elif args.command == "demo":
            from serve_dump import main as serve
            return serve(["--demo"])
        else:
            profile = load_profile(args.universe)
            if args.command == "plan":
                print(json.dumps(profile_plan(profile), indent=2, allow_nan=False))
            else:
                return run_profile(profile, args.workspace, args.command, getattr(args, "ledger_command", None))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
