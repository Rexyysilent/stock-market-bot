"""Read-only quality summaries and comparable-run changes; no network or advice."""
from __future__ import annotations

import json
import math
from pathlib import Path

MAX_BRIEF_BYTES = 32 * 1024 * 1024


def load_brief(path):
    with Path(path).open("rb") as handle:
        data = handle.read(MAX_BRIEF_BYTES + 1)
    if len(data) > MAX_BRIEF_BYTES:
        raise ValueError("Brief exceeds the 32 MiB inspection limit")
    def reject_constant(value):
        raise ValueError(f"Non-standard JSON number: {value}")
    result = json.loads(data, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError("Brief must be a JSON object")
    return result


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def inspect_brief(brief):
    health = _dict(brief.get("health"))
    universe = _dict(brief.get("universe"))
    membership = universe.get("instrumented_tickers", universe.get("tickers"))
    tickers = list(dict.fromkeys(t for t in _list(membership) if isinstance(t, str)))
    sections = _dict(brief.get("sections"))
    prices = {row["ticker"]: row for row in _list(sections.get("prices"))
              if isinstance(row, dict) and isinstance(row.get("ticker"), str)}
    technicals = {row["ticker"]: row for row in _list(sections.get("technicals"))
                  if isinstance(row, dict) and isinstance(row.get("ticker"), str)}
    options = _dict(sections.get("options_flow"))
    expected = len(tickers) if tickers else None
    populated_prices = sorted(t for t, row in prices.items() if _finite(_dict(row).get("price")))
    result = {
        "generated_at": brief.get("generated_at"),
        "pipeline_version": brief.get("pipeline_version"),
        "universe": universe,
        "health_status": health.get("status", "UNKNOWN"),
        "warnings": _list(health.get("warnings")),
        "errors": _list(health.get("errors")),
        "source_health": _dict(health.get("sources")),
        "coverage": {
            "expected_universe": expected,
            "price_records": len(prices),
            "numeric_price_records": len(populated_prices),
            "missing_numeric_prices": sorted(set(tickers) - set(populated_prices)),
            "technical_records": len(technicals),
            "rsi_unavailable": sorted(t for t, row in technicals.items() if not _finite(_dict(row).get("rsi"))),
            "options_records": len(options),
            "options_denominator": "Not inferred from the universe; optionability differs by instrument",
        },
        "run_context": brief.get("run_context"),
        "interpretation": "This summary does not certify schema validity, provider availability, or predictive value.",
    }
    rotation = _dict(sections.get("sector_rotation"))
    if rotation and not _finite(rotation.get("spread_5d")):
        result["derived_measurement_warning"] = "Sector spread unavailable; do not interpret its legacy regime label"
    return result


def diff_briefs(previous, current):
    keys = ("pipeline_version", "schema_version")
    reasons = [f"{key} changed or is missing" for key in keys
               if previous.get(key) is None or current.get(key) is None or previous.get(key) != current.get(key)]
    # Selected daily focus is presentation, not a configuration change.
    identity_keys = ("name", "version", "instrumented_version", "tickers",
                     "instrumented_tickers", "editorial_only_tickers", "editorial_coverage_mode",
                     "configured_focus_ticker", "coverage_policy", "profile")
    old_universe, new_universe = _dict(previous.get("universe")), _dict(current.get("universe"))
    if not old_universe or not new_universe or any(old_universe.get(k) != new_universe.get(k) for k in identity_keys):
        reasons.append("universe configuration changed or is missing")
    before = _dict(previous.get("health"))
    after = _dict(current.get("health"))
    old_warnings = {str(item) for item in _list(before.get("warnings"))}
    new_warnings = {str(item) for item in _list(after.get("warnings"))}
    old_sections = _dict(previous.get("sections"))
    new_sections = _dict(current.get("sections"))
    def price_tickers(sections):
        return {row["ticker"] for row in _list(sections.get("prices"))
                if isinstance(row, dict) and isinstance(row.get("ticker"), str)}
    old_tickers, new_tickers = price_tickers(old_sections), price_tickers(new_sections)
    old_headlines = _list(old_sections.get("headlines"))
    def identity(item):
        return (str(item.get("canonical_url") or item.get("link") or item.get("url") or item.get("title") or item.get("text") or "")
                if isinstance(item, dict) else str(item))
    seen = {identity(item) for item in old_headlines}
    return {
        "previous_generated_at": previous.get("generated_at"),
        "current_generated_at": current.get("generated_at"),
        "configuration_comparable": not reasons,
        "comparison_warnings": reasons,
        "health_transition": [before.get("status", "UNKNOWN"), after.get("status", "UNKNOWN")],
        "warnings_added": sorted(new_warnings - old_warnings),
        "warnings_removed": sorted(old_warnings - new_warnings),
        "price_records_added": sorted(new_tickers - old_tickers),
        "price_records_removed": sorted(old_tickers - new_tickers),
        "new_headline_records": [item for item in _list(new_sections.get("headlines")) if identity(item) not in seen],
        "current_coverage": inspect_brief(current)["coverage"],
        "interpretation": "A removed warning is not proof of recovery. Headline identity is URL/title based, not independent-event clustering. No return attribution is calculated.",
    }
