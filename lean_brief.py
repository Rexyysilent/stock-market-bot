"""Lean, clearly labelled projection of a daily brief for sharing with models.

The canonical ``daily_brief.json`` stays the record of the run. This derived
file keeps the market sections, summary, universe, field conventions and a
compact warning list, and drops the per-source diagnostics and data-quality
exclusion records that make up most of the canonical file's size.

Usage: python lean_brief.py [daily_brief.json] [-o lean_brief.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from stateutil import atomic_write_bytes, atomic_write_json
from timeutil import to_utc_z

LEAN_BRIEF_CONTRACT = "lean-brief-1"
LEAN_BRIEF_FILENAME = "lean_brief.json"
LEAN_BRIEF_DIR = "lean_briefs"
LEAN_BRIEF_KEEP_DAYS = 7
_DATED_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{6})Z\.json")
WARNING_MAX_CHARS = 240
KEPT_TOP_LEVEL = (
    "schema_version", "pipeline_version", "generated_at", "run_context",
    "universe", "conventions", "summary", "sections",
)
OMITTED = (
    "data_quality: per-record exclusion and normalization audit",
    "health.sources: per-source request diagnostics",
)
READING_RULES = (
    "null means unknown or not measured, never zero; observation_status values "
    "such as censored, partial or unavailable mean the value was not observed.",
    "An empty section can mean a source failed: read health_summary.warnings "
    "before concluding that nothing happened.",
    "Use values as given. Do not compute returns, fill missing values or "
    "convert units; field meanings are in conventions.",
    "Labels such as BULLISH_VS_BASELINE, TOP200_ENTRANCE, SOCIAL_BURST or "
    "RISK_ON are deterministic threshold classifications, not recommendations "
    "or forecasts.",
    "independent_corroboration is publisher breadth: several outlets carrying "
    "one story is not independent confirmation.",
    "Cross-family confluence is coincidence within 48 hours, not proof that "
    "the families are independent or causally related.",
    "When stating a fact from this file, cite its record_id or source link.",
)

_URL = re.compile(r"https?://\S+")
_LONG_TOKEN = re.compile(r"\S{80,}")  # e.g. relative query paths in errors


def _compact_warning(text):
    text = _LONG_TOKEN.sub("<long>", _URL.sub("<url>", str(text)))
    if len(text) > WARNING_MAX_CHARS:
        text = text[:WARNING_MAX_CHARS - 1].rstrip() + "…"
    return text


def build_lean_brief(brief, source_sha256=None):
    """Return the lean projection; ``brief`` is not modified."""
    if not isinstance(brief, dict):
        raise TypeError("brief must be a JSON object")
    health = brief.get("health") if isinstance(brief.get("health"), dict) else {}
    share = {
        "artifact": {
            "kind": "lean_brief",
            "contract": LEAN_BRIEF_CONTRACT,
            "label": (
                "Lean brief: a projection of the daily brief for sharing with language "
                "models. Not the canonical record; not for public "
                "redistribution or use as evidence."
            ),
            "source_brief_sha256": source_sha256,
            "omitted": list(OMITTED),
        },
        "reading_rules": list(READING_RULES),
        "health_summary": {
            "status": health.get("status"),
            "warnings": [_compact_warning(w) for w in health.get("warnings") or []],
            "errors": [_compact_warning(e) for e in health.get("errors") or []],
        },
    }
    for key in KEPT_TOP_LEVEL:
        if key in brief:
            share[key] = json.loads(json.dumps(brief[key]))
    return share


def write_lean_brief(brief_path="daily_brief.json", out_path=LEAN_BRIEF_FILENAME):
    """Project an on-disk brief; the share records the brief's exact bytes.

    On failure the previous share at ``out_path`` is removed before the error
    propagates, so an earlier run's projection never passes for this run's.
    """
    try:
        raw = Path(brief_path).read_bytes()
        share = build_lean_brief(
            json.loads(raw.decode("utf-8")),
            source_sha256=hashlib.sha256(raw).hexdigest(),
        )
        # Single-line JSON: indentation whitespace costs model tokens.
        atomic_write_json(os.fspath(out_path), share, indent=None)
    except Exception:
        Path(out_path).unlink(missing_ok=True)
        raise
    return out_path


def write_dated_lean_brief(lean_path=LEAN_BRIEF_FILENAME, directory=LEAN_BRIEF_DIR,
                           keep_days=LEAN_BRIEF_KEEP_DAYS):
    """Copy a lean brief to ``directory/<generated_at>.json`` and prune old copies.

    The cutoff is measured from the brief's own ``generated_at``, not the
    clock, so replaying an old brief never removes newer copies. Only files
    named like dated copies are candidates for removal.
    Returns ``(target_path, pruned_names)``.
    """
    raw = Path(lean_path).read_bytes()
    stamp = to_utc_z(json.loads(raw.decode("utf-8")).get("generated_at"))
    if stamp is None:
        raise ValueError("lean brief has no usable generated_at")
    generated = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    directory = Path(directory)
    target = directory / generated.strftime("%Y-%m-%d_%H%M%SZ.json")
    atomic_write_bytes(target, raw)

    cutoff = generated - timedelta(days=keep_days)
    pruned = []
    for path in sorted(directory.iterdir()):
        match = _DATED_NAME.fullmatch(path.name)
        if not match or not path.is_file():
            continue
        dated = datetime.strptime(match.group(1), "%Y-%m-%d_%H%M%S").replace(
            tzinfo=timezone.utc
        )
        if dated < cutoff:
            path.unlink()
            pruned.append(path.name)
    return target, pruned


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("brief", nargs="?", default="daily_brief.json")
    parser.add_argument("-o", "--output", default=LEAN_BRIEF_FILENAME)
    args = parser.parse_args(argv)
    out = write_lean_brief(args.brief, args.output)
    before = Path(args.brief).stat().st_size
    after = Path(out).stat().st_size
    print(f"{out}: {after:,} bytes (canonical brief {before:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
