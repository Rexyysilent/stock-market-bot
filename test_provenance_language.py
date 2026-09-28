"""M15: publisher breadth is not independent corroboration.

One filing plus two derivative reports of it is one reported origin and two
coverage reports. The legacy wire key is kept for schema compatibility, but
nothing reader-facing may describe the count as independence.
"""
from datetime import datetime, timezone
from pathlib import Path

import editorial_focus
from editorial_focus import rank_focus_candidates, render_focus_text, select_focus

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 8, 17, 15, 32, 36, tzinfo=timezone.utc)

filing = {
    "title": "ROKU files 8-K disclosing a material agreement",
    "link": "https://www.sec.gov/Archives/edgar/data/1428439/f1.htm",
    "canonical_url": "https://www.sec.gov/Archives/edgar/data/1428439/f1.htm",
    "as_of": "2026-08-17T14:00:00Z",
    "observed_at": "2026-08-17T15:00:00Z",
    "provider": "Official Feeds",
    "publisher": "SEC",
    "publisher_domain": "sec.gov",
    "source_class": "official_regulator",
    "source_record_id": "filing:F1",
    "source_time_kind": "published",
    "lane": "universe",
    "universe_tickers": ["ROKU"],
    # N1 and N2 are merged same-story reports that repeat F1.
    "duplicate_publishers": ["Reuters", "Bloomberg"],
    "duplicate_providers": ["FMP", "Google News"],
    "score_components": {
        "issuer_relevance": 2, "macro_relevance": 0, "vertical_relevance": 0,
        "authority": 3, "novelty": 1, "impact": 2,
    },
}

candidate = rank_focus_candidates([filing], ["ROKU"], NOW)[0]
# Two additional publishers carried the story: breadth 2, not two confirmations.
assert candidate["score_components"]["independent_corroboration"] == 2
assert candidate["evidence"][0]["duplicate_publishers"] == ["Bloomberg", "Reuters"]

focus = select_focus([filing], ["ROKU"], NOW)
assert focus["status"] == "selected"
assert focus["evidence_grade"] == "multiple_selected_sources"
rendered = render_focus_text(focus)
assert "publisher_breadth=2" in rendered, rendered
assert "independent" not in rendered.lower(), rendered
assert "corroborat" not in rendered.lower(), rendered

# The count is computed by a breadth helper; no code path names it independence.
source = (ROOT / "editorial_focus.py").read_text(encoding="utf-8")
assert "_independent_publisher_count" not in source
assert hasattr(editorial_focus, "_publisher_breadth")

# Reader-facing definitions state what the legacy key means.
exporter_source = (ROOT / "export_for_gemini.py").read_text(encoding="utf-8")
assert '"deep_dive.score_components.independent_corroboration"' in exporter_source
assert "independence of reporting origins is not assessed" in exporter_source
schema_doc = (ROOT / "docs" / "EXPORT_SCHEMA.md").read_text(encoding="utf-8")
assert "independent corroboration, audience" not in schema_doc
assert "publisher breadth" in schema_doc

print("M15 publisher-breadth provenance language checks passed")
