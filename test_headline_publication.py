"""Offline acceptance checks for the publication-stage headline projection.

Covers the five design cases from the brief-quality review (three-day
persistence, same-day rerun, recurring official title, malformed archive,
alternate halt wording) plus the stable-source-ID update policy, the halt
negative control, candidate accounting against the marked schema, focus-input
consistency and OMNI-02 replay independence. Synthetic archives live in a
temporary directory; the repository archive is never touched and no network
is used.
"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, ".")

import evidence_intake
from agents.news_agent import NewsAgent
from agents.news_providers import OfficialFeedProvider, ProviderResult
from editorial_focus import select_focus
from export_for_gemini import (
    add_record_metadata,
    build_dropped_headline_export_records,
    build_headline_export_records,
    normalize_headline_pool_diagnostics,
)
from headline_publication import (
    REASON_REPEAT,
    REASON_UNRELATED_HALT,
    VERSION,
    apply_publication_filter,
    load_publication_history,
    publication_warning,
)
from scripts.validate_export_schema import (
    DEFAULT_FIXTURE,
    DEFAULT_SCHEMA,
    load_json,
    validation_errors,
)


NOW = datetime(2026, 8, 17, 15, 32, 36, tzinfo=timezone.utc)
WINDOW_DAYS = NewsAgent.HEADLINES_MAX_AGE_DAYS
TARGET = NewsAgent.TARGET_COUNT
assert WINDOW_DAYS == 3 and TARGET == 5, "cases below assume the 2.6.5 defaults"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def selected_row(source_id, title, *, ticker=None, lane=None,
                 as_of="2026-08-17T14:00:00Z", provider="Fixture Wire",
                 publisher_domain="fixture.example", impact=2):
    """One intake-selected row in the agent's post-projection shape."""
    universe = [ticker] if ticker else []
    lane = lane or ("universe" if ticker else "macro")
    return {
        "text": f"[{provider}] {title}",
        "title": title,
        "link": f"https://{publisher_domain}/{source_id.replace(':', '-')}",
        "canonical_url": f"https://{publisher_domain}/{source_id.replace(':', '-')}",
        "published": as_of,
        "source_time_kind": "published",
        "provider_seen_at": "2026-08-17T15:00:00Z",
        "provider": provider,
        "publisher": publisher_domain,
        "publisher_domain": publisher_domain,
        "source_class": "aggregator",
        "source_record_id": source_id,
        "tickers": list(universe),
        "ticker_metadata_kind": "subject",
        "summary": None,
        "lane": lane,
        "universe_tickers": universe,
        "score_components": {
            "issuer_relevance": 2 if ticker else 0,
            "macro_relevance": 0 if ticker else 1,
            "vertical_relevance": 0,
            "authority": 2,
            "novelty": 1,
            "impact": impact,
        },
        "relevance": 2 if ticker else 1,
    }


def archived_row(source_id, title, as_of, **extra):
    row = {
        "source_record_id": source_id,
        "title": title,
        "text": f"[Fixture Wire] {title}",
        "as_of": as_of,
        "publisher": "fixture.example",
        "publisher_domain": "fixture.example",
    }
    row.update(extra)
    return row


def write_archive(directory, stamp, rows, *, generated_at="from_name", raw=None):
    name = f"{stamp}.json"
    if generated_at == "from_name":
        generated_at = f"{stamp[:10]}T{stamp[11:13]}:{stamp[13:15]}:{stamp[15:17]}Z"
    document = {
        "schema_version": "2.8",
        "generated_at": generated_at,
        "sections": {"headlines": rows},
    }
    path = Path(directory) / name
    path.write_bytes(raw if raw is not None else json.dumps(document).encode("utf-8"))
    return name


def archive_hashes(directory):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(Path(directory).iterdir())
    }


# ---------------------------------------------------------------------------
# Case 1: three-day persistence. A story published on day 1, withheld on day
# 2 (so day 2's archive lacks it), must still be withheld on day 3 because
# every eligible earlier-day archive is consulted, not only the latest.
story = selected_row("wire:story", "Issuer announces a financing", ticker="TSLA")
fresh = selected_row("wire:fresh", "Treasury yields climb before the Fed decision")

with tempfile.TemporaryDirectory() as archive_dir:
    day1 = write_archive(archive_dir, "2026-08-14_120000Z", [
        archived_row("wire:story", "Issuer announces a financing", "2026-08-14T11:00:00Z"),
    ])
    day2 = write_archive(archive_dir, "2026-08-15_120000Z", [
        archived_row("wire:other", "Another story", "2026-08-15T11:00:00Z"),
    ])
    before = archive_hashes(archive_dir)
    history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    assert history["status"] == "ok", history
    assert history["window_utc_dates"] == ["2026-08-14", "2026-08-16"]
    assert [entry["file"] for entry in history["archives"]] == [day1, day2]
    assert all(entry["used_rows"] == 1 and entry["skipped_rows"] == 0
               for entry in history["archives"])

    published, withheld, receipt = apply_publication_filter(
        [story, fresh], history, TARGET
    )
    assert [row["source_record_id"] for row in published] == ["wire:fresh"]
    assert [row["drop_reason"] for row in withheld] == [REASON_REPEAT]
    assert withheld[0]["source_record_id"] == "wire:story"
    assert receipt["version"] == VERSION
    assert receipt["withheld_rows"] == [{
        "source_record_id": "wire:story", "lane": "universe",
        "reason": REASON_REPEAT, "matched_by": "source_record_id",
        "matched_archive": day1,
    }]
    assert receipt["intake_selected_count"] == 2
    assert receipt["published_count"] == 1
    assert receipt["withheld"] == {REASON_REPEAT: 1, REASON_UNRELATED_HALT: 0}
    assert receipt["underfill"] is True
    assert "1 of 5 headline slot(s) withheld" in publication_warning(receipt)
    assert "not refilled" in publication_warning(receipt)
    # File names only; nothing resembling a path reaches the receipt.
    assert "/" not in canonical(receipt) and "\\" not in canonical(receipt)

    # Case 2: same-day rerun and clock problems. Archives dated today (UTC)
    # or later, and archives older than the window, contribute no identity.
    same_day = selected_row("wire:same-day", "Published earlier today", ticker="ROKU")
    future = selected_row("wire:future", "From a future-dated file")
    old = selected_row("wire:old", "From outside the window", ticker="NXE")
    write_archive(archive_dir, "2026-08-17_090000Z", [
        archived_row("wire:same-day", "Published earlier today", "2026-08-17T08:00:00Z"),
    ])
    write_archive(archive_dir, "2026-08-18_090000Z", [
        archived_row("wire:future", "From a future-dated file", "2026-08-18T08:00:00Z"),
    ])
    write_archive(archive_dir, "2026-08-13_090000Z", [
        archived_row("wire:old", "From outside the window", "2026-08-13T08:00:00Z"),
    ])
    Path(archive_dir, "notes.txt").write_text("not an archive", encoding="utf-8")
    history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    assert history["status"] == "ok"
    assert history["ignored_same_day"] == 1
    assert history["ignored_future"] == 1
    assert history["ignored_out_of_window"] == 1
    assert [entry["file"] for entry in history["archives"]] == [day1, day2]
    published, withheld, receipt = apply_publication_filter(
        [same_day, future, old, story], history, TARGET
    )
    assert [row["source_record_id"] for row in published] == [
        "wire:same-day", "wire:future", "wire:old",
    ]
    assert [row["source_record_id"] for row in withheld] == ["wire:story"]

    # Case 3: recurring official title. A new source record with a new as_of
    # is a new publication; the same title with the same as_of is a repeat;
    # missing values never establish identity.
    write_archive(archive_dir, "2026-08-16_120000Z", [
        archived_row("official:x1", "Weekly reserve statement", "2026-08-16T13:00:00Z"),
        archived_row("", "Orphan without identity", None),
        archived_row(None, "Orphan with title only", "   "),
    ])
    history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    day3 = history["archives"][-1]
    assert day3["file"] == "2026-08-16_120000Z.json"
    assert day3["used_rows"] == 1 and day3["skipped_rows"] == 2
    # Rows that could not be compared are publications this run cannot see,
    # so the archive is incomplete and the history is partial, not ok.
    assert day3["complete"] is False
    assert all(entry["complete"] for entry in history["archives"][:-1])
    assert history["status"] == "partial", history["status"]
    recurring_new = selected_row(
        "official:x2", "Weekly reserve statement", as_of="2026-08-17T13:00:00Z",
    )
    recurring_same_time = selected_row(
        "official:x3", "Weekly reserve statement", as_of="2026-08-16T13:00:00Z",
    )
    recurring_undated = selected_row(
        "official:x4", "Weekly reserve statement", as_of="2026-08-17T13:00:00Z",
    )
    recurring_undated["published"] = None
    orphan_title = selected_row("wire:orphan", "Orphan without identity")
    published, withheld, receipt = apply_publication_filter(
        [recurring_new, recurring_same_time, recurring_undated, orphan_title],
        history, TARGET,
    )
    assert [row["source_record_id"] for row in published] == [
        "official:x2", "official:x4", "wire:orphan",
    ]
    assert [row["source_record_id"] for row in withheld] == ["official:x3"]
    assert receipt["withheld_rows"][0]["matched_by"] == "title_as_of"
    assert receipt["withheld_rows"][0]["matched_archive"] == day3["file"]
    assert receipt["history"]["status"] == "partial"
    assert "repeat history partial" in publication_warning(receipt)

    # An archive whose every row is skipped reads but compares nothing. It
    # must not count as clean history.
    with tempfile.TemporaryDirectory() as skipped_dir:
        write_archive(skipped_dir, "2026-08-16_120000Z", [
            {"record_id": ["x"], "title": None, "as_of": None},
            {"source_record_id": "", "title": "", "as_of": ""},
        ])
        all_skipped = load_publication_history(skipped_dir, NOW, WINDOW_DAYS)
        assert all_skipped["status"] == "partial", all_skipped
        assert all_skipped["archives"][0]["used_rows"] == 0
        assert all_skipped["archives"][0]["skipped_rows"] == 2
        assert all_skipped["archives"][0]["complete"] is False
        assert all_skipped["unreadable"] == []
        # An archive with a legitimately empty headline section is complete.
        write_archive(skipped_dir, "2026-08-15_120000Z", [])
        with_empty = load_publication_history(skipped_dir, NOW, WINDOW_DAYS)
        assert with_empty["archives"][0]["complete"] is True
        assert with_empty["status"] == "partial"  # the skipped file still counts

    # Case 5b: stable source ID with changed content and time. This is the
    # documented v1 limitation: the document identity wins and the row is
    # withheld, visibly, as matched_by source_record_id.
    updated = selected_row(
        "wire:story", "Issuer revises the financing terms",
        ticker="TSLA", as_of="2026-08-17T15:00:00Z",
    )
    published, withheld, receipt = apply_publication_filter([updated], history, TARGET)
    assert published == [] and withheld[0]["drop_reason"] == REASON_REPEAT
    assert receipt["withheld_rows"][0]["matched_by"] == "source_record_id"
    assert receipt["withheld_rows"][0]["matched_archive"] == day1

    # Reading never changed a byte of any archive.
    after = archive_hashes(archive_dir)
    assert {k: v for k, v in after.items() if k in before} == before

# ---------------------------------------------------------------------------
# Case 4: malformed archives. Invalid rows are skipped, unusable files are
# listed, a mismatched or missing generated_at disqualifies a file, and
# publication proceeds without an exception or a rewrite.
with tempfile.TemporaryDirectory() as archive_dir:
    valid_with_junk = write_archive(archive_dir, "2026-08-15_100000Z", [
        {"record_id": ["list"], "source_record_id": ["list"],
         "title": None, "as_of": "2026-08-15T09:00:00Z"},
        {"source_record_id": 12, "title": 5, "as_of": 7},
        "not a mapping",
        archived_row("wire:valid", "A valid earlier publication", "2026-08-15T09:00:00Z"),
    ])
    broken_json = write_archive(
        archive_dir, "2026-08-16_100000Z", [], raw=b"{not json",
    )
    not_a_list = write_archive(archive_dir, "2026-08-14_100000Z", [], raw=json.dumps({
        "generated_at": "2026-08-14T10:00:00Z", "sections": {"headlines": {"a": 1}},
    }).encode("utf-8"))
    mismatch = write_archive(archive_dir, "2026-08-14_110000Z", [
        archived_row("wire:mismatch", "Time label disagrees", "2026-08-14T10:30:00Z"),
    ], generated_at="2026-08-13T11:00:00Z")
    null_generated = write_archive(archive_dir, "2026-08-15_110000Z", [
        archived_row("wire:nullgen", "No generated_at", "2026-08-15T10:30:00Z"),
    ], generated_at=None)
    write_archive(archive_dir, "2026-13-40_110000Z", [])  # malformed date in name
    before = archive_hashes(archive_dir)

    history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    assert history["status"] == "partial", history
    assert [entry["file"] for entry in history["archives"]] == [valid_with_junk]
    assert history["archives"][0]["rows"] == 4
    assert history["archives"][0]["used_rows"] == 1
    assert history["archives"][0]["skipped_rows"] == 3
    problems = {entry["file"]: entry["problem"] for entry in history["unreadable"]}
    assert problems == {
        broken_json: "unreadable",
        not_a_list: "headlines_not_a_list",
        mismatch: "generated_at_mismatch",
        null_generated: "generated_at_mismatch",
        "2026-13-40_110000Z.json": "malformed_name",
    }, problems
    assert all("sha256" in entry for entry in history["unreadable"]
               if entry["problem"] != "unreadable"
               and entry["problem"] != "malformed_name")

    rows = [
        selected_row("wire:valid", "A valid earlier publication", ticker="TSLA"),
        selected_row("wire:mismatch", "Time label disagrees"),
        selected_row("wire:nullgen", "No generated_at", ticker="ROKU"),
    ]
    published, withheld, receipt = apply_publication_filter(rows, history, TARGET)
    assert [row["source_record_id"] for row in published] == [
        "wire:mismatch", "wire:nullgen",
    ]
    assert [row["source_record_id"] for row in withheld] == ["wire:valid"]
    assert receipt["history"]["status"] == "partial"
    assert "repeat history partial" in publication_warning(receipt)
    assert archive_hashes(archive_dir) == before

# An absent directory is unavailable history: nothing is withheld as a repeat
# and the brief says so; an empty directory is simply empty history.
missing = load_publication_history("/nonexistent/archive/dir", NOW, WINDOW_DAYS)
assert missing["status"] == "unavailable"
published, withheld, receipt = apply_publication_filter([story], missing, TARGET)
assert published == [story] and withheld == []
assert "repeat history unavailable" in publication_warning(receipt)
with tempfile.TemporaryDirectory() as archive_dir:
    empty = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    assert empty["status"] == "empty"
    published, withheld, receipt = apply_publication_filter([story], empty, TARGET)
    assert published == [story] and publication_warning(receipt) == ""

# ---------------------------------------------------------------------------
# Case 5: halt provenance. Halt rows are recognised only by the parser's
# explicit source_parser value, never by title wording. Unrelated halts are
# withheld whatever lane they landed in; universe halts and generic-feed
# exchange-wide notices are retained; the shape alone is never enough.


class Response:
    def __init__(self, content=b"", status=200):
        self.content = content
        self.status_code = status


NASDAQ = b"""<?xml version='1.0'?><rss xmlns:n='http://www.nasdaqtrader.com/'>
<channel>
<item><title>ZZZ</title><link>https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts</link>
<n:IssueSymbol>ZZZ</n:IssueSymbol><n:IssueName>Zed Corp</n:IssueName>
<n:HaltDate>08/17/2026</n:HaltDate><n:HaltTime>10:31:00.000</n:HaltTime>
<n:ReasonCode>LUDP</n:ReasonCode></item>
<item><title>TSLA</title><link>https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts</link>
<n:IssueSymbol>TSLA</n:IssueSymbol><n:IssueName>Tesla Inc.</n:IssueName>
<n:HaltDate>08/17/2026</n:HaltDate><n:HaltTime>10:32:00.000</n:HaltTime>
<n:ReasonCode>T1</n:ReasonCode></item>
<item><title>Trading Halts - QQQ</title><link>https://www.nasdaqtrader.com/trader.aspx?id=TradeHalts</link>
<n:IssueSymbol>QQQ</n:IssueSymbol>
<n:HaltDate>08/17/2026</n:HaltDate><n:HaltTime>10:33:00.000</n:HaltTime></item>
</channel></rss>"""
GENERIC = b"""<?xml version='1.0'?><rss><channel>
<item><title>Nasdaq: Trading halt after a systems failure</title>
<link>https://www.nasdaq.com/market-activity/notices/1</link>
<guid>nasdaq-notice-1</guid>
<pubDate>Mon, 17 Aug 2026 13:00:00 GMT</pubDate>
<description>Exchange-wide notice.</description></item>
</channel></rss>"""


def official_get(url, **kwargs):
    if url.endswith("halts.xml"):
        return Response(content=NASDAQ)
    return Response(content=GENERIC)


feed_result = OfficialFeedProvider(
    [
        {
            "url": "https://nasdaq.test/halts.xml",
            "publisher": "Nasdaq Trader",
            "publisher_url": "https://www.nasdaqtrader.com/",
            "source_class": "official_exchange",
            "parser": "nasdaq_halts",
        },
        {
            "url": "https://nasdaq.test/notices.xml",
            "publisher": "Nasdaq",
            "publisher_url": "https://www.nasdaq.com/",
            "source_class": "official_exchange",
        },
    ],
    now=NOW,
    request_get=official_get,
).fetch()
assert feed_result.status == "ok" and len(feed_result.records) == 4
by_title = {row["title"]: row for row in feed_result.records}
assert by_title["ZZZ (Zed Corp) trading halt - LUDP"]["source_parser"] == "nasdaq_halts"
assert by_title["Trading Halts - QQQ"]["source_parser"] == "nasdaq_halts"
assert by_title["Nasdaq: Trading halt after a systems failure"]["source_parser"] == "generic"


def classified(record):
    components = NewsAgent._score_components(record)
    return {
        **record,
        "text": f"[{record['provider']}] {record['title']}",
        "lane": NewsAgent._assign_lane(record, components),
        "universe_tickers": NewsAgent._matched_universe_tickers(record),
        "score_components": components,
        "relevance": NewsAgent._compatibility_relevance(components),
    }


halt_rows = [classified(row) for row in feed_result.records]
lanes = {row["title"]: row["lane"] for row in halt_rows}
assert lanes["ZZZ (Zed Corp) trading halt - LUDP"] == "discovery", lanes
assert lanes["Trading Halts - QQQ"] == "macro", lanes
assert lanes["TSLA (Tesla Inc.) trading halt - T1"] == "universe", lanes
assert lanes["Nasdaq: Trading halt after a systems failure"] == "macro", lanes

# Negative control: an official non-halt event with exactly the halt row's
# shape (event time, no canonical URL, a non-universe ticker) but without the
# parser identity is published. Provenance is explicit or unknown, never
# inferred from shape.
shape_only = dict(by_title["ZZZ (Zed Corp) trading halt - LUDP"])
shape_only.update({
    "title": "ZZZ (Zed Corp) listing transfer notice",
    "source_record_id": "official-feeds:shape-only",
    "source_parser": "generic",
})
shape_absent = dict(shape_only)
shape_absent.pop("source_parser")
shape_absent["source_record_id"] = "official-feeds:shape-absent"
controls = [classified(shape_only), classified(shape_absent)]
assert all(not row["universe_tickers"] for row in controls)

with tempfile.TemporaryDirectory() as archive_dir:
    no_history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    published, withheld, receipt = apply_publication_filter(
        halt_rows + controls, no_history, TARGET
    )
assert sorted(row["title"] for row in withheld) == [
    "Trading Halts - QQQ", "ZZZ (Zed Corp) trading halt - LUDP",
]
assert all(row["drop_reason"] == REASON_UNRELATED_HALT for row in withheld)
assert sorted(row["title"] for row in published) == [
    "Nasdaq: Trading halt after a systems failure",
    "TSLA (Tesla Inc.) trading halt - T1",
    "ZZZ (Zed Corp) listing transfer notice",
    "ZZZ (Zed Corp) listing transfer notice",
]
assert receipt["withheld"] == {REASON_REPEAT: 0, REASON_UNRELATED_HALT: 2}
assert all(item["matched_by"] == "source_parser" and item["matched_archive"] is None
           for item in receipt["withheld_rows"])

# ---------------------------------------------------------------------------
# End to end through NewsAgent: parser identity survives intake selection,
# the unrelated halt is withheld at publication, and OMNI-02 replay of the
# intake manifest still matches because publication never touches intake.


class StaticProvider:
    name = "Official Feeds"

    def __init__(self, rows):
        self.rows = rows

    def fetch(self):
        return ProviderResult(self.name, "ok", [dict(r) for r in self.rows])


class EmptyGoogleProvider:
    name = "Google News"

    def fetch(self):
        return ProviderResult(self.name, "empty", [])

    def fetch_query(self, query, limit=None):
        return [], None


agent = NewsAgent(
    now=NOW,
    providers=[StaticProvider([
        by_title["ZZZ (Zed Corp) trading halt - LUDP"],
        by_title["TSLA (Tesla Inc.) trading halt - T1"],
        by_title["Nasdaq: Trading halt after a systems failure"],
    ])],
    google_provider=EmptyGoogleProvider(),
)
agent.get_global_headlines()
intake = agent.get_scored_headlines()
assert sorted(row["title"] for row in intake) == [
    "Nasdaq: Trading halt after a systems failure",
    "TSLA (Tesla Inc.) trading halt - T1",
    "ZZZ (Zed Corp) trading halt - LUDP",
], [row["title"] for row in intake]
assert all("source_parser" in row for row in intake)
with tempfile.TemporaryDirectory() as archive_dir:
    published, withheld, receipt = apply_publication_filter(
        intake, load_publication_history(archive_dir, NOW, WINDOW_DAYS), TARGET
    )
assert [row["title"] for row in withheld] == ["ZZZ (Zed Corp) trading halt - LUDP"]
assert len(published) == 2
manifest = agent.get_pool_diagnostics()["evidence_intake"]
assert manifest["status"] == "ok", manifest
replayed = evidence_intake.replay(manifest)
assert replayed["selection_matches"] is True, replayed
assert "official-feeds:" in "".join(replayed["selected_source_ids"])
assert len(replayed["selected_source_ids"]) == 3, "intake selection is untouched"

# Exported rows carry the parser identity (null when unknown) in both the
# selected and the dropped contract shapes.
exported = build_headline_export_records(published)
assert {row["source_parser"] for row in exported} == {"nasdaq_halts", "generic"}
assert build_dropped_headline_export_records(withheld)[0]["source_parser"] == "nasdaq_halts"
assert build_headline_export_records([story])[0]["source_parser"] is None

# ---------------------------------------------------------------------------
# Accounting, schema validity, focus input and no-refill on a full document.
focus_row = selected_row("wire:nxe-financing", "NXE announces a financing", ticker="NXE")
macro_row = selected_row("wire:macro", "Treasury yields climb before the Fed decision")
repeat_row = selected_row("wire:story", "Issuer announces a financing", ticker="TSLA", impact=3)
halt_row = classified(by_title["ZZZ (Zed Corp) trading halt - LUDP"])
intake_rows = [repeat_row, focus_row, macro_row, halt_row]

with tempfile.TemporaryDirectory() as archive_dir:
    day1 = write_archive(archive_dir, "2026-08-14_120000Z", [
        archived_row("wire:story", "Issuer announces a financing", "2026-08-14T11:00:00Z"),
    ])
    history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
    published, withheld, receipt = apply_publication_filter(intake_rows, history, TARGET)

assert [row["source_record_id"] for row in published] == ["wire:nxe-financing", "wire:macro"]
assert len(withheld) == 2 and receipt["underfill"] is True
# A withheld high-impact repeat cannot become the focus: the focus consumes
# the published rows only.
document = load_json(DEFAULT_FIXTURE)
generated_at = document["generated_at"]
focus = select_focus(published, document["universe"]["focus_eligible_tickers"], generated_at)
assert focus["status"] == "selected" and focus["ticker"] == "NXE"
assert {row["source_record_id"] for row in focus["evidence"]} == {"wire:nxe-financing"}
assert select_focus(intake_rows, document["universe"]["focus_eligible_tickers"], generated_at)["ticker"] == "TSLA", (
    "the repeat would have won on the intake rows; publication removed it"
)

sections = {"headlines": build_headline_export_records(published)}
assert add_record_metadata(sections) == []
document["sections"]["headlines"] = sections["headlines"]
document["sections"]["deep_dive"] = focus
document["summary"]["editorial_focus"] = {
    "status": focus["status"], "ticker": focus["ticker"],
    "selection_mode": focus["selection_mode"],
    "eligible_candidate_count": focus["eligible_candidate_count"],
}
document["summary"]["editorial_focus_selection_mode"] = focus["selection_mode"]
document["summary"]["editorial_focus_eligible_candidates"] = focus["eligible_candidate_count"]
document["summary"]["total_headlines"] = len(published)
document["universe"].update({
    "focus_ticker": focus["ticker"],
    "focus_coverage_tier": focus["coverage_tier"],
    "focus_signal_eligible": focus["signal_eligible"],
    "focus_signal_eligibility_reason": focus["signal_eligibility_reason"],
    "configured_focus_ticker": None,
    "focus_selection_mode": focus["selection_mode"],
})
dropped_records = build_dropped_headline_export_records(withheld)
assert sorted(row["reason"] for row in dropped_records) == sorted([
    REASON_REPEAT, REASON_UNRELATED_HALT,
])
assert all(row["kept_source_record_id"] is None for row in dropped_records)
document["data_quality"]["headlines_dropped"] = dropped_records
pool = dict(document["data_quality"]["headline_pool"])
# The fixture's shadow examples resolve to the fixture's own headline rows,
# which this document replaces; the shadow projection is out of scope here.
pool["editorial_shadow"] = {
    **pool["editorial_shadow"], "candidate_count": 0,
    "fresh_candidate_count": 0, "records": [],
}
pool.update({
    "fetched_count": len(intake_rows),
    "fresh_before_relevance": len(intake_rows),
    # Stale intake-stage diagnostics that publication must re-derive.
    "selected_provider_counts": {"stale": 99},
    "selected_publisher_counts": {"stale": 99},
    "selected_provider_lane_counts": {"stale": 99},
    "selected_publisher_lane_counts": {"stale": 99},
    "unique_selected_publishers": 99,
    "selected_publisher_max_share": 0.99,
    "google_selected_count": 99,
})
pool = normalize_headline_pool_diagnostics(pool, published, withheld)
assert pool["selected_count"] == 2
assert pool["accounted_candidate_count"] == 4 == pool["fetched_count"]
assert pool["selected_lane_counts"] == {"universe": 1, "macro": 1}
assert pool["selected_provider_counts"] == {"Fixture Wire": 2}
assert pool["selected_publisher_counts"] == {"fixture.example": 2}
assert pool["selected_provider_lane_counts"] == {
    "universe:Fixture Wire": 1, "macro:Fixture Wire": 1,
}
assert pool["selected_publisher_lane_counts"] == {
    "universe:fixture.example": 1, "macro:fixture.example": 1,
}
assert pool["unique_selected_publishers"] == 1
assert pool["selected_publisher_max_share"] == 1.0
assert pool["google_selected_count"] == 0
document["data_quality"]["headline_pool"] = pool
document["data_quality"]["headline_publication"] = receipt
errors = validation_errors(document, load_json(DEFAULT_SCHEMA))
assert errors == [], [str(error)[:300] for error in errors]

# Publication withholding receives intake-selected provider rows. Their source
# timestamps may still be naive (FMP) or offset-qualified (RSS); dropped rows
# bypass add_record_metadata, so their export builder owns normalization.
raw_withheld = deepcopy(withheld)
raw_withheld[0].update({
    "as_of": "2026-08-16 02:02:48",
    "published": "2026-08-16 02:02:48",
    "provider_seen_at": "2026-08-17T17:00:00+02:00",
})
normalized_drops = build_dropped_headline_export_records(raw_withheld)
assert normalized_drops[0]["as_of"] == "2026-08-16T00:00:00Z", normalized_drops[0]
assert normalized_drops[0]["observed_at"] == "2026-08-17T15:00:00Z"
assert normalized_drops[0]["published"] == "2026-08-16 02:02:48"
normalized_document = deepcopy(document)
normalized_document["data_quality"]["headlines_dropped"] = normalized_drops
assert validation_errors(normalized_document, load_json(DEFAULT_SCHEMA)) == []
raw_withheld[0].update({"as_of": "unavailable", "provider_seen_at": "unavailable"})
unknown_drop = build_dropped_headline_export_records(raw_withheld)[0]
assert unknown_drop["as_of"] is None and unknown_drop["observed_at"] is None

# The fail-soft pool contract is unchanged: absent diagnostics stay absent.
assert "selected_provider_counts" not in normalize_headline_pool_diagnostics({}, [], [])

# Text lines are the published rows' text, so JSON and text agree.
assert [row["text"] for row in published] == [
    "[Fixture Wire] NXE announces a financing",
    "[Fixture Wire] Treasury yields climb before the Fed decision",
]

# ---------------------------------------------------------------------------
# Structured health. Five intake rows that become three published rows must
# be reported as three by health, with a publication warning that reaches
# every health consumer (dashboard quality panel, lean brief health_summary),
# and a partial history must be warned about there too.
from export_for_gemini import build_export_health
from headline_publication import (
    publication_health_summary,
    publication_health_warnings,
)
from lean_brief import build_lean_brief


class HealthAgent:
    def __init__(self, health=None):
        self.health = health or {}

    def get_health(self):
        return dict(self.health)


class IntakeNewsHealth:
    """Agent diagnostics as the intake stage reports them: five selected."""

    def get_pool_diagnostics(self):
        return {
            "fetched_count": 9, "fresh_before_relevance": 9,
            "fresh_relevant_count": 5, "selected_count": 5,
            "fallback_used": False, "providers": [],
        }


class ResearchHealth:
    adcom_parse_failed = False
    adcom_detail_failures = []

    def get_ceo_ca_health(self):
        return {}


five_intake = [
    selected_row("wire:a", "Story A", ticker="TSLA"),
    selected_row("wire:b", "Story B", ticker="ROKU"),
    selected_row("wire:c", "Story C"),
    selected_row("wire:d", "Story D", ticker="NXE"),
    classified(by_title["ZZZ (Zed Corp) trading halt - LUDP"]),
]
with tempfile.TemporaryDirectory() as archive_dir:
    write_archive(archive_dir, "2026-08-16_120000Z", [
        archived_row("wire:a", "Story A", "2026-08-16T11:00:00Z"),
        {"record_id": ["malformed"], "title": None, "as_of": None},
    ])
    partial_history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
assert partial_history["status"] == "partial"
published, withheld, receipt = apply_publication_filter(
    five_intake, partial_history, TARGET
)
assert receipt["intake_selected_count"] == 5 and receipt["published_count"] == 3
pool = normalize_headline_pool_diagnostics(
    {"fetched_count": 9, "fresh_before_relevance": 9, "fresh_relevant_count": 5,
     "selected_count": 5, "fallback_used": False, "providers": []},
    published,
    [selected_row(f"wire:dropped-{i}", f"Dropped {i}") for i in range(4)] + withheld,
)
assert pool["selected_count"] == 3


def health_for(headline_pool=None, headline_publication=None):
    return build_export_health(
        "2026-08-17T15:34:26Z", 1.0, [], {}, {}, [], [], {}, [], [], [],
        IntakeNewsHealth(), HealthAgent(), HealthAgent(), HealthAgent(),
        HealthAgent({
            "openinsider_cluster_rows_considered": 0,
            "openinsider_cluster_rows_eligible": 0,
            "openinsider_cluster_rows_dropped": {},
        }),
        ResearchHealth(),
        headline_pool=headline_pool,
        headline_publication=headline_publication,
    )


health = health_for(pool, receipt)
assert health["status"] == "WARN"
assert health["sources"]["news"]["selected_count"] == 3, (
    "health must report the published rows, not the intake selection"
)
assert health["sources"]["news"]["publication"] == publication_health_summary(receipt)
assert health["sources"]["news"]["publication"]["published_count"] == 3
assert health["sources"]["news"]["publication"]["underfill"] is True
assert health["sources"]["news"]["publication"]["history_status"] == "partial"
expected_warnings = publication_health_warnings(receipt)
assert len(expected_warnings) == 2
assert expected_warnings[0] == (
    "Headline publication withheld 2 of 5 intake row(s) (repeats=1, "
    "unrelated_halts=1); published 3/5; withheld slots are not refilled."
)
assert expected_warnings[1].startswith(
    "Headline repeat history is partial for UTC dates 2026-08-14..2026-08-16"
)
for message in expected_warnings:
    assert message in health["warnings"], health["warnings"]
# Intake filled all five slots, so the intake-stage underfill warning stays
# out: publication underfill is reported as publication underfill.
assert not any(
    warning.startswith("Publisher-diverse headline section underfilled")
    for warning in health["warnings"]
), health["warnings"]

# A caller without the projection keeps the historical behaviour.
legacy_health = health_for()
assert legacy_health["sources"]["news"]["selected_count"] == 5
assert "publication" not in legacy_health["sources"]["news"]
assert not any("Headline publication" in w for w in legacy_health["warnings"])

# An intake underfill is still reported from the intake count, and a clean
# publication adds no publication warning.
with tempfile.TemporaryDirectory() as archive_dir:
    clean_history = load_publication_history(archive_dir, NOW, WINDOW_DAYS)
three_intake = five_intake[:3]
clean_published, clean_withheld, clean_receipt = apply_publication_filter(
    three_intake, clean_history, TARGET
)
assert clean_withheld == [] and publication_health_warnings(clean_receipt) == []
clean_pool = normalize_headline_pool_diagnostics(
    {"fetched_count": 3, "fresh_before_relevance": 3, "fresh_relevant_count": 3,
     "selected_count": 3, "fallback_used": False, "providers": []},
    clean_published, [],
)
clean_health = health_for(clean_pool, clean_receipt)
assert "Publisher-diverse headline section underfilled: 3/5." in clean_health["warnings"]
assert clean_health["sources"]["news"]["publication"]["underfill"] is True
assert not any("Headline publication" in w for w in clean_health["warnings"])

# The lean brief's health_summary carries the structured warnings, so a
# model-facing reader also sees the publication underfill.
lean = build_lean_brief({
    "schema_version": "2.8", "pipeline_version": "2.6.5",
    "generated_at": "2026-08-17T15:34:26Z", "health": health,
    "summary": {"total_headlines": 3}, "sections": {"headlines": []},
    "data_quality": {"headline_publication": receipt},
})
assert any(
    warning.startswith("Headline publication withheld 2 of 5")
    for warning in lean["health_summary"]["warnings"]
), lean["health_summary"]

# Inputs are never mutated by the projection.
pristine = deepcopy(intake_rows)
with tempfile.TemporaryDirectory() as archive_dir:
    apply_publication_filter(
        intake_rows, load_publication_history(archive_dir, NOW, WINDOW_DAYS), TARGET
    )
assert canonical(intake_rows) == canonical(pristine)

print("Publication-stage headline projection checks passed")
