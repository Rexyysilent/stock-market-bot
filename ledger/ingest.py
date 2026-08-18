"""Deterministic archived-brief ingestion and legacy archive migration."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from config import BRIEF_ARCHIVE_MIRROR_DIR
from timeutil import to_utc_z

from .config import (
    ARCHIVE_DIR, BURST_BUCKETS, BURST_MIN, BURST_MIN_MENTIONS, HORIZONS,
    LEGACY_PIPELINE_VERSION, NOTIONAL_BUCKETS, Z_BUCKETS,
)
from .db import connect

logger = logging.getLogger("SignalLedger.Ingest")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_class(ticker):
    if ticker.startswith("^"):
        return "index"
    if ticker.endswith("=F"):
        return "future"
    from .config import FUND_TICKERS
    return "etf" if ticker in FUND_TICKERS else "equity"


def _bucket(value, cuts, labels):
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return None
    for cut, label in zip(cuts, labels):
        if number < cut:
            return label
    return labels[-1]


def _source_id(family, record, fallback):
    return str(record.get("record_id") or _digest(
        family + "|" + fallback + "|" + _canonical(record)
    )[:24])


def _signal_id(pipeline_version, source_record_id):
    return _digest(f"{pipeline_version}|{source_record_id}")[:32]


def _generated_at(data, path):
    raw = data.get("generated_at")
    text = str(raw or "")
    # New archives are explicit UTC. Legacy naive values recover their true UTC
    # observation time from the canonical filename created by import_legacy.
    if text.endswith("Z") or "+" in text[10:] or text[10:].count("-"):
        normalized = to_utc_z(raw)
        if normalized:
            return normalized
    try:
        parsed = datetime.strptime(Path(path).stem[:17], "%Y-%m-%d_%H%M%S")
        return parsed.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        normalized = to_utc_z(raw)
        if normalized:
            return normalized
    raise ValueError(f"brief has no usable generated_at: {path}")


def _iter_signals(data, run_id, pipeline_version):
    sections = data.get("sections")
    if not isinstance(sections, dict):
        raise TypeError("sections must be an object")

    def records(name):
        value = sections.get(name, [])
        if not isinstance(value, list):
            raise TypeError(f"sections.{name} must be a list")
        return [row for row in value if isinstance(row, dict)]

    # Each extractor is isolated so one malformed section cannot kill the run.
    extractors = []

    def insider():
        for row in records("insider_clusters"):
            if row.get("signal_eligible") is False:
                continue
            direction_name = str(row.get("cluster_direction") or "").lower()
            direction = "long" if direction_name == "buy" else "none"
            yield row, "insider_cluster", direction_name or None, direction, row.get("alert_level")
    extractors.append(("insider_clusters", insider))

    def confluence():
        for row in records("confluence"):
            families = sorted(str(v) for v in (row.get("families") or []))
            yield row, "confluence", "+".join(families) or None, "none", row.get("confluence_score")
    extractors.append(("confluence", confluence))

    def baselines():
        for row in records("baseline_alerts"):
            signal = str(row.get("signal") or "")
            metric = str(row.get("metric") or "")
            if "BULLISH" in signal:
                direction = "long"
            elif "BEARISH" in signal:
                direction = "short"
            else:
                direction = "none"
            strength = _bucket(row.get("z_score"), Z_BUCKETS,
                               ("2-3", "3-5", "5+"))
            yield row, "baseline_alert", "+".join(v for v in (signal, metric) if v), direction, strength
    extractors.append(("baseline_alerts", baselines))

    def options():
        flows = sections.get("options_flow", {})
        if not isinstance(flows, dict):
            raise TypeError("sections.options_flow must be an object")
        for ticker, flow in sorted(flows.items()):
            if not isinstance(flow, dict):
                continue
            contracts = flow.get("option_contract_volume_oi_anomaly")
            if contracts is None:  # legacy archive compatibility only
                contracts = flow.get("gamma_sweeps", [])
            contracts = [row for row in (contracts or []) if isinstance(row, dict)]
            if not contracts:
                continue
            def estimated_notional(row):
                return float(
                    row.get("notional_estimate")
                    or row.get("premium")
                    or 0
                )

            dominant = max(contracts, key=estimated_notional)
            total_notional = sum(estimated_notional(row) for row in contracts)
            payload = {
                "ticker": ticker,
                "contracts": contracts,
                "dominant_contract": dominant,
                "total_notional_estimate": total_notional,
                # Compatibility for already-written Tier-1 consumers. The
                # value is estimated notional, never classified paid premium.
                "total_premium": total_notional,
                "as_of": flow.get("as_of") or dominant.get("as_of"),
            }
            subtype = str(dominant.get("type") or "").upper()
            # CALL/PUT describes the contract, not aggressor side or intent.
            direction = "none"
            strength = _bucket(total_notional, NOTIONAL_BUCKETS,
                               ("<100k", "100k-1m", "1m+"))
            payload["record_id"] = _digest(
                f"option_contract_volume_oi_anomaly|{run_id}|{ticker}"
            )[:24]
            yield payload, "option_contract_volume_oi_anomaly", subtype or None, direction, strength
    extractors.append(("options_flow", options))

    def attention():
        explicit_attention = set()
        for row in records("social_alerts"):
            tag = str(row.get("tag") or "")
            if tag not in ("SOCIAL_BURST", "ATTENTION_BIRTH"):
                continue
            ticker = row.get("ticker")
            explicit_attention.add(ticker)
            strength = _bucket(row.get("burst_ratio"), BURST_BUCKETS,
                               ("3-5x", "5-10x", "10x+"))
            yield row, "attention", "ATTENTION_BIRTH" if tag == "ATTENTION_BIRTH" else "BURST", "none", strength
        for row in records("social_attention"):
            ticker = row.get("ticker")
            if (
                ticker in explicit_attention
                or row.get("universe_member") is not True
                or row.get("filter") != "all-stocks"
                or row.get("is_low_volume") is not False
            ):
                continue
            try:
                qualifies = (
                    float(row.get("burst_ratio")) >= BURST_MIN
                    and int(row.get("mentions") or 0) >= BURST_MIN_MENTIONS
                )
            except (TypeError, ValueError):
                qualifies = False
            if qualifies:
                strength = _bucket(row.get("burst_ratio"), BURST_BUCKETS,
                                   ("3-5x", "5-10x", "10x+"))
                yield row, "attention", "BURST", "none", strength
    extractors.append(("attention", attention))

    def catalysts():
        for row in records("fda_catalysts"):
            if row.get("alert"):
                risk = (row.get("fragility") or {}).get("runway_risk")
                yield row, "catalyst", str(row.get("alert")), "none", risk
        for row in records("clinical_catalysts"):
            if row.get("fragile_alert"):
                risk = (row.get("fragility") or {}).get("runway_risk")
                yield row, "catalyst", str(row.get("fragile_alert")), "none", risk
    extractors.append(("catalysts", catalysts))

    for section_name, extractor in extractors:
        try:
            for row, family, subtype, direction, strength in extractor():
                ticker = str(row.get("ticker") or "").upper()
                if not ticker:
                    logger.warning("%s row missing ticker; skipped", section_name)
                    continue
                source_id = _source_id(family, row, f"{run_id}|{ticker}|{subtype}")
                yield {
                    "record_id": _signal_id(pipeline_version, source_id),
                    "source_record_id": source_id,
                    "family": family,
                    "subtype": subtype,
                    "ticker": ticker,
                    "direction": direction,
                    "strength": None if strength is None else str(strength),
                    "as_of": to_utc_z(row.get("as_of")),
                    "asset_class": _asset_class(ticker),
                    "payload": _canonical(row),
                }
        except Exception as exc:
            logger.warning("%s extraction failed: %s", section_name, exc)


def ingest_file(path, conn=None):
    path = Path(path)
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    generated_at = _generated_at(data, path)
    pipeline_version = str(data.get("pipeline_version") or LEGACY_PIPELINE_VERSION)
    run_id = path.stem
    sections = data.get("sections") if isinstance(data.get("sections"), dict) else {}
    regime_section = sections.get("regime") if isinstance(sections.get("regime"), dict) else {}
    regime = regime_section.get("regime")
    sha = hashlib.sha256(raw).hexdigest()
    owns_connection = conn is None
    conn = conn or connect()
    try:
        existing = conn.execute(
            "SELECT brief_sha256 FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing and existing["brief_sha256"] != sha:
            raise ValueError(f"archive mutation detected for run {run_id}")
        conn.execute(
            "INSERT OR IGNORE INTO runs(run_id,generated_at,regime,brief_sha256,pipeline_version) VALUES(?,?,?,?,?)",
            (run_id, generated_at, regime, sha, pipeline_version),
        )
        for signal in _iter_signals(data, run_id, pipeline_version):
            inserted = conn.execute(
                """INSERT OR IGNORE INTO signals(
                     record_id,source_record_id,family,subtype,ticker,direction,strength,
                     regime_at_emission,first_seen_run,last_seen_run,as_of,asset_class,payload
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (signal["record_id"], signal["source_record_id"], signal["family"],
                 signal["subtype"], signal["ticker"], signal["direction"],
                 signal["strength"], regime, run_id, run_id, signal["as_of"],
                 signal["asset_class"], signal["payload"]),
            ).rowcount
            if inserted:
                conn.executemany(
                    "INSERT INTO outcomes(record_id,horizon,status) VALUES(?,?,'pending')",
                    ((signal["record_id"], horizon) for horizon in HORIZONS),
                )
            else:
                conn.execute(
                    """UPDATE signals SET last_seen_run=?
                       WHERE record_id=? AND last_seen_run<>?""",
                    (run_id, signal["record_id"], run_id),
                )
        conn.commit()
    finally:
        if owns_connection:
            conn.close()
    return run_id


def ingest_archives(archive_dir=ARCHIVE_DIR, db_path=None):
    conn = connect(db_path) if db_path else connect()
    count = 0
    try:
        for path in sorted(Path(archive_dir).glob("*.json")):
            ingest_file(path, conn)
            count += 1
    finally:
        conn.close()
    return count


def _copy_once(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if _file_sha(source) != _file_sha(destination):
            raise FileExistsError(f"archive collision: {destination}")
        return False
    with open(source, "rb") as src, open(destination, "xb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    shutil.copystat(source, destination)
    if _file_sha(source) != _file_sha(destination):
        destination.unlink(missing_ok=True)
        raise IOError(f"archive checksum mismatch: {destination}")
    return True


def import_legacy(source_dir="briefs", archive_dir=ARCHIVE_DIR,
                  mirror_dir=BRIEF_ARCHIVE_MIRROR_DIR):
    """Copy legacy snapshots verbatim, recovering naive run time from mtime."""
    imported = 0
    for source in sorted(Path(source_dir).glob("**/daily_brief*.json")):
        data = json.loads(source.read_text(encoding="utf-8"))
        raw_stamp = str(data.get("generated_at") or "")
        explicit = (raw_stamp.endswith("Z") or "+" in raw_stamp[10:]
                    or bool(raw_stamp[10:].count("-")))
        if explicit:
            stamp = datetime.fromisoformat(to_utc_z(raw_stamp).replace("Z", "+00:00"))
        else:
            stamp = datetime.fromtimestamp(source.stat().st_mtime, tz=timezone.utc)
        filename = stamp.strftime("%Y-%m-%d_%H%M%SZ.json")
        canonical = Path(archive_dir) / filename
        created = _copy_once(source, canonical)
        if mirror_dir:
            _copy_once(canonical, Path(mirror_dir) / filename)
        imported += int(created)
    (Path(archive_dir).parent / "raw").mkdir(parents=True, exist_ok=True)
    return imported
