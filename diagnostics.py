"""Local read-only diagnostics. Never acquire providers or print secret values."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

from brief_tools import inspect_brief, load_brief
from universe_profile import fingerprint, load_profile


def diagnose(root, workspace=None, profile=None):
    root = Path(root).resolve()
    target = Path(workspace).resolve() if workspace else root
    checks = []

    def add(name, status, action):
        checks.append({"check": name, "status": status, "next_action": action})

    supported = sys.version_info[:2] in ((3, 11), (3, 12))
    add("python", "ok" if supported else "warning", "Use Python 3.11 or 3.12 for the supported collection environment.")
    required = ("dotenv", "requests", "pandas", "numpy", "yfinance", "pandas_market_calendars")
    missing = [name for name in required if importlib.util.find_spec(name) is None]
    add("provider_dependencies", "missing" if missing else "ok", "Install requirements.lock with --require-hashes." if missing else "Collection dependencies are installed; availability was not tested.")
    validation_missing = importlib.util.find_spec("jsonschema") is None
    add("schema_validation_tool", "optional_missing" if validation_missing else "ok",
        "Optional export validation needs requirements-ci.lock installed with --require-hashes; collection does not require it."
        if validation_missing else "Schema validation tooling is installed; no export was validated by doctor.")

    values = dict(os.environ)
    env_path = root / ".env"
    if env_path.is_file() and importlib.util.find_spec("dotenv") is not None:
        from dotenv import dotenv_values
        # Parsing does not modify the environment. Never include the mapping in output.
        values = {**dotenv_values(env_path, interpolate=False), **values}
    from marketbot import _require_contact
    try:
        _require_contact(values.get("SEC_USER_AGENT"))
    except (ValueError, TypeError):
        add("sec_contact", "missing", "Set SEC_USER_AGENT to an application name and real contact email before collection.")
    else:
        add("sec_contact", "ok", "Contact syntax is present; no request or entitlement check was made.")
    optional = {key: bool(values.get(key)) for key in ("FMP_API_KEY", "ALPHA_VANTAGE_API_KEY", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET")}

    ancestor = target
    while not ancestor.exists() and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    writable = ancestor.is_dir() and os.access(ancestor, os.W_OK)
    add("workspace_access", "ok" if writable else "warning", "Read-only permission estimate; no probe file was created.")
    manifest = target / "universe.json"
    if manifest.exists():
        try:
            stored = load_profile(manifest)
            matches = profile is None or fingerprint(stored) == fingerprint(profile)
            add("workspace_identity", "ok" if matches else "error", "Use a fresh workspace for changed profile configuration." if not matches else "Workspace profile is parseable and matches the requested profile.")
        except (OSError, ValueError, TypeError):
            add("workspace_identity", "error", "Workspace profile cannot be validated; preserve it and inspect the manifest.")
    elif profile is not None:
        occupied = target.exists() and any(target.iterdir())
        add("workspace_identity", "error" if occupied else "ready", "Choose an empty dedicated workspace; existing unmarked data cannot be adopted." if occupied else "The first profile operation will pin the configuration here.")

    brief_summary = None
    brief_path = target / "daily_brief.json"
    if brief_path.is_file():
        try:
            summary = inspect_brief(load_brief(brief_path))
            # Source error strings can contain private provider URLs. Doctor
            # returns counts and status only; inspect is the explicit detail view.
            brief_summary = {key: summary[key] for key in ("generated_at", "pipeline_version", "health_status", "coverage")}
            brief_summary["warning_count"] = len(summary["warnings"])
            brief_summary["error_count"] = len(summary["errors"])
            add("saved_brief", "ok" if summary["health_status"] == "OK" else "warning", "Review saved source health and timestamps with inspect; saved status is not live availability.")
        except (OSError, ValueError, TypeError):
            add("saved_brief", "error", "Saved brief is unreadable or malformed; preserve it before retrying.")
    else:
        add("saved_brief", "missing", "Use demo to preview the viewer, or configure a collection run.")
    return {"read_only": True, "network_requests": 0, "python": sys.version.split()[0],
            "missing_dependencies": missing, "optional_credentials_present": optional,
            "checks": checks, "saved_brief": brief_summary,
            "interpretation": "Diagnostics inspect local configuration only; they do not certify data quality or provider coverage."}
