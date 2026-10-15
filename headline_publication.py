"""Publication-stage headline projection for the daily brief.

This runs in the exporter after ``NewsAgent`` intake selection and before any
file is written. It withholds two kinds of already-selected rows:

* ``publication_repeat`` — a headline that an earlier day's archived brief
  inside the recency window already published;
* ``publication_unrelated_halt`` — a Nasdaq halt-feed record (explicit
  ``source_parser == "nasdaq_halts"``) for an instrument outside the
  configured universe.

It is a projection, not a second selector: dropped intake rows are never
promoted to refill a withheld slot, so underfill stays visible. Withheld rows
are returned to the exporter, which appends them to
``data_quality.headlines_dropped`` so ``fetched == published + dropped``
still holds. Earlier archives are read only; nothing is written and no
archive is rewritten. OMNI-02 intake replay is unaffected because its
selection signature is fixed inside the agent before this stage runs.

Repeat identity is a provider-scoped *document* identity. An article that
changes but keeps its ``source_record_id`` is therefore withheld for the rest
of the window; the receipt records ``matched_by: source_record_id`` so each
such case is visible. Preserving genuine updates would need an explicit
revision comparison and is deliberately not attempted here.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
import hashlib
import json
import os
import re

from agents.news_agent import NewsAgent
from timeutil import to_utc_z


VERSION = "headline-publication-1"
ARCHIVE_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{6})Z\.json$")
REASON_REPEAT = "publication_repeat"
REASON_UNRELATED_HALT = "publication_unrelated_halt"
HALT_PARSER = "nasdaq_halts"
_HISTORY_RECEIPT_KEYS = (
    "status", "window_utc_dates", "archives", "unreadable",
    "ignored_same_day", "ignored_future", "ignored_out_of_window",
)


def _clean(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _utc_date(now):
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).date()


def _title_key(row):
    """Normalised title identity, or None when the row has no usable title."""
    title = _clean(row.get("title"))
    if title is None:
        return None
    probe = {
        "title": title,
        "publisher": row.get("publisher") if isinstance(row.get("publisher"), str) else "",
        "publisher_domain": (
            row.get("publisher_domain")
            if isinstance(row.get("publisher_domain"), str) else ""
        ),
    }
    return NewsAgent._normalized_title(probe) or None


def _row_as_of(row):
    """Source time exactly as the export contract derives it, normalised."""
    provider_seen_time = row.get("source_time_kind") == "provider_seen"
    raw = row.get("as_of") or row.get("published") or (
        row.get("provider_seen_at") if provider_seen_time else None
    )
    return to_utc_z(raw) if isinstance(raw, str) else None


def load_publication_history(archive_dir, now, max_age_days):
    """Read-only identities from earlier-day archives inside the window.

    Eligible archives are canonical ``YYYY-MM-DD_HHMMSSZ.json`` files whose
    UTC date is strictly before today's UTC date and no older than
    ``max_age_days``. A file dated today (a rerun) or later (a clock problem)
    is ignored and counted. Before a file's rows are used, its embedded
    ``generated_at`` must agree with its name; otherwise it is listed under
    ``unreadable`` and contributes nothing. Rows are validated individually
    and invalid rows are skipped and counted.

    ``status`` is ``unavailable`` when the directory cannot be listed,
    ``partial`` when at least one eligible file could not be used, ``empty``
    when no eligible file exists, else ``ok``.
    """
    today = _utc_date(now)
    start = today - timedelta(days=int(max_age_days))
    history = {
        "version": VERSION,
        "status": "unavailable",
        "window_utc_dates": [
            start.isoformat(), (today - timedelta(days=1)).isoformat(),
        ],
        "archives": [],
        "unreadable": [],
        "ignored_same_day": 0,
        "ignored_future": 0,
        "ignored_out_of_window": 0,
        "_ids": {},
        "_titles": {},
    }
    try:
        names = sorted(os.listdir(archive_dir))
    except (OSError, TypeError):
        return history
    for name in names:
        match = ARCHIVE_NAME.match(name)
        if match is None:
            continue
        try:
            file_date = date.fromisoformat(match.group(1))
        except ValueError:
            history["unreadable"].append({"file": name, "problem": "malformed_name"})
            continue
        if file_date > today:
            history["ignored_future"] += 1
            continue
        if file_date == today:
            history["ignored_same_day"] += 1
            continue
        if file_date < start:
            history["ignored_out_of_window"] += 1
            continue
        try:
            with open(os.path.join(archive_dir, name), "rb") as handle:
                raw = handle.read()
            document = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            history["unreadable"].append({"file": name, "problem": "unreadable"})
            continue
        sha = hashlib.sha256(raw).hexdigest()
        stamp = match.group(2)
        expected = f"{match.group(1)}T{stamp[:2]}:{stamp[2:4]}:{stamp[4:]}Z"
        generated = (
            to_utc_z(document.get("generated_at"))
            if isinstance(document, dict)
            and isinstance(document.get("generated_at"), str)
            else None
        )
        if generated != expected:
            history["unreadable"].append({
                "file": name, "sha256": sha, "problem": "generated_at_mismatch",
            })
            continue
        sections = document.get("sections")
        rows = sections.get("headlines") if isinstance(sections, dict) else None
        if not isinstance(rows, list):
            history["unreadable"].append({
                "file": name, "sha256": sha, "problem": "headlines_not_a_list",
            })
            continue
        used = skipped = 0
        for row in rows:
            if not isinstance(row, dict):
                skipped += 1
                continue
            record_id = _clean(row.get("source_record_id"))
            as_of = _row_as_of(row)
            title_key = _title_key(row) if as_of else None
            if record_id is None and title_key is None:
                skipped += 1
                continue
            if record_id is not None:
                history["_ids"].setdefault(record_id, name)
            if title_key is not None:
                history["_titles"].setdefault((title_key, as_of), name)
            used += 1
        history["archives"].append({
            "file": name, "sha256": sha, "rows": len(rows),
            "used_rows": used, "skipped_rows": skipped,
        })
    if history["unreadable"]:
        history["status"] = "partial"
    elif history["archives"]:
        history["status"] = "ok"
    else:
        history["status"] = "empty"
    return history


def apply_publication_filter(selected_rows, history, target_count):
    """Project intake-selected rows to published rows.

    Returns ``(published, withheld, receipt)``. ``withheld`` rows carry
    ``drop_reason`` so the exporter can account for them as dropped
    candidates. Repeat suppression runs only when history is ``ok`` or
    ``partial``; the halt rule needs no history.
    """
    published, withheld, withheld_rows = [], [], []
    suppress_repeats = history.get("status") in ("ok", "partial")
    ids = history.get("_ids") or {}
    titles = history.get("_titles") or {}
    for row in selected_rows:
        reason = matched_by = matched_archive = None
        if (
            row.get("source_parser") == HALT_PARSER
            and not row.get("universe_tickers")
        ):
            reason, matched_by = REASON_UNRELATED_HALT, "source_parser"
        elif suppress_repeats:
            record_id = _clean(row.get("source_record_id"))
            if record_id is not None and record_id in ids:
                reason, matched_by = REASON_REPEAT, "source_record_id"
                matched_archive = ids[record_id]
            else:
                as_of = _row_as_of(row)
                title_key = _title_key(row) if as_of else None
                if title_key is not None and (title_key, as_of) in titles:
                    reason, matched_by = REASON_REPEAT, "title_as_of"
                    matched_archive = titles[(title_key, as_of)]
        if reason is None:
            published.append(row)
            continue
        withheld.append({**row, "drop_reason": reason})
        withheld_rows.append({
            "source_record_id": _clean(row.get("source_record_id")),
            "lane": row.get("lane"),
            "reason": reason,
            "matched_by": matched_by,
            "matched_archive": matched_archive,
        })
    receipt = {
        "version": VERSION,
        "history": {key: history.get(key) for key in _HISTORY_RECEIPT_KEYS},
        "target_count": int(target_count),
        "intake_selected_count": len(selected_rows),
        "published_count": len(published),
        "withheld": {
            REASON_REPEAT: sum(
                1 for item in withheld_rows if item["reason"] == REASON_REPEAT
            ),
            REASON_UNRELATED_HALT: sum(
                1 for item in withheld_rows
                if item["reason"] == REASON_UNRELATED_HALT
            ),
        },
        "underfill": len(published) < int(target_count),
        "withheld_rows": withheld_rows,
    }
    return published, withheld, receipt


def publication_warning(receipt):
    """One brief line when slots were withheld or history is not clean."""
    withheld = sum(receipt["withheld"].values())
    status = receipt["history"]["status"]
    if not withheld and status in ("ok", "empty"):
        return ""
    parts = []
    if withheld:
        parts.append(
            f"{withheld} of {receipt['target_count']} headline slot(s) withheld "
            f"at publication (repeats={receipt['withheld'][REASON_REPEAT]}, "
            f"unrelated_halts={receipt['withheld'][REASON_UNRELATED_HALT]}); "
            "withheld slots are not refilled"
        )
    if status not in ("ok", "empty"):
        window = receipt["history"].get("window_utc_dates") or ["?", "?"]
        parts.append(
            f"repeat history {status} for UTC dates {window[0]}..{window[1]}; "
            "repeat suppression may be incomplete"
        )
    return "- [publication] " + "; ".join(parts)
