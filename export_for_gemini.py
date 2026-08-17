"""
Market intelligence daily-brief exporter.
ThreadPoolExecutor parallelized pipeline. All 12 steps fire concurrently.

Output:
  - daily_brief.txt (human readable)
  - daily_brief.json (structured for LLM parsing)
"""
from collections import Counter
import sys
sys.path.insert(0, '.')

import hashlib
import io
import json
import math
import os
import re
import requests
import shutil
import time
import numpy as np
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from timeutil import build_run_context, to_utc_z, utc_now_z


class NumpySafeEncoder(json.JSONEncoder):
    """Handle numpy types that slip through from yfinance/pandas."""
    def default(self, obj):
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)

# Fix Windows console encoding (cp1252 can't handle emoji)
import sys
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from agents.news_agent import NewsAgent
from agents.social_agent import SocialAgent
from agents.watcher_agent import WatcherAgent
from agents.research_agent import ResearchAgent
from agents.twitter_agent import TwitterAgent
from agents.sec_agent import SECAgent
import signals
from stateutil import (
    atomic_text_writer,
    atomic_write_json,
    exclusive_file_lock,
    load_json_state,
    state_transaction,
)
from config import (
    ALL_TICKERS, WATCHLIST_STOCKS, WATCHLIST_VULTURE, FOCUS_TICKER,
    UNIVERSE_NAME, TICKER_ALIASES, APEWISDOM_LOW_VOLUME_MENTIONS,
    PIPELINE_VERSION, SCHEMA_VERSION, BRIEF_ARCHIVE_DIR,
    BRIEF_ARCHIVE_MIRROR_DIR,
)

# P2-8: entity linking for social whispers
CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")


def link_whisper_entities(whispers):
    """P2-8: anchor whispers to universe tickers. Returns (linked, unanchored).

    relevance: 1.0 = explicit cashtag, 0.8 = bare uppercase ticker symbol,
    0.6 = company-name alias. Universe whitelist prevents false hits on
    common acronyms (CEO, RSS, ...). Zero-ticker whispers are quarantined,
    not deleted.
    """
    universe = {t.upper() for t in ALL_TICKERS}
    linked, unanchored = [], []
    for w in whispers:
        text_lower = w.lower()
        tickers = set()
        relevance = 0.0
        for m in CASHTAG_RE.finditer(w):
            sym = m.group(1).upper()
            if sym in universe:
                tickers.add(sym)
                relevance = max(relevance, 1.0)
        for sym in universe:
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(sym)}(?![A-Za-z0-9])", w):
                tickers.add(sym)
                relevance = max(relevance, 0.8)
        for sym, aliases in TICKER_ALIASES.items():
            if sym in universe and any(alias in text_lower for alias in aliases):
                tickers.add(sym)
                relevance = max(relevance, 0.6)
        rec = {"text": w, "source": parse_source_from_string(w)}
        if tickers:
            rec["tickers"] = sorted(tickers)
            rec["relevance"] = relevance
            linked.append(rec)
        else:
            unanchored.append(rec)
    return linked, unanchored


# P2-10: baseline-relative signal scoring
BASELINE_STATE_FILE = os.path.join("state", "signal_baselines.json")
BASELINE_WINDOW = 20      # trailing runs kept per (ticker, metric)
BASELINE_MIN_POINTS = 15  # minimum prior readings before z-scores are emitted
BASELINE_MATURE_POINTS = 20
BASELINE_IMMATURE_Z_CAP = 8.0
BASELINE_Z_ALERT = 2.0
EXPORT_LOCK_FILE = os.path.join("state", "export.lock")


def update_baselines_and_score(options_flow, technicals, run_date,
                               pipeline_version=PIPELINE_VERSION):
    """P2-10: score put/call and volume ratios against each ticker's own
    trailing baseline instead of absolute thresholds.

    Keeps a rolling window of readings (one per run date) in
    state/signal_baselines.json. Returns (z_scores, baseline_alerts);
    z is null until a ticker has BASELINE_MIN_POINTS prior readings.
    """
    raw_state = load_json_state(BASELINE_STATE_FILE, {})

    # v2.5 starts a clean baseline era. Preserve the old flat file under a
    # legacy namespace so no historical measurements are deleted.
    if isinstance(raw_state, dict) and isinstance(raw_state.get("versions"), dict):
        state = raw_state
    else:
        state = {
            "schema_version": 2,
            "versions": {"legacy-unversioned": raw_state if isinstance(raw_state, dict) else {}},
        }
    version_state = state["versions"].setdefault(pipeline_version, {})

    z_scores = {}
    alerts = []

    def score_and_append(ticker, metric, value, observation_key,
                         as_of=None, observed_at=None, eligible=True):
        history = version_state.setdefault(ticker, {}).setdefault(metric, [])
        prior_rows = [
            row for row in history
            if (row.get("session") or row.get("date")) != observation_key
        ]
        prior = [row["value"] for row in prior_rows]
        if (not eligible or not observation_key
                or not isinstance(value, (int, float)) or value != value):
            return {
                "z": None,
                "sample_size": len(prior),
                "baseline_immature": len(prior) < BASELINE_MATURE_POINTS,
                "baseline_updated": False,
            }
        z = None
        if len(prior) >= BASELINE_MIN_POINTS:
            mean = sum(prior) / len(prior)
            std = (sum((v - mean) ** 2 for v in prior) / len(prior)) ** 0.5
            # floor the std at 5% of the mean so a flat history (std=0) still
            # yields a finite z instead of swallowing genuine deviations
            std = max(std, abs(mean) * 0.05)
            if std > 1e-9:
                z = (value - mean) / std
                if len(prior) < BASELINE_MATURE_POINTS:
                    z = max(-BASELINE_IMMATURE_Z_CAP,
                            min(BASELINE_IMMATURE_Z_CAP, z))
                z = round(z, 2)
        history[:] = prior_rows
        history.append({
            "session": observation_key,
            "as_of": to_utc_z(as_of),
            "observed_at": to_utc_z(observed_at),
            "value": value,
        })
        history[:] = history[-BASELINE_WINDOW:]
        return {
            "z": z,
            "sample_size": len(prior),
            "baseline_immature": len(prior) < BASELINE_MATURE_POINTS,
            "baseline_updated": True,
        }

    for ticker, flow in options_flow.items():
        value = flow.get("put_call_vol_ratio")
        score = score_and_append(
            ticker,
            "put_call_vol_ratio",
            value,
            flow.get("source_session") or run_date,
            as_of=flow.get("as_of"),
            observed_at=flow.get("observed_at"),
            eligible=flow.get("signal_eligible", True),
        )
        z = score["z"]
        z_scores.setdefault(ticker, {}).update({
            "put_call_vol_z": z,
            "put_call_vol_baseline_n": score["sample_size"],
            "put_call_vol_baseline_immature": score["baseline_immature"],
        })
        if z is not None and abs(z) >= BASELINE_Z_ALERT:
            alerts.append({
                "ticker": ticker,
                "metric": "put_call_vol_ratio",
                "value": value,
                "z_score": z,
                "signal": "BEARISH_VS_BASELINE" if z > 0 else "BULLISH_VS_BASELINE",
                "baseline_sample_size": score["sample_size"],
                "baseline_immature": score["baseline_immature"],
                "as_of": to_utc_z(flow.get("as_of")),
                "observed_at": to_utc_z(flow.get("observed_at")),
            })

    for ticker, tech in technicals.items():
        value = tech.get("volume_ratio")
        score = score_and_append(
            ticker,
            "volume_ratio",
            value,
            tech.get("source_session") or run_date,
            as_of=tech.get("as_of"),
            observed_at=tech.get("observed_at"),
            eligible=tech.get("session_complete", True),
        )
        z = score["z"]
        z_scores.setdefault(ticker, {}).update({
            "volume_ratio_z": z,
            "volume_ratio_baseline_n": score["sample_size"],
            "volume_ratio_baseline_immature": score["baseline_immature"],
        })
        if z is not None and abs(z) >= BASELINE_Z_ALERT:
            alerts.append({
                "ticker": ticker,
                "metric": "volume_ratio",
                "value": value,
                "z_score": z,
                "signal": "HIGH_VOLUME_SURPRISE" if z > 0 else "LOW_PARTICIPATION",
                "baseline_sample_size": score["sample_size"],
                "baseline_immature": score["baseline_immature"],
                "as_of": to_utc_z(tech.get("as_of")),
                "observed_at": to_utc_z(tech.get("observed_at")),
            })

    atomic_write_json(BASELINE_STATE_FILE, state, indent=1)
    return z_scores, alerts


def sanitize_nans(obj, path, nulled):
    """Replace NaN/inf floats with None anywhere in the JSON tree.

    Records the JSON path of every nulled field in `nulled` so the
    data_quality block can surface them instead of silently dropping data.
    """
    if isinstance(obj, dict):
        return {k: sanitize_nans(v, f"{path}.{k}", nulled) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize_nans(v, f"{path}[{i}]", nulled) for i, v in enumerate(obj)]
    if isinstance(obj, (float, np.floating)) and (math.isnan(obj) or math.isinf(obj)):
        nulled.append(path)
        return None
    return obj


def strip_render_fields(record, fields=("premium_fmt",)):
    """Drop presentation-only fields from a record before it enters the data layer."""
    return {k: v for k, v in record.items() if k not in fields}


ACTIVE_TRIAL_STATUSES = ("RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION")


# News items older than this are dropped from biotech_news: the Google News
# scan returns hits up to a year old, and stale headlines aren't news.
BIOTECH_NEWS_MAX_AGE_DAYS = 14


def _parse_rss_date(text):
    """RSS pubDate date part ('Thu, 02 Jul 2026') -> naive datetime, else None."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), '%a, %d %b %Y')
    except ValueError:
        return None


def build_clinical_catalysts(pdufa_catalysts, clinical_trials, now=None):
    """Split the raw PDUFA scan into clinical_catalysts, biotech_news, and the
    unfiltered registry view.

    The raw scan mixes ClinicalTrials.gov NCT records with undated Google News
    items (status "NEWS"). News items go to biotech_news with published mapped
    to as_of (date precision, same principle as filings' filed_at); items older
    than BIOTECH_NEWS_MAX_AGE_DAYS are dropped, newest sort first. NCT records
    are merged with the clinical_trials feed (deduped on nct_id), then filtered:
    keep only active statuses and drop past-dated trials. days_until stays null
    for trials with no parseable completion date. as_of for an NCT record is
    CT.gov's lastUpdatePostDate — when the registry entry last changed, not the
    trial's own completion date, which is a future schedule and not a source
    time. A stale registry record and a fresh one must not look identical.

    The third return value is the UNFILTERED merge (Sniper 3a): the registry
    diff must see a trial that flipped to TERMINATED even though the filtered
    catalyst list drops it. ticker comes from the shared sponsor map.
    """
    biotech_news = []
    merged = {}
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is not None:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)

    for cat in pdufa_catalysts:
        if cat.get('status') == 'NEWS' or not cat.get('nct_id'):
            published = cat.get('target_date') or None
            published_dt = _parse_rss_date(published)
            if published_dt and (now - published_dt).days > BIOTECH_NEWS_MAX_AGE_DAYS:
                continue
            news_rec = {
                'title': cat.get('title'),
                'link': cat.get('link'),
                'published': published,
                'is_priority': cat.get('is_priority', False),
                'source': cat.get('source'),
            }
            if published_dt:
                news_rec['as_of'] = published_dt.strftime('%Y-%m-%dT00:00:00Z')
            biotech_news.append(news_rec)
            continue
        merged[cat['nct_id']] = {
            'nct_id': cat['nct_id'],
            'title': cat.get('title'),
            'sponsor': cat.get('sponsor'),
            'ticker': cat.get('mapped_ticker') or ResearchAgent.resolve_ticker(
                cat.get('sponsor'), cat.get('title')),
            'status': cat.get('status'),
            'phase': cat.get('phase') if cat.get('phase') not in ('', 'N/A') else None,
            'target_date': cat.get('target_date') if cat.get('target_date') not in ('', 'TBD') else None,
            'days_until': cat.get('days_until'),
            'is_priority': cat.get('is_priority', False),
            'primary_completion_date': cat.get('primary_completion_date'),
            'enrollment': cat.get('enrollment'),
            'as_of': to_utc_z(cat.get('last_update_posted')),
            'source': 'ClinicalTrials.gov',
            'link': cat.get('link'),
        }

    for trial in clinical_trials:
        nct_id = trial.get('nct_id')
        if not nct_id or nct_id == 'N/A' or nct_id in merged:
            continue
        merged[nct_id] = {
            'nct_id': nct_id,
            'title': trial.get('title'),
            'sponsor': trial.get('sponsor'),
            'ticker': ResearchAgent.resolve_ticker(
                trial.get('sponsor'), trial.get('title')),
            'status': trial.get('status'),
            'phase': trial.get('phase') if trial.get('phase') not in ('', 'N/A') else None,
            'target_date': None,
            'days_until': None,
            'is_priority': False,
            'primary_completion_date': trial.get('primary_completion_date'),
            'enrollment': trial.get('enrollment'),
            'as_of': to_utc_z(trial.get('last_update_posted')),
            'source': 'ClinicalTrials.gov',
            'link': trial.get('link'),
        }

    catalysts = [
        rec for rec in merged.values()
        if rec['status'] in ACTIVE_TRIAL_STATUSES
        and (rec['days_until'] is None or rec['days_until'] >= 0)
    ]
    catalysts.sort(key=lambda r: (
        0 if r['is_priority'] else 1,
        r['days_until'] if r['days_until'] is not None else 9999,
    ))
    # Newest first so the TXT render's [:15] cut keeps the freshest items;
    # undated records ('' key) sort last.
    biotech_news.sort(key=lambda r: r.get('as_of') or '', reverse=True)
    return catalysts, biotech_news, list(merged.values())


def build_cash_runway_record(fin):
    """Pure-numeric cash runway record from a research_agent financials dict.

    Sign convention: burn_musd positive = cash consumed per quarter
    (negative = cash generated). runway_quarters is null when the company
    is cash-flow positive (see cash_flow_positive) or unknown.
    """
    def _musd(val):
        if val is None:
            return None
        return round(val / 1e6, 1)

    runway = fin.get("runway_quarters")
    cash_flow_positive = runway == 999
    burn = fin.get("quarterly_burn")
    return {
        "ticker": fin.get("ticker"),
        "risk_level": fin.get("risk_level"),
        "cash_musd": _musd(fin.get("cash_and_equivalents")),
        "burn_musd": _musd(-burn) if burn is not None else None,
        "debt_musd": _musd(fin.get("total_debt")),
        "runway_quarters": None if cash_flow_positive else runway,
        "cash_flow_positive": cash_flow_positive,
        "market_cap_musd": _musd(fin.get("market_cap")),
    }


# P3-13: key fields that identify a record within each section. Keep headline
# identity on its legacy rendered-text basis: the new provenance fields are
# additive and must not silently rewrite historical identities.
RECORD_ID_KEYS = {
    "headlines": ("text",),
    "prices": ("ticker",),
    "technicals": ("ticker",),
    "sec_filings": ("accession_number",),
    "clinical_catalysts": ("nct_id",),
    "biotech_news": ("link", "title"),
    "fda_catalysts": ("ticker", "event_type", "event_date"),
    "fda_advisory_meetings": ("date", "application", "link"),
    "registry_changes": ("nct_id", "change_type"),
    "confluence": ("ticker", "first_seen"),
    "cash_runway": ("ticker",),
    "cash_runway_alerts": ("ticker",),
    "insider_clusters": ("ticker", "period_days"),
    "earnings_calendar": ("ticker", "earnings_date"),
    "ceo_ca_signals": ("link", "title"),
    "social_whispers": ("text",),
    "social_whispers_unanchored": ("text",),
    "social_attention": ("ticker", "filter"),
    "social_alerts": ("ticker", "tag"),
    "twitter_signals": ("link",),
    "baseline_alerts": ("ticker", "metric", "signal"),
}


def _record_id(section, rec, keys):
    basis = section + "|" + "|".join(str(rec.get(k, "")) for k in keys)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def add_record_metadata(sections):
    """P3-13: stamp every list-section record with record_id and as_of.

    as_of = when the underlying data was true at its source, normalized to
    UTC Z (timeutil.to_utc_z). A record whose source exposes no usable time
    gets as_of null — run time is never substituted; that masquerade is what
    as_of exists to prevent. Returns the JSON paths of nulled records for
    data_quality.as_of_nulled (same philosophy as the NaN policy).
    """
    as_of_nulled = []

    def stamp(rec, path):
        if "observed_at" in rec:
            rec["observed_at"] = to_utc_z(rec.get("observed_at"))
        # source-time fallback chain; "date" = filing/publication date fields,
        # never event schedules (those live under their own names)
        raw = rec.get("as_of")
        if raw is None:
            raw = rec.get("filed_at") or rec.get("published") or rec.get("date")
        rec["as_of"] = to_utc_z(raw)
        if rec["as_of"] is None:
            as_of_nulled.append(path)

    for section, keys in RECORD_ID_KEYS.items():
        records = sections.get(section)
        if not isinstance(records, list):
            continue
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                continue
            rec["record_id"] = _record_id(section, rec, keys)
            stamp(rec, f"$.sections.{section}[{i}]")

    # Option anomaly records live inside options_flow[ticker].
    for ticker, flow in sections.get("options_flow", {}).items():
        for i, anomaly in enumerate(flow.get("option_contract_volume_oi_anomaly", [])):
            anomaly["record_id"] = _record_id(
                "option_contract_volume_oi_anomaly", anomaly,
                ("ticker", "strike", "type", "expiration")
            )
            stamp(anomaly, f"$.sections.options_flow.{ticker}."
                  f"option_contract_volume_oi_anomaly[{i}]")

    return as_of_nulled


def format_insider_direction(cluster):
    """' | direction=sell (buyers 0/sellers 3) | codes S:8 M:2 (edgar_fallback)'
    — empty when no codes were observed."""
    code_counts = cluster.get("code_counts") or {}
    if not code_counts:
        return ""
    codes = " ".join(f"{k}:{v}" for k, v in sorted(code_counts.items()))
    direction = cluster.get("cluster_direction") or "non-directional"
    buyers, sellers = cluster.get("buyers"), cluster.get("sellers")
    filers = ""
    if buyers is not None and sellers is not None:
        filers = f" (buyers {buyers}/sellers {sellers})"
    source = cluster.get("source") or "unknown"
    return f" | direction={direction}{filers} | codes {codes} ({source})"


def write_snapshot(source_path, run_dt):
    """Copy a verbatim point-in-time snapshot under briefs/YYYY-MM-DD/.

    The first run of a day owns daily_brief.json; later runs the same day get
    timestamped siblings so no snapshot is ever rewritten or backfilled.
    """
    snap_dir = os.path.join("briefs", run_dt.strftime("%Y-%m-%d"))
    os.makedirs(snap_dir, exist_ok=True)
    snap_path = os.path.join(snap_dir, "daily_brief.json")
    if os.path.exists(snap_path):
        snap_path = os.path.join(
            snap_dir, f"daily_brief_{run_dt.strftime('%H%M%S')}.json"
        )
    return _copy_verbatim_no_overwrite(source_path, snap_path)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_verbatim_no_overwrite(source_path, destination_path):
    """Copy once, verify SHA-256, and never replace an existing file."""
    os.makedirs(os.path.dirname(destination_path), exist_ok=True)
    if os.path.exists(destination_path):
        if _sha256_file(source_path) == _sha256_file(destination_path):
            return destination_path
        raise FileExistsError(f"archive collision: {destination_path}")
    try:
        with open(source_path, "rb") as source, open(destination_path, "xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        shutil.copystat(source_path, destination_path)
        if _sha256_file(source_path) != _sha256_file(destination_path):
            raise IOError(f"archive checksum mismatch: {destination_path}")
    except Exception:
        if os.path.exists(destination_path):
            os.remove(destination_path)
        raise
    return destination_path


def archive_brief(source_path, generated_at, archive_dir=BRIEF_ARCHIVE_DIR,
                  mirror_dir=BRIEF_ARCHIVE_MIRROR_DIR):
    """Archive an emitted brief verbatim to canonical and mirror locations."""
    stamp = to_utc_z(generated_at)
    if stamp is None:
        raise ValueError("generated_at must be a usable UTC timestamp")
    filename = datetime.fromisoformat(stamp.replace("Z", "+00:00")).strftime(
        "%Y-%m-%d_%H%M%SZ.json"
    )
    canonical = _copy_verbatim_no_overwrite(
        source_path, os.path.join(archive_dir, filename)
    )
    mirror = None
    if mirror_dir:
        mirror = _copy_verbatim_no_overwrite(
            canonical, os.path.join(mirror_dir, filename)
        )
    os.makedirs(os.path.join(os.path.dirname(archive_dir), "raw"), exist_ok=True)
    return canonical, mirror


# Extract source from strings like '[r/wallstreetbets] Post title (Score: 123)'
def parse_source_from_string(text):
    match = re.match(r'\[([^\]]+)\]', text)
    return match.group(1) if match else "unknown"


def _count_social_sources(whispers):
    counts = {}
    for item in whispers:
        source = parse_source_from_string(item)
        counts[source] = counts.get(source, 0) + 1
    return counts

def build_headline_export_records(headlines_scored):
    """Map selected provider records to the additive public JSON contract."""
    records = []
    for row in headlines_scored:
        provider_seen_at = row.get("provider_seen_at")
        provider_seen_time = row.get("source_time_kind") == "provider_seen"
        records.append({
            "text": row["text"],
            "title": row["title"],
            "link": row["link"],
            "canonical_url": row.get("canonical_url"),
            "published": row.get("published"),
            "as_of": row.get("published") or (
                provider_seen_at if provider_seen_time else None
            ),
            "observed_at": None if provider_seen_time else provider_seen_at,
            "source_time_kind": row.get("source_time_kind"),
            "relevance": row["relevance"],
            "lane": row.get("lane"),
            "universe_tickers": row.get("universe_tickers", []),
            "score_components": dict(row.get("score_components") or {}),
            "provider": row.get("provider"),
            "publisher": row.get("publisher"),
            "publisher_domain": row.get("publisher_domain"),
            "source_class": row.get("source_class"),
            "source_record_id": row.get("source_record_id"),
            "tickers": row.get("tickers", []),
            "summary": row.get("summary"),
            "duplicate_providers": row.get("duplicate_providers", []),
            "duplicate_publishers": row.get("duplicate_publishers", []),
            "source": row.get("provider"),
        })
    return records


def _copy_health(agent, attr_name="health"):
    if agent is None:
        return {}
    if hasattr(agent, "get_health"):
        return agent.get_health()
    return dict(getattr(agent, attr_name, {}) or {})


def _add_unique(items, value):
    if value and value not in items:
        items.append(value)


def collect_future_result(stage, future, default, failures):
    """Collect one top-level stage without letting it erase the whole brief."""
    try:
        return future.result()
    except Exception as exc:
        failure = {
            "stage": stage,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
        failures.append(failure)
        print(f"  [ERROR] {failure['stage']}: {failure['error']}")
        return default


def build_export_health(
    generated_at,
    elapsed,
    whispers,
    prices,
    technicals,
    twitter_data,
    sec_filings,
    options_flow,
    earnings_cal,
    ceo_signals,
    all_option_anomalies,
    news_agent,
    social_agent,
    twitter_agent,
    sec_agent,
    research_agent=None,
    pipeline_failures=None,
):
    warnings = []
    pipeline_failures = list(pipeline_failures or [])
    errors = [
        f"{failure['stage']}: {failure['error']}"
        for failure in pipeline_failures
    ]
    news_health = news_agent.get_pool_diagnostics()
    if news_health.get("fetch_error"):
        _add_unique(warnings, f"Headline fetch failed: {news_health['fetch_error']}")
    elif not news_health.get("fetched_count"):
        _add_unique(warnings, "Headline source returned an empty raw pool.")
    elif not news_health.get("fresh_before_relevance"):
        _add_unique(
            warnings,
            "Headline raw pool contained no rows inside the recency window.",
        )
    elif not news_health.get("fresh_relevant_count"):
        _add_unique(
            warnings,
            "Headline relevance scorer rejected every fresh raw-feed row.",
        )
    if news_health.get("fallback_used"):
        _add_unique(
            warnings,
            "Google News primary query returned no rows; targeted market-query "
            "fallback supplied the section.",
        )
    for provider in news_health.get("providers", []):
        if provider.get("status") == "error":
            _add_unique(
                warnings,
                f"Headline provider {provider.get('name')} failed: "
                f"{provider.get('error')}",
            )
        elif provider.get("partial_failures"):
            _add_unique(
                warnings,
                f"Headline provider {provider.get('name')} had "
                f"{provider.get('partial_failures')} partial fetch failure(s).",
            )
        if provider.get("name") == "FMP" and provider.get("fallback_used"):
            _add_unique(
                warnings,
                "FMP Stock News is unavailable for this account; using the "
                "narrower FMP Articles feed.",
            )
    if news_health.get("selected_count", 0) < NewsAgent.TARGET_COUNT:
        _add_unique(
            warnings,
            "Publisher-diverse headline section underfilled: "
            f"{news_health.get('selected_count', 0)}/{NewsAgent.TARGET_COUNT}.",
        )
    social_source_counts = _count_social_sources(whispers)
    social_agent_health = _copy_health(social_agent)
    reddit_failure_count = (
        len(social_agent_health.get("reddit_failures", []))
        + social_agent_health.get("reddit_failures_omitted", 0)
    )
    rss_failure_count = (
        len(social_agent_health.get("rss_failures", []))
        + social_agent_health.get("rss_failures_omitted", 0)
    )
    apewisdom_failure_count = (
        len(social_agent_health.get("apewisdom_failures", []))
        + social_agent_health.get("apewisdom_failures_omitted", 0)
    )
    if reddit_failure_count:
        _add_unique(
            warnings,
            f"Reddit social fetch had {reddit_failure_count} failures; whisper coverage may be degraded.",
        )
    if rss_failure_count:
        _add_unique(
            warnings,
            f"RSS/social feeds had {rss_failure_count} failures; external-feed coverage may be degraded.",
        )
    if apewisdom_failure_count:
        _add_unique(
            warnings,
            f"ApeWisdom had {apewisdom_failure_count} failures; retail ticker-heat coverage may be degraded.",
        )
    elif social_agent_health.get("apewisdom_enabled") and not social_agent_health.get("apewisdom_items", 0):
        _add_unique(warnings, "ApeWisdom returned zero ticker-heat rows.")

    openinsider_agent = getattr(social_agent, "openinsider", None)
    openinsider_health = _copy_health(openinsider_agent)
    openinsider_warning_items = [
        item for item in whispers
        if parse_source_from_string(item) == "OpenInsider/WARN"
    ]
    openinsider_trade_items = [
        item for item in whispers
        if parse_source_from_string(item) == "OpenInsider"
    ]
    if openinsider_health.get("error"):
        # One transport/parser failure can also produce a rendered WARN row and
        # an empty trade list. Report the root cause once; SEC fallback coverage
        # is reported separately below.
        _add_unique(warnings, f"OpenInsider error: {openinsider_health['error']}")
    else:
        if openinsider_health.get("warning") and not openinsider_warning_items:
            _add_unique(warnings, f"OpenInsider: {openinsider_health['warning']}")
        for item in openinsider_warning_items:
            _add_unique(warnings, item)
        if not openinsider_trade_items:
            _add_unique(
                warnings,
                "OpenInsider returned no trade rows in the social feed.",
            )

    sec_health = _copy_health(sec_agent)
    sec_failed_count = (
        len(sec_health.get("failed_requests", []))
        + sec_health.get("failed_requests_omitted", 0)
    )
    if sec_failed_count:
        _add_unique(warnings, f"SEC EDGAR had {sec_failed_count} failed calls after retries.")
    elif sec_health.get("retries", 0):
        _add_unique(warnings, f"SEC EDGAR needed {sec_health['retries']} retries but recovered.")
    if not sec_filings:
        _add_unique(warnings, "SEC EDGAR returned zero filings for the watchlist.")
    if sec_health.get("insider_cluster_fallback_used"):
        _add_unique(warnings, "Insider cluster scan used SEC EDGAR fallback after OpenInsider produced no clusters.")

    twitter_health = _copy_health(twitter_agent)
    twitter_used_google = any(
        item.get("account") == "GoogleNews" for item in twitter_data
    ) or twitter_health.get("fallback_used")
    if not twitter_data:
        _add_unique(warnings, "Twitter/X signals are empty; narrative velocity is degraded.")
    elif twitter_used_google:
        _add_unique(warnings, "Twitter/X used Google News fallback; treat as a slower narrative proxy.")
    else:
        twitter_expected = twitter_health.get("nitter_accounts_expected", 0)
        twitter_with_items = twitter_health.get(
            "nitter_accounts_with_items", 0
        )
        if twitter_expected and twitter_with_items < twitter_expected:
            failed_accounts = [
                f"@{row.get('account')} ({row.get('reason')})"
                for row in twitter_health.get("nitter_account_failures", [])
            ]
            failure_detail = (
                f": {', '.join(failed_accounts)}"
                if failed_accounts
                else ""
            )
            _add_unique(
                warnings,
                f"Twitter/Nitter covered {twitter_with_items}/"
                f"{twitter_expected} configured accounts{failure_detail}.",
            )

    expected_options_symbols = list(
        dict.fromkeys(WATCHLIST_STOCKS + WATCHLIST_VULTURE)
    )
    expected_options_tickers = len(expected_options_symbols)
    missing_options_tickers = sorted(
        set(expected_options_symbols) - set(options_flow)
    )
    if missing_options_tickers:
        _add_unique(
            warnings,
            f"Options flow unavailable for "
            f"{len(missing_options_tickers)}/{expected_options_tickers} "
            f"expected tickers: {', '.join(missing_options_tickers)}.",
        )
    if not earnings_cal:
        _add_unique(warnings, "Earnings calendar returned zero rows; yfinance calendar may be stale/unavailable.")

    ceo_health = (
        research_agent.get_ceo_ca_health()
        if research_agent is not None
        else {}
    )
    fallback_channels = ceo_health.get("fallback_channels", [])
    if fallback_channels:
        fallback_names = ", ".join(
            row.get("ticker", "?") for row in fallback_channels
        )
        _add_unique(
            warnings,
            f"CEO.ca API unavailable for "
            f"{len(fallback_channels)}/{len(ceo_health.get('expected_channels', []))} "
            f"channels ({fallback_names}); Google News proxy used.",
        )
    incomplete_direct = ceo_health.get("incomplete_direct_api_records", 0)
    if incomplete_direct:
        _add_unique(
            warnings,
            f"CEO.ca returned {incomplete_direct} direct-API record(s) without "
            "both a post ID and source timestamp.",
        )

    missing_prices = sorted([
        ticker for ticker, value in prices.items()
        if not value or value.get("price") is None
    ])
    if missing_prices:
        _add_unique(warnings, f"Missing price data for: {', '.join(missing_prices[:12])}")

    technical_errors = {
        ticker: data.get("error")
        for ticker, data in technicals.items()
        if data.get("error")
    }
    if technical_errors:
        detail = "; ".join(
            f"{ticker}: {error}"
            for ticker, error in sorted(technical_errors.items())
        )
        ticker_label = "ticker" if len(technical_errors) == 1 else "tickers"
        _add_unique(
            warnings,
            f"Technical indicators unavailable for "
            f"{len(technical_errors)} {ticker_label}: {detail}.",
        )

    if research_agent is not None and getattr(research_agent, "adcom_parse_failed", False):
        _add_unique(warnings, "adcom_calendar: parse_failed")
    adcom_detail_failures = (
        getattr(research_agent, "adcom_detail_failures", [])
        if research_agent is not None
        else []
    )
    if adcom_detail_failures:
        _add_unique(
            warnings,
            f"FDA AdCom detail enrichment failed for "
            f"{len(adcom_detail_failures)} meeting(s).",
        )

    status = "ERROR" if errors else "WARN" if warnings else "OK"
    # Content totals live in `summary`; health carries only status/warnings/errors/timing/sources.
    return {
        "status": status,
        "generated_at": generated_at,
        "pipeline_time_seconds": round(elapsed, 1),
        "warnings": warnings,
        "errors": errors,
        "sources": {
            "news": news_health,
            "social": social_source_counts,
            "social_agent": social_agent_health,
            "openinsider": {
                **openinsider_health,
                "social_trade_items": len(openinsider_trade_items),
                "warning_items": len(openinsider_warning_items),
            },
            "sec_edgar": {
                **sec_health,
                "watchlist_tickers": len(ALL_TICKERS),
                "filings_returned": len(sec_filings),
            },
            "twitter": twitter_health,
            "ceo_ca": ceo_health,
            "options_flow": {
                "expected_tickers": expected_options_tickers,
                "covered_tickers": len(options_flow),
                "missing_tickers": missing_options_tickers,
                "option_contract_volume_oi_anomaly": len(all_option_anomalies),
            },
            "earnings_calendar": {
                "rows": len(earnings_cal),
            },
            "pipeline_stages": {
                "failed": pipeline_failures,
            },
        },
    }


def _generate_daily_brief():
    print("=" * 60)
    print("  Market Intelligence Daily Brief")
    print("  ThreadPoolExecutor Parallel Pipeline")
    print("=" * 60)

    run_context = build_run_context()
    run_date = run_context.utc_date
    run_now = datetime.fromisoformat(
        run_context.started_at.replace("Z", "+00:00")
    )

    news_agent = NewsAgent(now=run_now)
    social_agent = SocialAgent(now=run_now)
    watcher_agent = WatcherAgent(run_context=run_context)
    research_agent = ResearchAgent(now=run_now)
    twitter_agent = TwitterAgent(now=run_now)
    sec_agent = SECAgent(now=run_now)

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 1: Fire ALL data fetches in parallel
    # ═══════════════════════════════════════════════════════════════════
    print("\n  [PHASE 1] Firing all agents in parallel...")
    start_time = time.monotonic()

    with ThreadPoolExecutor(max_workers=8) as pool:
        # Core intelligence
        fut_news       = pool.submit(news_agent.get_global_headlines)
        fut_social     = pool.submit(social_agent.get_whisper)
        fut_prices     = pool.submit(watcher_agent.get_full_report_data)
        fut_techs      = pool.submit(watcher_agent.get_all_technicals)
        fut_ceo        = pool.submit(research_agent.get_ceo_ca_signals)
        fut_trials     = pool.submit(research_agent.get_clinical_trials)
        fut_twitter    = pool.submit(twitter_agent.get_twitter_intel, 5)
        fut_sec        = pool.submit(sec_agent.scan_all_watchlist, 7)
        fut_pdufa      = pool.submit(research_agent.get_pdufa_with_financials)
        fut_insider    = pool.submit(sec_agent.detect_insider_clusters, 30)
        fut_options    = pool.submit(watcher_agent.get_all_options_flow)
        fut_earnings   = pool.submit(watcher_agent.get_earnings_calendar)
        fut_rotation   = pool.submit(watcher_agent.get_sector_rotation)
        fut_vix        = pool.submit(watcher_agent.get_vix_term_structure)
        fut_adcom      = pool.submit(research_agent.get_adcom_calendar)
        # Market structure measurements
        fut_spread     = pool.submit(watcher_agent.check_instrument_relative_return_spread)
        fut_contrarian = pool.submit(social_agent.get_retail_contrarian_index)

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 2: Collect results
    # ═══════════════════════════════════════════════════════════════════
    print("  [PHASE 2] Collecting results...")

    pipeline_failures = []

    def collect_stage(stage, future, default):
        return collect_future_result(
            stage, future, default, pipeline_failures
        )

    headlines = collect_stage("news", fut_news, [])
    headlines_scored = news_agent.get_scored_headlines()  # data layer
    headlines_dropped = news_agent.get_dropped_headlines()  # recency-gate drops
    headline_pool = news_agent.get_pool_diagnostics()
    whispers = collect_stage("social", fut_social, [])
    prices = collect_stage("prices", fut_prices, {})
    technicals = collect_stage("technicals", fut_techs, {})
    ceo_signals = collect_stage("ceo_ca", fut_ceo, [])
    ceo_signals_dropped = research_agent.get_dropped_ceo_signals()
    ceo_quality_dropped = research_agent.get_dropped_ceo_quality()
    clinical_trials = collect_stage("clinical_trials", fut_trials, [])
    twitter_data = collect_stage("twitter", fut_twitter, [])
    twitter_signals_dropped = twitter_agent.get_dropped_twitter_signals()
    sec_filings = collect_stage("sec_edgar", fut_sec, [])
    pdufa_data = collect_stage(
        "pdufa_financials",
        fut_pdufa,
        {"catalysts": [], "alerts": [], "financials": {}},
    )
    insider_clusters = collect_stage("insider_clusters", fut_insider, [])
    options_flow = collect_stage("options_flow", fut_options, {})
    earnings_cal = collect_stage("earnings_calendar", fut_earnings, [])
    sector_rotation = collect_stage("sector_rotation", fut_rotation, {})
    vix_term = collect_stage("vix_term_structure", fut_vix, {})
    adcom_meetings = collect_stage("fda_adcom_calendar", fut_adcom, [])
    instrument_spread = collect_stage(
        "instrument_relative_return_spread", fut_spread, {}
    )
    contrarian = collect_stage("retail_contrarian", fut_contrarian, {})

    elapsed = time.monotonic() - start_time
    print(f"\n  ⚡ Pipeline completed in {elapsed:.1f}s")

    # Collect option contract volume/OI anomalies across tickers.
    all_option_anomalies = []
    for ticker, data in options_flow.items():
        all_option_anomalies.extend(
            data.get("option_contract_volume_oi_anomaly", [])
        )

    # Split/merge the raw PDUFA scan and clinical trials feed (P0-2 / P1-5)
    clinical_catalysts, biotech_news, registry_view = build_clinical_catalysts(
        pdufa_data.get('catalysts', []),
        clinical_trials,
        now=run_now,
    )

    # Sniper 3a: day-over-day CT.gov diffs (observation time = event time
    # for a diff — the one legitimate use of run time; UTC Z like all as_of)
    registry_changes = signals.diff_registry(
        registry_view, run_context.started_at
    )

    # ApeWisdom rows live structured in social_attention; keep their rendered
    # strings out of the whisper data sections so the data has a single home.
    social_attention = social_agent.get_apewisdom_attention()

    # Sniper 1b/1c: burst ratios (mutates social_attention rows) + entrances
    social_alerts = signals.update_social_signals(
        social_attention,
        social_agent.get_apewisdom_top200(),
        run_date,
        mcap_lookup=lambda ticker: signals.get_mcap_musd(ticker, now=run_now),
        observed_at=run_context.started_at,
    )

    # Sniper 2a/2b: FDA catalysts keyword-mined from already-ingested primary
    # sources + AdCom calendar matches (no new text fetched, no LLM)
    fda_catalysts = signals.mine_fda_catalysts(
        sec_filings, biotech_news, ceo_signals,
        run_date,
        adcom_meetings=adcom_meetings,
    )

    # Sniper 3b: fragility join — a catalyst on a fragile small cap is
    # existential; the same event on big pharma is a footnote
    signals.attach_fragility(
        clinical_catalysts + fda_catalysts, pdufa_data.get('financials', {})
    )

    # Entity-link whispers to universe tickers (P2-8)
    linked_whispers, unanchored_whispers = link_whisper_entities(
        [w for w in whispers if not w.startswith("[ApeWisdom/")]
    )

    # Baseline-relative z-scores for P/C and volume ratios (P2-10)
    baseline_z, baseline_alerts = update_baselines_and_score(
        options_flow,
        technicals,
        run_context.latest_completed_session or run_date,
    )

    # Sniper 4: confluence — emitted only when >=2 distinct alert families
    # (social/options/insider/technical/catalyst) hit one ticker inside 48h.
    # Empty most days by design; thresholds are never loosened to populate it.
    confluence = signals.build_confluence(
        signals.collect_alert_events(
            social_alerts, baseline_alerts, options_flow, insider_clusters,
            technicals, fda_catalysts, clinical_catalysts, registry_changes,
        ),
        run_context.started_at,
        pipeline_version=PIPELINE_VERSION,
    )

    # Generate timestamp — observation time, UTC Z (as_of never uses this)
    iso_timestamp = utc_now_z()
    timestamp = iso_timestamp.replace("T", " ")
    health = build_export_health(
        iso_timestamp,
        elapsed,
        whispers,
        prices,
        technicals,
        twitter_data,
        sec_filings,
        options_flow,
        earnings_cal,
        ceo_signals,
        all_option_anomalies,
        news_agent,
        social_agent,
        twitter_agent,
        sec_agent,
        research_agent,
        pipeline_failures,
    )

    # ========== Write TXT file (human readable) ==========
    filename_txt = "daily_brief.txt"
    with atomic_text_writer(filename_txt) as f:
        f.write("=" * 70 + "\n")
        f.write(f"MARKET INTELLIGENCE DAILY BRIEF\n")
        f.write(f"Generated: {timestamp}\n")
        f.write(f"Pipeline Time: {elapsed:.1f}s (ThreadPoolExecutor)\n")
        f.write("=" * 70 + "\n\n")
        f.write("## EXPORT HEALTH\n")
        f.write("-" * 40 + "\n")
        f.write(f"- Status: {health['status']}\n")
        if health["warnings"]:
            for warning in health["warnings"][:10]:
                f.write(f"- WARN: {warning}\n")
        else:
            f.write("- No source health warnings\n")
        for error in health["errors"]:
            f.write(f"- ERROR: {error}\n")
        f.write("\n")

        # First section on purpose (Sniper Task 4): cross-source coincidence
        f.write("## CONFLUENCE (>=2 alert families on one ticker in 48h)\n")
        f.write("-" * 40 + "\n")
        if confluence:
            for rec in confluence:
                f.write(f"- {rec['ticker']} [score {rec['confluence_score']}] "
                        f"families: {', '.join(rec['families'])} "
                        f"(first seen {rec['first_seen']})\n")
                for alert in rec["alerts"]:
                    f.write(f"    {alert['family']}/{alert['tag']}: {alert['detail']}\n")
        else:
            f.write("- None today (sparse by design — an empty section is a feature)\n")
        f.write("\n")

        f.write("## HEADLINES (Official/FMP/Alpha Vantage/GDELT; Google fill)\n")
        f.write("-" * 40 + "\n")
        for h in headlines:
            f.write(f"- {h}\n")
        f.write("\n")

        f.write("## ASSET PRICES (Source: yfinance)\n")
        f.write("-" * 40 + "\n")
        for ticker, price in prices.items():
            f.write(f"- {ticker}: {watcher_agent.render_price(price) or 'N/A'}\n")
        f.write("\n")

        f.write("## TECHNICAL INDICATORS\n")
        f.write("-" * 40 + "\n")
        for ticker, tech in technicals.items():
            f.write(f"- {ticker}: RSI={tech.get('rsi', '?')} | Trend={tech.get('trend', '?')}")
            if tech.get('alerts'):
                f.write(f" | Notes: {', '.join(tech['alerts'])}")
            f.write("\n")
        f.write("\n")

        # === MARKET STRUCTURE MEASUREMENTS ===
        f.write("=" * 70 + "\n")
        f.write("## MARKET STRUCTURE MEASUREMENTS\n")
        f.write("=" * 70 + "\n\n")

        # Option contract volume/OI anomalies
        f.write("--- OPTION CONTRACT VOLUME/OI ANOMALIES (Vol/OI > 5x, DTE ≤ 5, Est. Notional > $500K) ---\n")
        if all_option_anomalies:
            for anomaly in all_option_anomalies:
                f.write(
                    f"- 🎯 {anomaly['ticker']} ${anomaly['strike']}{anomaly['type'][0]} "
                    f"DTE={anomaly['dte']} Vol/OI={anomaly['vol_oi_ratio']}x "
                    f"Est.Notional={anomaly['premium_fmt']} (Exp: {anomaly['expiration']})\n"
                )
        else:
            f.write("- No option contract volume/OI anomalies detected\n")
        f.write("\n")

        # Instrument relative-return spread
        f.write("--- INSTRUMENT RELATIVE-RETURN SPREAD ---\n")
        for pair in instrument_spread.get("pairs", []):
            status = "🚨 THRESHOLD CROSSED" if pair["threshold_crossed"] else "✅ NORMAL"
            f.write(
                f"- [{status}] {pair['commodity']}: "
                f"Physical ({pair['physical_ticker']}) {pair['physical_5d_chg']:+.1f}% vs "
                f"Paper ({pair['paper_ticker']}) {pair['paper_5d_chg']:+.1f}% | "
                f"Spread: {pair['spread']:+.1f}%\n"
            )
        if instrument_spread.get("alerts"):
            for alert in instrument_spread["alerts"]:
                f.write(f"  {alert}\n")
        f.write("\n")

        # Retail Contrarian Index
        f.write("--- RETAIL LANGUAGE INTENSITY ---\n")
        for sub in contrarian.get("subreddits", []):
            status = "ELEVATED" if sub["is_topped"] else "BASELINE"
            f.write(
                f"- [{status}] r/{sub['subreddit']}: "
                f"{sub['euphoria_ratio']:.0%} euphoria "
                f"({sub['euphoric_posts']}/{sub['total_posts']} posts)\n"
            )
        if contrarian.get("biz"):
            f.write(f"- /biz/ threads scanned: {len(contrarian['biz'])}\n")
        if contrarian.get("alerts"):
            for alert in contrarian["alerts"]:
                f.write(f"  {alert}\n")
        f.write("\n")

        # === END MARKET STRUCTURE MEASUREMENTS ===

        f.write("## SEC EDGAR FILINGS (Source: SEC EDGAR)\n")
        f.write("-" * 40 + "\n")
        for filing in sec_filings:
            f.write(f"- [{filing['ticker']}/{filing['form_type']}] {filing['description']}\n")
            f.write(f"  Filed: {filing['date']} | Link: {filing['link']}\n")
        f.write("\n")

        # Clinical Catalysts + Cash Runway (structured format matching reference)
        f.write("## CLINICAL CATALYSTS + CASH RUNWAY\n")
        f.write("Priority: CRSP, NTLA, RGNX | Window: 60 days\n")
        f.write("Risk: GREEN (>6Q) | YELLOW (4-6Q) | RED (<4Q cash runway)\n")
        f.write("-" * 40 + "\n")

        # Cash-runway flags first
        alerts = pdufa_data.get('alerts', [])
        if alerts:
            f.write("\n--- CASH RUNWAY FLAGS ---\n")
            for alert in alerts:
                if isinstance(alert, dict):
                    f.write(f"- {alert['message']}\n")
                else:
                    f.write(f"- {alert}\n")

        # Cash runway summary per ticker
        financials = pdufa_data.get('financials', {})
        if financials:
            f.write("\n--- CASH RUNWAY SUMMARY ---\n")
            for ticker, data in sorted(financials.items()):
                risk_tag = {"GREEN": "[OK]", "YELLOW": "[CAUTION]", "RED": "[DANGER]"}.get(data.get('risk_level', ''), "[?]")
                runway = f"{data['runway_quarters']}Q" if data.get('runway_quarters', 999) < 999 else "CF+"
                f.write(
                    f"- {risk_tag} {ticker}: Cash {data.get('cash_formatted', 'N/A')} | "
                    f"Burn {data.get('burn_formatted', 'N/A')}/Q | Runway {runway} | "
                    f"Debt {data.get('debt_formatted', 'N/A')} | MCap {data.get('mcap_formatted', 'N/A')}\n"
                )

        # Upcoming catalysts with details (filtered: active statuses, not past-dated)
        if clinical_catalysts:
            f.write("\n--- UPCOMING CATALYSTS ---\n")
            for cat in clinical_catalysts[:30]:  # Cap at 30
                priority_tag = " [WATCHLIST]" if cat.get('is_priority') else ""
                fragile_tag = " [FRAGILE]" if cat.get('fragile_alert') else ""
                days_str = f" ({cat['days_until']}d)" if cat.get('days_until') is not None else ""
                f.write(f"- [{cat.get('nct_id', '?')}] {cat.get('title', 'Unknown')}{priority_tag}{fragile_tag}{days_str}\n")
                if cat.get('sponsor'):
                    f.write(f"  Sponsor: {cat['sponsor']} | Status: {cat.get('status', '?')} | Phase: {cat.get('phase') or '?'}\n")
        else:
            f.write("- No upcoming clinical catalysts found\n")

        # Keyword-mined FDA regulatory events (Sniper 2a/2b)
        if adcom_meetings:
            f.write("\n--- FDA ADVISORY COMMITTEE MEETINGS (official) ---\n")
            for meeting in adcom_meetings[:20]:
                ticker = f" [{meeting['ticker']}]" if meeting.get("ticker") else ""
                application = (
                    f" {meeting['application']}"
                    if meeting.get("application")
                    else ""
                )
                subject = meeting.get("product") or meeting.get("title", "")
                f.write(
                    f"- {meeting.get('date', '?')}{ticker}{application}: "
                    f"{subject}\n"
                )

        if fda_catalysts:
            f.write("\n--- FDA CATALYSTS (keyword-mined) ---\n")
            for cat in fda_catalysts[:20]:
                alert_tag = f" [{cat['alert']}]" if cat.get('alert') else ""
                date_str = (f" {cat['event_date']} ({cat['days_until']}d)"
                            if cat.get('event_date') else " (no date stated)")
                f.write(f"- {cat['ticker']} {cat['event_type']}{date_str}{alert_tag}\n")
                f.write(f"  {cat.get('headline', '')[:150]} [{cat.get('source', '?')}]\n")

        # Day-over-day CT.gov registry diffs (Sniper 3a)
        if registry_changes:
            f.write("\n--- REGISTRY CHANGES (CT.gov day-over-day) ---\n")
            for ch in registry_changes[:20]:
                sev_tag = " [HIGH]" if ch.get('severity') == 'high' else ""
                tick = f" {ch['ticker']}" if ch.get('ticker') else ""
                extra = ""
                if ch.get('change_type') == 'DATE_SLIP':
                    extra = f" ({ch.get('delta_days'):+d}d)"
                elif ch.get('change_type') == 'ENROLLMENT_CHANGE':
                    extra = f" ({ch.get('delta_pct'):+.1f}%)"
                f.write(f"- [{ch['change_type']}]{sev_tag}{tick} {ch['nct_id']}: "
                        f"{ch.get('old')} -> {ch.get('new')}{extra}\n")

        # Undated FDA/biotech news items (routed out of the catalyst list)
        if biotech_news:
            f.write("\n--- BIOTECH NEWS (FDA/PDUFA headlines) ---\n")
            for item in biotech_news[:15]:
                priority_tag = " [WATCHLIST]" if item.get('is_priority') else ""
                f.write(f"- {item.get('title', 'Unknown')}{priority_tag}\n")
        f.write("\n")

        f.write("## CEO.CA / URANIUM INTELLIGENCE (Source: CEO.ca API; Google News fallback)\n")
        f.write("Tickers: UUUU, CCJ, NXE, DNN | Filter: ≥1% U3O8, >5m intercept\n")
        f.write("-" * 40 + "\n")
        for s in ceo_signals:
            provenance = (
                "DIRECT_API"
                if s.get("source_record_verified")
                else s.get("provenance_status", "UNKNOWN_PROVENANCE").upper()
            )
            classification = (
                "GEOLOGY_THRESHOLD"
                if s.get("threshold_qualified")
                else "CONTEXT_ONLY"
            )
            f.write(
                f"- [Source: {s['source']}] [{provenance}] "
                f"[{classification}] {s['title']}\n"
            )
            if s.get('grade_tag'):
                f.write(f"  GRADE: {s['grade_tag']}\n")
            f.write(f"  Link: {s['link']}\n")
        f.write("\n")

        # Cross-source and technical context
        f.write("## MARKET CONTEXT MEASUREMENTS\n")
        f.write("-" * 40 + "\n")

        # RSI Divergences
        f.write("\n--- RSI DIVERGENCES ---\n")
        rsi_divs = {t: d for t, d in technicals.items() if d.get('rsi_divergence')}
        if rsi_divs:
            for ticker, data in rsi_divs.items():
                f.write(f"- {ticker}: {data['rsi_divergence'].upper()} DIVERGENCE (RSI={data.get('rsi', '?')})\n")
        else:
            f.write("- No RSI divergences detected\n")

        # Insider Clusters (direction from Form 4 transaction codes)
        f.write("\n--- INSIDER CLUSTERS (direction from Form 4 transaction codes) ---\n")
        if insider_clusters:
            for cluster in insider_clusters:
                severity = cluster.get('alert_level', 'MEDIUM')
                f.write(f"- [{severity}] {cluster['ticker']}: {cluster['insider_count']} distinct filers ({cluster.get('filing_count', '?')} filings) in {cluster.get('period_days', 30)}d{format_insider_direction(cluster)}\n")
        else:
            f.write("- No insider clusters detected\n")

        # Options Flow
        f.write("\n--- OPTIONS ACTIVITY (PUT/CALL MEASUREMENTS) ---\n")
        for ticker, data in options_flow.items():
            if data.get('alerts') or data.get('put_call_vol_ratio'):
                f.write(f"- {ticker}: P/C Vol={data.get('put_call_vol_ratio', '?')} | P/C OI={data.get('put_call_oi_ratio', '?')} | Puts={data.get('total_put_volume', '?')} Calls={data.get('total_call_volume', '?')}\n")
                if data.get('alerts'):
                    for alert in data['alerts']:
                        f.write(f"  {alert}\n")

        # Sector Rotation
        f.write("\n--- SECTOR ROTATION ---\n")
        f.write(f"- Regime label: {sector_rotation.get('signal', 'N/A')}\n")
        f.write(f"- Growth 5d: {sector_rotation.get('growth_5d', 'N/A')} | Defensive 5d: {sector_rotation.get('defensive_5d', 'N/A')}\n")
        spread_5d = sector_rotation.get('spread_5d', 'N/A')
        if isinstance(spread_5d, (int, float)):
            spread_5d = f"{spread_5d:+.1f}%"
        f.write(f"- Spread (Def-Growth): {spread_5d}\n")
        if sector_rotation.get('growth_20d'):
            growth_20d = sector_rotation.get('growth_20d', 'N/A')
            def_20d = sector_rotation.get('defensive_20d', 'N/A')
            if isinstance(growth_20d, (int, float)):
                growth_20d = f"{growth_20d:+.1f}%"
            if isinstance(def_20d, (int, float)):
                def_20d = f"{def_20d:+.1f}%"
            f.write(f"- Growth 20d: {growth_20d} | Defensive 20d: {def_20d}\n")
            spread_20d = sector_rotation.get('spread_20d', 0)
            if isinstance(spread_20d, (int, float)):
                leader = "defensives" if spread_20d > 0 else "growth"
                f.write(f"  📊 20-day trend confirms: {leader} {'+' if spread_20d > 0 else ''}{round(spread_20d, 1)}% vs {'growth' if spread_20d > 0 else 'defensives'} (sustained {'risk-off' if spread_20d > 0 else 'risk-on'})\n")
        f.write("\n")

        # VIX Term Structure (regime flag)
        f.write("\n--- VIX TERM STRUCTURE (Regime) ---\n")
        if vix_term.get('vix_vix3m_ratio') is not None:
            emoji = "🚨" if vix_term['structure'] == "BACKWARDATION" else "🟢"
            f.write(
                f"- {emoji} VIX {vix_term['vix']} / VIX3M {vix_term['vix3m']} = "
                f"{vix_term['vix_vix3m_ratio']} ({vix_term['structure']} — {vix_term['regime'].replace('_', '-').lower()})\n"
            )
        else:
            f.write(f"- VIX term structure unavailable: {vix_term.get('error', 'no data')}\n")

        # Baseline-relative deviations
        f.write("\n--- BASELINE Z-SCORE DEVIATIONS (vs own 20-run history) ---\n")
        if baseline_alerts:
            for alert in baseline_alerts:
                f.write(
                    f"- {alert['ticker']}: {alert['metric']}={alert['value']} "
                    f"(z={alert['z_score']:+.1f}) — {alert['signal']}\n"
                )
        else:
            f.write("- No baseline deviations >= 2 sigma (or insufficient history yet)\n")
        f.write("\n")

        # Earnings Calendar
        if earnings_cal:
            f.write("## UPCOMING EARNINGS\n")
            f.write("-" * 40 + "\n")
            for earn in earnings_cal:
                timing = earn.get('timing') or '?'
                days = earn.get('days_until', '?')
                eps = earn.get('eps_estimate')
                eps_text = f" | EPS est: {eps}" if eps is not None else ""
                f.write(
                    f"- {earn.get('ticker', '?')}: {earn.get('earnings_date', '?')} "
                    f"({timing}, D+{days}){eps_text}\n"
                )
            f.write("\n")

        if social_alerts:
            f.write("## SOCIAL ATTENTION THRESHOLDS (Source: ApeWisdom vs own history)\n")
            f.write("-" * 40 + "\n")
            for alert in social_alerts:
                f.write(f"- [{alert['tag']}] {alert['ticker']}: {alert['detail']}\n")
            f.write("\n")

        f.write("## SOCIAL WHISPERS (Sources: Reddit, ApeWisdom, HackerNews, RSS Feeds)\n")
        f.write("-" * 40 + "\n")
        for w in whispers:
            f.write(f"- {w}\n")
        f.write("\n")

        f.write("## X/TWITTER NARRATIVE INDICATORS (Source: X/Twitter via Nitter/RSS)\n")
        f.write("-" * 40 + "\n")
        for tw in twitter_data:
            f.write(f"- [{tw.get('account', tw.get('query', 'X'))}] {tw['title']}\n")
            f.write(f"  Link: {tw['link']}\n")
        f.write("\n")

        # ===== FOCUS TICKER DEEP DIVE =====
        focus = FOCUS_TICKER.upper()
        f.write("=" * 70 + "\n")
        f.write(f"## {focus} DEEP DIVE\n")
        f.write("=" * 70 + "\n\n")

        focus_price = watcher_agent.render_price(prices.get(focus)) or "N/A"
        f.write(f"--- PRICE ---\n")
        f.write(f"- {focus}: {focus_price}\n\n")

        focus_tech = technicals.get(focus, {})
        f.write(f"--- TECHNICALS ---\n")
        if focus_tech:
            f.write(f"- RSI: {focus_tech.get('rsi', '?')}\n")
            f.write(f"- Trend: {focus_tech.get('trend', '?')}\n")
            f.write(f"- Volume Ratio: {focus_tech.get('volume_ratio', '?')}\n")
            if focus_tech.get('rsi_divergence'):
                f.write(f"- RSI Divergence: {focus_tech['rsi_divergence'].upper()}\n")
            if focus_tech.get('alerts'):
                f.write(f"- Notes: {', '.join(focus_tech['alerts'])}\n")
            if focus_tech.get('sma_50'):
                f.write(f"- SMA-50: {focus_tech.get('sma_50', '?')} | SMA-200: {focus_tech.get('sma_200', '?')}\n")
        else:
            f.write("- No technical data available\n")
        f.write("\n")

        focus_options = options_flow.get(focus, {})
        f.write(f"--- OPTIONS FLOW ---\n")
        if focus_options and (focus_options.get('put_call_vol_ratio') or focus_options.get('alerts')):
            f.write(f"- P/C Volume Ratio: {focus_options.get('put_call_vol_ratio', '?')}\n")
            f.write(f"- P/C OI Ratio: {focus_options.get('put_call_oi_ratio', '?')}\n")
            f.write(f"- Total Put Volume: {focus_options.get('total_put_volume', '?')}\n")
            f.write(f"- Total Call Volume: {focus_options.get('total_call_volume', '?')}\n")
            if focus_options.get('option_contract_volume_oi_anomaly'):
                f.write("- 🎯 OPTION CONTRACT VOLUME/OI ANOMALIES:\n")
                for anomaly in focus_options['option_contract_volume_oi_anomaly']:
                    f.write(f"  ${anomaly['strike']}{anomaly['type'][0]} DTE={anomaly['dte']} Vol/OI={anomaly['vol_oi_ratio']}x Est.Notional={anomaly['premium_fmt']}\n")
            if focus_options.get('alerts'):
                for alert in focus_options['alerts']:
                    f.write(f"  Note: {alert}\n")
        else:
            f.write("- No significant options flow data\n")
        f.write("\n")

        focus_filings = [fi for fi in sec_filings if fi.get('ticker', '').upper() == focus]
        f.write(f"--- SEC FILINGS ---\n")
        if focus_filings:
            for fi in focus_filings:
                f.write(f"- [{fi['form_type']}] {fi['description']}\n")
                f.write(f"  Filed: {fi['date']} | Link: {fi['link']}\n")
        else:
            f.write(f"- No recent {focus} filings\n")
        f.write("\n")

        focus_insiders = [c for c in insider_clusters if c.get('ticker', '').upper() == focus]
        f.write(f"--- INSIDER ACTIVITY ---\n")
        if focus_insiders:
            for c in focus_insiders:
                f.write(f"- [{c.get('alert_level', 'MEDIUM')}] {c['insider_count']} distinct filers ({c.get('filing_count', '?')} filings) in {c.get('period_days', 30)}d{format_insider_direction(c)}\n")
        else:
            f.write("- No insider clusters detected\n")
        f.write("\n")

        focus_earnings = [e for e in earnings_cal if e.get('ticker', '').upper() == focus]
        f.write(f"--- EARNINGS ---\n")
        if focus_earnings:
            for e in focus_earnings:
                timing = e.get('timing') or '?'
                days = e.get('days_until', '?')
                f.write(f"- Date: {e.get('earnings_date', '?')} ({timing}, D+{days})\n")
        else:
            f.write(f"- No upcoming {focus} earnings in window\n")
        f.write("\n")

        focus_whispers = [w for w in whispers if focus in w.upper()]
        f.write(f"--- SOCIAL MENTIONS ---\n")
        if focus_whispers:
            for w in focus_whispers:
                f.write(f"- {w}\n")
        else:
            f.write(f"- No {focus} mentions in social whispers\n")
        f.write("\n")

        focus_headlines = [h for h in headlines if focus in h.upper()]
        f.write(f"--- NEWS HEADLINES ---\n")
        if focus_headlines:
            for h in focus_headlines:
                f.write(f"- {h}\n")
        else:
            f.write(f"- No {focus}-specific headlines\n")
        f.write("\n")

        focus_twitter = [tw for tw in twitter_data if focus in tw.get('title', '').upper() or focus in tw.get('query', '').upper()]
        f.write(f"--- X/TWITTER ---\n")
        if focus_twitter:
            for tw in focus_twitter:
                f.write(f"- [{tw.get('account', tw.get('query', 'X'))}] {tw['title']}\n")
                f.write(f"  Link: {tw['link']}\n")
        else:
            f.write(f"- No {focus}-specific Twitter indicators\n")
        f.write("\n")

        f.write("=" * 70 + "\n")
        f.write("END OF BRIEF\n")
        f.write("=" * 70 + "\n")

    # ========== Write JSON file (structured for LLM) ==========
    filename_json = "daily_brief.json"

    financials_map = pdufa_data.get('financials', {})

    json_data = {
        "schema_version": SCHEMA_VERSION,
        "pipeline_version": PIPELINE_VERSION,
        "generated_at": iso_timestamp,
        "run_context": run_context.to_dict(),
        "pipeline_time_seconds": round(elapsed, 1),
        "universe": {
            "name": UNIVERSE_NAME,
            "version": hashlib.sha256(",".join(ALL_TICKERS).encode("utf-8")).hexdigest()[:8],
            "tickers": ALL_TICKERS,
            "focus_ticker": FOCUS_TICKER,
        },
        "conventions": {
            "units": "*_musd fields are millions of USD",
            "burn_musd": "positive = cash consumed per quarter; negative = cash generated",
            "runway_quarters": "null when company is cash-flow positive (see cash_flow_positive) or unknown",
            "nan_policy": "NaN/inf values are emitted as null and listed in data_quality.nan_fields_nulled",
            "record_id": "sha256[:16] of section + key fields; stable across days for the same underlying record",
            "timestamps": "every timestamp is UTC with an explicit Z suffix; naive source times (no offset) are truncated to date precision (T00:00:00Z) rather than guessing a timezone",
            "as_of": "when the data was true at its source — exchange bar time for prices, filing time for filings, publication time for news, registry last-update time for trial records; NEVER the pipeline's observation time; null when the source exposes no usable timestamp (paths listed in data_quality.as_of_nulled, same philosophy as the NaN policy)",
            "observed_at": "when this pipeline fetched a snapshot; it never substitutes for source as_of except for observation-native alerts such as a registry diff or social leaderboard transition",
            "derived_as_of": "derived records inherit source time: single-series computations carry the last observation used; multi-series aggregates (sector_rotation, instrument_relative_return_spread, regime) carry the OLDEST constituent timestamp; event sets carry the newest constituent timestamp",
            "universe.version": "sha256[:8] of the ticker list; changes whenever the watchlist changes",
            "relevance": "whisper ticker-anchor confidence: 1.0 cashtag, 0.8 bare symbol, 0.6 company-name alias",
            "z_scores": "vs the ticker's own pipeline-versioned trailing 20-run baseline; null until 15 prior runs, baseline_immature through 19, immature z capped at ±8",
            "option_contract_volume_oi_anomaly": "completed-session contract snapshot crossing volume/open-interest, DTE, and estimated-notional thresholds; lastPrice × session volume × 100 is an estimate, not classified premium flow, and CALL/PUT does not imply direction",
            "instrument_relative_return_spread": "five-session return difference between configured instrument pairs; threshold crossing only, with no causal claim",
            "mention_velocity_24h": "mentions minus mentions_24h_ago; null when either side is unknown",
            "rank_delta_24h": "rank_24h_ago minus rank; positive = climbed the leaderboard",
            "attention_score": "upvotes / max(mentions, 1) — engagement quality: viral concentration vs broad low-effort chatter; null for filters with no upvote mechanic (4chan)",
            "is_low_volume": f"mentions unknown or < {APEWISDOM_LOW_VOLUME_MENTIONS}; velocity/rank moves are noise at that size",
            "burst_ratio": "mentions today / trailing-20-run median of own mentions; null until 5 prior runs exist",
            "ATTENTION_BIRTH": "entered ApeWisdom top-200 after >=5 runs absent; small/mid caps only (mcap < $2000M)",
            "social_whispers": "narrative context for the human reader (HN/Substack/ZeroHedge/Reddit-narrative); no per-ticker signal fields; excluded from confluence",
            "fda_catalysts": "keyword-mined from primary sources (SEC filing metadata, biotech news, CEO.ca, FDA AdCom calendar); event_date null when no parseable date sits near the keyword; alert FDA_CATALYST_NEAR when 0-90 days out",
            "fda_advisory_meetings": "official FDA meeting rows enriched from each meeting agenda; sponsor/product/application are preserved and ticker is emitted only through a deterministic sponsor map",
            "registry_changes": "day-over-day CT.gov diffs (STATUS_FLIP / DATE_SLIP >14d / ENROLLMENT_CHANGE >10%); observation time = event time; empty on quiet days and on the seeding run",
            "fragility": "on catalyst records: runway_risk from cash_runway, market_cap_musd (financials -> weekly mcap cache -> null); fragility_flag = runway RED/YELLOW and mcap < $2000M; fragile_alert FRAGILE_CATALYST when flagged and event 0-90 days out",
            "confluence_score": "count of distinct source-timestamped alert families active in an exact trailing 48h UTC window; persistent stale conditions are de-duplicated and cannot refresh on rerun; records require >=2 families",
            "headlines.relevance": "compatibility score retained for existing consumers; editorial selection uses explicit universe/macro/discovery lanes and separate issuer, macro, vertical, authority, novelty, and impact components rather than treating the composite as ground truth",
            "headlines.lanes": "universe requires a configured ticker or alias; macro requires a true macro subject after stripping exchange listing metadata; discovery requires explicit high-impact evidence plus a covered vertical or official authority",
            "ceo_ca_signals": "source_record_verified means a direct CEO.ca API row carried both a post ID and source timestamp; threshold_qualified means the text crossed the deterministic geology threshold; neither field verifies the factual truth of a user post. Context-only domain-relevant rows may render, while irrelevant chatter is excluded into data_quality.ceo_ca_quality_dropped",
            "headlines.recency": f"hard cutoff, not a decay: headlines whose as_of is older than {NewsAgent.HEADLINES_MAX_AGE_DAYS} days are dropped before the top-5 selection, so a stale high-relevance item cannot outrank a fresh one; survivors keep the relevance sort. Headlines with NO as_of are dropped too — unprovable freshness fails a freshness gate. That is a deliberate exception to the brief-wide null-preserving convention and applies to this section only; everywhere else a missing source time is exported as null and listed in data_quality.as_of_nulled. Drops are listed in data_quality.headlines_dropped, so an empty section is distinguishable from a slow news day",
            "headlines.provenance": "provider is the acquisition path; publisher/publisher_domain identify the underlying newsroom; source_time_kind distinguishes published, event_time, and provider_seen; scoring is followed by freshness gating, syndication dedupe, and publisher-diverse selection",
            "insider_clusters": "a cluster is >=2 DISTINCT filers transacting the same open-market direction in the window; insider_count = distinct filers in the cluster direction; buyers/sellers = distinct filers with any P / any S; filing_count = Form 4 documents; code_counts are transaction codes as filed; cluster_direction = buy/sell by filer majority, mixed on tie; BUY clusters are HIGH at >=3 buyers and MEDIUM at 2, mixed is MEDIUM, SELL clusters are LOW context and excluded from confluence; filer identity = rptOwnerCik or insider name; filings whose XML could not be fetched count toward filing_count only",
        },
        "health": health,
        "summary": {
            "total_confluence": len(confluence),
            "total_headlines": len(headlines_scored),
            "headline_pool_fetched": headline_pool.get("fetched_count", 0),
            "headline_pool_fresh_before_relevance": headline_pool.get(
                "fresh_before_relevance", 0
            ),
            "headline_unique_publishers": headline_pool.get(
                "unique_selected_publishers", 0
            ),
            "headline_google_selected": headline_pool.get(
                "google_selected_count", 0
            ),
            "headline_google_fallback_used": headline_pool.get(
                "google_fallback_used", False
            ),
            "total_whispers": len(whispers),
            "total_whispers_linked": len(linked_whispers),
            "total_whispers_unanchored": len(unanchored_whispers),
            "total_social_attention": len(social_attention),
            "total_social_alerts": len(social_alerts),
            "total_baseline_alerts": len(baseline_alerts),
            "vix_regime": vix_term.get("regime"),
            "total_tickers": len(prices),
            "total_ceo_signals": len(ceo_signals),
            "total_ceo_direct_api_records_verified": sum(
                1 for signal in ceo_signals
                if signal.get("source_record_verified")
            ),
            "total_ceo_threshold_qualified": sum(
                1 for signal in ceo_signals
                if signal.get("threshold_qualified")
            ),
            "total_clinical_catalysts": len(clinical_catalysts),
            "total_biotech_news": len(biotech_news),
            "total_fda_catalysts": len(fda_catalysts),
            "total_fda_advisory_meetings": len(adcom_meetings),
            "total_registry_changes": len(registry_changes),
            "registry_trials_compared": len(registry_view),
            "total_twitter_signals": len(twitter_data),
            "total_sec_filings": len(sec_filings),
            "total_technical_alerts": sum(1 for t in technicals.values() if t.get("alerts")),
            "total_cash_runway_alerts": len(pdufa_data.get('alerts', [])),
            "total_insider_clusters": len(insider_clusters),
            "total_insider_buy_clusters": sum(
                1 for c in insider_clusters if c.get("cluster_direction") == "buy"
            ),
            "total_options_flow_alerts": len({t: d for t, d in options_flow.items() if d.get('alerts')}),
            "total_options_tickers": len(options_flow),
            "total_earnings_tracked": len(earnings_cal),
            "sector_rotation_signal": sector_rotation.get('signal', 'N/A'),
            "rsi_divergences": len({t: d for t, d in technicals.items() if d.get('rsi_divergence')}),
            # Market structure measurements
            "total_option_contract_volume_oi_anomaly": len(all_option_anomalies),
            "instrument_relative_return_spread_alerts": len(instrument_spread.get('alerts', [])),
            "contrarian_alerts": len(contrarian.get('alerts', [])),
        },
        "sections": {
            # First on purpose: the cross-source coincidence signal is the
            # highest-value row in the brief when it fires (Sniper Task 4)
            "confluence": confluence,
            "headlines": build_headline_export_records(headlines_scored),
            "prices": [
                {
                    "ticker": ticker,
                    "price": (data or {}).get("price"),
                    "change_pct": (data or {}).get("change_pct"),
                    "as_of": (data or {}).get("as_of"),
                    "observed_at": (data or {}).get("observed_at"),
                    "source_session": (data or {}).get("source_session"),
                    "session_complete": (data or {}).get("session_complete"),
                    "source": "yfinance",
                }
                for ticker, data in prices.items()
            ],
            "technicals": [
                {
                    "ticker": ticker,
                    "as_of": tech.get("as_of"),
                    "observed_at": tech.get("observed_at"),
                    "source_session": tech.get("source_session"),
                    "session_complete": tech.get("session_complete"),
                    "rsi": tech.get("rsi"),
                    "trend": tech.get("trend"),
                    "volume_ratio": tech.get("volume_ratio"),
                    "rsi_divergence": tech.get("rsi_divergence"),
                    "sma_50": tech.get("sma_50"),
                    "sma_200": tech.get("sma_200"),
                    "pct_from_52w_high": tech.get("pct_from_52w_high"),
                    "pct_from_52w_low": tech.get("pct_from_52w_low"),
                    "daily_change_pct": tech.get("daily_change_pct"),
                    "volume_ratio_z": baseline_z.get(ticker, {}).get("volume_ratio_z"),
                    "volume_ratio_baseline_n": baseline_z.get(ticker, {}).get("volume_ratio_baseline_n"),
                    "volume_ratio_baseline_immature": baseline_z.get(ticker, {}).get("volume_ratio_baseline_immature"),
                    "vulture": tech.get("vulture", False),
                    "alerts": tech.get("alerts", []),
                    "error": tech.get("error"),
                    "source": "yfinance/computed"
                }
                for ticker, tech in technicals.items()
            ],
            # === MARKET STRUCTURE MEASUREMENTS ===
            # Option anomalies live only inside each options_flow ticker record.
            "instrument_relative_return_spread": instrument_spread,
            "retail_contrarian": contrarian,
            # === END MARKET STRUCTURE MEASUREMENTS ===
            "sec_filings": [
                {
                    "ticker": f["ticker"],
                    "form_type": f["form_type"],
                    "description": f["description"],
                    "date": f["date"],
                    "filed_at": f.get("filed_at"),
                    "accession_number": f.get("accession_number"),
                    "primary_doc_url": f.get("primary_doc_url"),
                    "link": f["link"],
                    "source": "SEC EDGAR"
                } for f in sec_filings
            ],
            "clinical_catalysts": clinical_catalysts,
            "biotech_news": biotech_news,
            "fda_catalysts": fda_catalysts,
            "fda_advisory_meetings": adcom_meetings,
            "registry_changes": registry_changes,
            "cash_runway": [
                build_cash_runway_record(fin)
                for _, fin in sorted(financials_map.items())
            ],
            "cash_runway_alerts": [
                build_cash_runway_record(
                    financials_map.get(
                        a["ticker"],
                        {"ticker": a["ticker"], "risk_level": a.get("risk_level")},
                    )
                )
                for a in pdufa_data.get('alerts', [])
                if isinstance(a, dict)
            ],
            "insider_clusters": [
                {
                    "ticker": c["ticker"],
                    "as_of": c.get("as_of"),
                    "insider_count": c.get("insider_count", 0),
                    "buyers": c.get("buyers"),
                    "sellers": c.get("sellers"),
                    "filing_count": c.get("filing_count"),
                    "code_counts": c.get("code_counts") or {},
                    "cluster_direction": c.get("cluster_direction"),
                    "source": c.get("source"),
                    "period_days": c.get("period_days", 30),
                    "alert_level": c.get("alert_level", "MEDIUM")
                } for c in insider_clusters
            ],
            "options_flow": {
                ticker: {
                    "put_call_vol_ratio": d.get("put_call_vol_ratio"),
                    "put_call_vol_z": baseline_z.get(ticker, {}).get("put_call_vol_z"),
                    "put_call_vol_baseline_n": baseline_z.get(ticker, {}).get("put_call_vol_baseline_n"),
                    "put_call_vol_baseline_immature": baseline_z.get(ticker, {}).get("put_call_vol_baseline_immature"),
                    "put_call_oi_ratio": d.get("put_call_oi_ratio"),
                    "total_put_volume": d.get("total_put_volume"),
                    "total_call_volume": d.get("total_call_volume"),
                    "as_of": d.get("as_of"),
                    "observed_at": d.get("observed_at"),
                    "source_session": d.get("source_session"),
                    "signal_eligible": d.get("signal_eligible"),
                    "eligible_volume_contracts": d.get("eligible_volume_contracts"),
                    "excluded_stale_volume_contracts": d.get("excluded_stale_volume_contracts"),
                    "option_contract_volume_oi_anomaly": [
                        strip_render_fields(a)
                        for a in d.get("option_contract_volume_oi_anomaly", [])
                    ],
                    "alerts": d.get("alerts", [])
                }
                for ticker, d in options_flow.items()
            },
            "sector_rotation": sector_rotation,
            "regime": vix_term,
            "earnings_calendar": earnings_cal,
            "ceo_ca_signals": [
                {
                    "ticker": s.get('ticker'),
                    "title": s['title'],
                    "link": s['link'],
                    "source": s['source'],
                    "via": s.get('via'),
                    "author": s.get('author'),
                    "votes": s.get('votes'),
                    "source_record_id": s.get("source_record_id"),
                    "source_record_verified": s.get(
                        "source_record_verified", False
                    ),
                    "provenance_status": s.get("provenance_status"),
                    "threshold_qualified": s.get(
                        "threshold_qualified", s.get("verified", False)
                    ),
                    "content_class": s.get("content_class"),
                    # Compatibility alias for pre-2.6 consumers. This means
                    # threshold-qualified, not source/factual verification.
                    "verified": s.get('verified', False),
                    "grade_tag": s.get('grade_tag', ''),
                    "as_of": s.get('date'),
                }
                for s in ceo_signals
            ],
            "social_whispers": linked_whispers,
            "social_whispers_unanchored": unanchored_whispers,
            "social_attention": social_attention,
            "social_alerts": social_alerts,
            "baseline_alerts": baseline_alerts,
            "twitter_signals": [
                {
                    "account": tw.get('account', tw.get('query', 'X')),
                    "title": tw['title'],
                    "link": tw['link'],
                    "as_of": tw.get('date'),
                    "source": "X/Twitter"
                }
                for tw in twitter_data
            ],
            "deep_dive": {
                "ticker": FOCUS_TICKER,
                "note": "Numeric data for this ticker lives in the main sections keyed by ticker "
                        "(prices, technicals, options_flow, insider_clusters, earnings_calendar); "
                        "this section holds only filing references and ticker-filtered text.",
                "sec_filing_accessions": [
                    fi.get("accession_number") for fi in sec_filings
                    if fi.get("ticker", "").upper() == FOCUS_TICKER
                ],
                "has_insider_cluster": any(
                    c.get("ticker", "").upper() == FOCUS_TICKER for c in insider_clusters
                ),
                "social_mentions": [w for w in whispers if FOCUS_TICKER.lower() in w.lower()],
                "news_headlines": [h for h in headlines if FOCUS_TICKER.lower() in h.lower()],
                "twitter_signals": [
                    tw for tw in twitter_data
                    if FOCUS_TICKER.lower() in tw.get("title", "").lower()
                    or FOCUS_TICKER.lower() in tw.get("query", "").lower()
                ]
            }
        }
    }

    as_of_nulled = add_record_metadata(json_data["sections"])

    nan_fields_nulled = []
    json_data = sanitize_nans(json_data, "$", nan_fields_nulled)
    json_data["data_quality"] = {
        "nan_fields_nulled": nan_fields_nulled,
        "as_of_nulled": as_of_nulled,
        # Relevant-but-stale headlines the recency gate removed. Listed rather
        # than counted: an empty headlines section on a busy news day should say
        # why, and "the gate ate everything" reads very differently from "nothing
        # scored". Undated items land here too — see NewsAgent._drop_stale.
        "headlines_dropped": [
            {"title": h.get("title"), "as_of": h.get("as_of"),
             "relevance": h.get("relevance"), "reason": h.get("drop_reason"),
             "lane": h.get("lane"),
             "universe_tickers": h.get("universe_tickers", []),
             "score_components": h.get("score_components", {}),
             "provider": h.get("provider"), "publisher": h.get("publisher"),
             "publisher_domain": h.get("publisher_domain"),
             "source_record_id": h.get("source_record_id"),
             "kept_source_record_id": h.get("kept_source_record_id")}
            for h in headlines_dropped
        ],
        "headline_pool": headline_pool,
        "fda_adcom_detail_failures": list(
            getattr(research_agent, "adcom_detail_failures", [])
        ),
        "registry_diff": {
            "records_compared": len(registry_view),
            "changes_detected": len(registry_changes),
        },
        "ceo_ca_dropped": [
            {"ticker": row.get("ticker"), "title": row.get("title"),
             "as_of": row.get("as_of"), "reason": row.get("drop_reason")}
            for row in ceo_signals_dropped
        ],
        "ceo_ca_quality_dropped": [
            {
                "ticker": row.get("ticker"),
                "title": row.get("title"),
                "as_of": row.get("as_of"),
                "provenance_status": row.get("provenance_status"),
                "reason": row.get("drop_reason"),
            }
            for row in ceo_quality_dropped
        ],
        "twitter_dropped": [
            {"account": row.get("account"), "title": row.get("title"),
             "as_of": row.get("as_of"), "reason": row.get("drop_reason")}
            for row in twitter_signals_dropped
        ],
    }
    if nan_fields_nulled:
        print(f"  [data_quality] {len(nan_fields_nulled)} NaN/inf fields nulled in JSON export")
    if as_of_nulled:
        print(f"  [data_quality] {len(as_of_nulled)} records with no source timestamp (as_of null)")
    if headlines_dropped:
        headline_drop_counts = Counter(
            row.get("drop_reason") or "unknown" for row in headlines_dropped
        )
        headline_drop_detail = ", ".join(
            f"{reason}={headline_drop_counts[reason]}"
            for reason in sorted(headline_drop_counts)
        )
        print(
            f"  [data_quality] {len(headlines_dropped)} headline exclusions "
            f"after relevance: {headline_drop_detail}"
        )
    print(
        "  [data_quality] headline raw pool: "
        f"{headline_pool.get('fetched_count', 0)} fetched, "
        f"{headline_pool.get('fresh_before_relevance', 0)} fresh, "
        f"newest={headline_pool.get('newest_as_of')}"
    )

    atomic_write_json(
        filename_json, json_data, indent=2, encoder_cls=NumpySafeEncoder
    )

    snapshot_dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    archive_path, mirror_path = archive_brief(filename_json, iso_timestamp)
    snapshot_path = write_snapshot(filename_json, snapshot_dt)

    print(f"\nDone! Exported to:")
    print(f"  - {filename_txt} (human readable)")
    print(f"  - {filename_json} (structured JSON)")
    print(f"  - {snapshot_path} (point-in-time snapshot)")
    print(f"  - {archive_path} (canonical append-only archive)")
    if mirror_path:
        print(f"  - {mirror_path} (verified archive mirror)")
    print(f"\nStats:")
    print(f"  Pipeline time:          {elapsed:.1f}s")
    print(f"  Total whispers:         {len(whispers)}")
    print(f"  Total headlines:        {len(headlines)}")
    print(f"  Total tickers:          {len(prices)}")
    print(f"  Total CEO.ca indicators: {len(ceo_signals)}")
    print(f"  Clinical catalysts:     {len(clinical_catalysts)}")
    print(f"  Biotech news items:     {len(biotech_news)}")
    print(f"  Total Twitter indicators: {len(twitter_data)}")
    print(f"  Total SEC filings:      {len(sec_filings)}")
    print(f"  Technical thresholds:   {sum(1 for t in technicals.values() if t.get('alerts'))}")
    print(f"  Cash runway flags:      {len(pdufa_data.get('alerts', []))}")
    print(f"  Total insider clusters: {len(insider_clusters)} "
          f"({sum(1 for c in insider_clusters if c.get('cluster_direction') == 'buy' )} buy-direction)")
    print(f"  Total options flow:     {len({t: d for t, d in options_flow.items() if d.get('alerts')})}")
    print(f"  Total earnings tracked: {len(earnings_cal)}")
    print(f"  Sector rotation label:  {sector_rotation.get('signal', 'N/A')}")
    print(f"  RSI divergences:        {len({t: d for t, d in technicals.items() if d.get('rsi_divergence')})}")
    print(f"  === MARKET STRUCTURE MEASUREMENTS ===")
    print(f"  Option anomalies:       {len(all_option_anomalies)}")
    print(f"  Instrument spreads:     {len(instrument_spread.get('alerts', []))}")
    print(f"  Language thresholds:   {len(contrarian.get('alerts', []))}")
    print(f"  Export health:          {health['status']} ({len(health['warnings'])} warnings)")
    for warning in health["warnings"][:5]:
        print(f"    - {warning}")

    print("\n" + "="*60)
    print("[!] Note: X/Twitter RSS coverage may be incomplete; check export health.")
    print("")
    print("="*60)
    print("\n" + "="*60)
    print("[MOBILE] To access on phone: python serve_dump.py")
    print("   Then open the URL on your phone's browser")
    print("="*60 + "\n")

    if health["errors"]:
        raise RuntimeError(
            "brief was archived with ERROR health; signal state was not committed"
        )
    return filename_txt, filename_json


def _mutable_signal_state_paths():
    """Return state paths whose histories must commit with the brief."""
    return [
        BASELINE_STATE_FILE,
        signals.SOCIAL_HISTORY_FILE,
        signals.MCAP_CACHE_FILE,
        signals.CTGOV_SNAPSHOT_FILE,
        signals.ALERT_HISTORY_FILE,
        ResearchAgent.ADCOM_CACHE_FILE,
    ]


def generate_daily_brief():
    """Run one mutually-exclusive, transactional export."""
    with exclusive_file_lock(EXPORT_LOCK_FILE):
        with state_transaction(_mutable_signal_state_paths()):
            return _generate_daily_brief()


if __name__ == "__main__":
    generate_daily_brief()
