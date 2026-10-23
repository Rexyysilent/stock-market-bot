"""Strict input contracts for an isolated, single-currency research study.

An adjusted price is an economic return proxy, not a tradable share price.
Validation proves internal consistency, not provider accuracy, historic universe
completeness, source-use rights, or the truth of an operator's time assertions.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_JSON_BYTES = 64 * 1024 * 1024
MAX_SESSIONS = 30000
MAX_PRICE_CELLS = 250000
MAX_DECISIONS = 100000
HASH = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,31}\Z")
CALENDARS = {"XNYS": "America/New_York", "XNSE": "Asia/Kolkata"}
ASSET_KINDS = {"equity", "equity_etf", "non_equity_etf"}


def utc(value: str) -> datetime:
    if not isinstance(value, str) or not value or "T" not in value:
        raise ValueError("Timestamp must be an ISO instant with a timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Timestamp must be a valid ISO instant") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def session_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("Session must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid session date") from exc


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value):
    raise ValueError(f"Nonfinite JSON value is forbidden: {value}")


def load_json(path):
    with Path(path).open("rb") as handle:
        payload = handle.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("JSON file exceeds the 64 MiB research input limit")
    try:
        return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_pairs,
                          parse_constant=_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Invalid UTF-8 JSON input") from exc


def _object(value, label, required, optional=()):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} field names must be strings")
    missing = set(required) - set(value)
    unknown = set(value) - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f"{label} fields: missing={sorted(missing)}, unknown={sorted(unknown)}")


def _text(value, label, limit=4096):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label} must be a nonempty string of at most {limit} characters")


def _identifier(value, label):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label}")


def _number(value, label, lower=0, upper=None, *, positive=False):
    try:
        finite = not isinstance(value, bool) and isinstance(value, (float, int)) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite number")
    if value < lower or (positive and value <= lower) or (upper is not None and value > upper):
        raise ValueError(f"{label} is outside the supported range")


def _integer(value, label, lower, upper):
    if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
        raise ValueError(f"{label} must be an integer in [{lower}, {upper}]")


def _hash(value, label):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _weights(value, assets, label, max_weight=1.0, max_gross=1.0):
    if not isinstance(value, dict) or set(value) - set(assets):
        raise ValueError(f"{label} must map declared assets to weights")
    for symbol, weight in value.items():
        _number(weight, f"{label}.{symbol}", upper=max_weight)
    if math.fsum(value.values()) > max_gross + 1e-12:
        raise ValueError(f"{label} exceeds the long-only cash-funded gross limit")


def validate_study(study):
    required = (
        "schema_version", "study_id", "strategy_sha256", "currency", "calendar",
        "assets", "universe_basis", "universe_declared_at", "initial_cash",
        "benchmark_weights", "cost_model", "slippage_bps", "annual_fixed_cost",
        "benchmark_annual_fixed_cost", "execution", "limits", "evaluation", "contributions",
    )
    _object(study, "study", required, ("notes",))
    if study["schema_version"] != "portfolio-lab.study.v1":
        raise ValueError("Unsupported study schema")
    _identifier(study["study_id"], "study_id")
    _hash(study["strategy_sha256"], "strategy_sha256")
    if study["currency"] not in ("INR", "USD") or study["calendar"] not in CALENDARS:
        raise ValueError("v1 supports single-currency INR/USD studies with XNYS/XNSE calendars")
    assets = study["assets"]
    if not isinstance(assets, dict) or not 1 <= len(assets) <= 64:
        raise ValueError("A study must declare 1–64 assets, including its benchmark")
    for symbol, asset in assets.items():
        if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise ValueError("Invalid asset identifier")
        _object(asset, f"assets.{symbol}", ("kind",))
        if asset["kind"] not in ASSET_KINDS:
            raise ValueError("Unsupported asset kind; no derivatives, leverage or short sales")
    if study["universe_basis"] not in ("fixed_operator_selection", "point_in_time"):
        raise ValueError("Declare the universe-selection basis")
    utc(study["universe_declared_at"])
    _number(study["initial_cash"], "initial_cash", positive=True)
    _weights(study["benchmark_weights"], assets, "benchmark_weights")
    from .costs import validate_cost_model
    validate_cost_model(study["cost_model"], study["currency"])
    if study["cost_model"]["kind"] == "india_delivery" and study["calendar"] != "XNSE":
        raise ValueError("The India delivery tariff models NSE; use an XNSE study")
    for field in ("annual_fixed_cost", "benchmark_annual_fixed_cost"):
        _number(study[field], field)
    _number(study["slippage_bps"], "slippage_bps", upper=1000)
    execution = study["execution"]
    _object(execution, "execution", ("latency_seconds", "extra_delay_sessions",
                                     "max_decision_age_days", "min_trade_value"))
    _integer(execution["latency_seconds"], "latency_seconds", 0, 86400 * 30)
    _integer(execution["extra_delay_sessions"], "extra_delay_sessions", 0, 252)
    _number(execution["max_decision_age_days"], "max_decision_age_days", upper=366, positive=True)
    _number(execution["min_trade_value"], "min_trade_value")
    _object(study["limits"], "limits", ("max_weight", "max_gross"))
    for key in ("max_weight", "max_gross"):
        _number(study["limits"][key], key, upper=1, positive=True)
    evaluation = study["evaluation"]
    _object(evaluation, "evaluation", ("holdout_start", "declared_at", "minimum_holdout_sessions",
                                       "minimum_paper_decisions", "objective"))
    session_date(evaluation["holdout_start"])
    utc(evaluation["declared_at"])
    _integer(evaluation["minimum_holdout_sessions"], "minimum_holdout_sessions", 1, MAX_SESSIONS)
    _integer(evaluation["minimum_paper_decisions"], "minimum_paper_decisions", 1, MAX_DECISIONS)
    _text(evaluation["objective"], "objective")
    if not isinstance(study["contributions"], list) or len(study["contributions"]) > MAX_SESSIONS:
        raise ValueError("contributions must be a bounded list")
    contribution_dates = []
    for flow in study["contributions"]:
        _object(flow, "contribution", ("session", "amount"))
        session_date(flow["session"])
        _number(flow["amount"], "contribution amount", positive=True)
        contribution_dates.append(flow["session"])
    if contribution_dates != sorted(set(contribution_dates)):
        raise ValueError("Contribution sessions must be unique and chronological")
    if "notes" in study:
        _text(study["notes"], "notes", 16384)
    return study


def validate_dataset(dataset, study):
    validate_study(study)
    _object(dataset, "dataset", ("schema_version", "dataset_id", "synthetic", "currency", "calendar",
                                "price_basis", "adjustment_policy", "acquired_at", "source",
                                "expected_sessions", "sessions"), ("notes",))
    if dataset["schema_version"] != "portfolio-lab.dataset.v1":
        raise ValueError("Unsupported dataset schema")
    _identifier(dataset["dataset_id"], "dataset_id")
    if type(dataset["synthetic"]) is not bool:
        raise ValueError("synthetic must be an explicit boolean")
    if dataset["currency"] != study["currency"] or dataset["calendar"] != study["calendar"]:
        raise ValueError("Dataset currency/calendar does not match study; implicit FX is forbidden")
    if dataset["price_basis"] != "total_return_proxy":
        raise ValueError("v1 accepts explicitly labelled total_return_proxy data only, not executable share prices")
    _text(dataset["adjustment_policy"], "adjustment_policy")
    acquired_at = utc(dataset["acquired_at"])
    if not dataset["synthetic"] and acquired_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("A real historical dataset cannot have a future acquisition timestamp")
    _object(dataset["source"], "source", ("name", "url", "rights_note", "calendar_source"))
    for field, value in dataset["source"].items():
        _text(value, f"source.{field}")
    expected = dataset["expected_sessions"]
    if not isinstance(expected, list) or not 1 <= len(expected) <= MAX_SESSIONS:
        raise ValueError("Supply an independent expected-session schedule with 1–30000 sessions")
    if len(expected) * len(study["assets"]) > MAX_PRICE_CELLS:
        raise ValueError("Dataset exceeds the 250000 asset-session measurement limit")
    for value in expected:
        session_date(value)
    if expected != sorted(set(expected)):
        raise ValueError("Expected sessions must be unique and chronological")
    sessions = dataset["sessions"]
    if not isinstance(sessions, list) or len(sessions) != len(expected):
        raise ValueError("Price sessions must cover the full expected schedule; no gap filling")
    last_close = None
    try:
        zone = ZoneInfo(CALENDARS[study["calendar"]])
    except ZoneInfoNotFoundError as exc:
        raise ValueError("Exchange timezone data is missing; use the repository's locked Python environment") from exc
    for expected_date, row in zip(expected, sessions):
        _object(row, "session", ("session", "open_at", "close_at", "prices"))
        if row["session"] != expected_date:
            raise ValueError("Missing, duplicate, unexpected or out-of-order price session")
        opening, closing = utc(row["open_at"]), utc(row["close_at"])
        if not opening < closing or (last_close is not None and opening <= last_close):
            raise ValueError("Market session instants overlap or are reversed")
        if opening.astimezone(zone).date().isoformat() != expected_date or closing.astimezone(zone).date().isoformat() != expected_date:
            raise ValueError("Session date disagrees with exchange-local open/close timestamps")
        if closing > acquired_at:
            raise ValueError("An incomplete/future bar cannot predate dataset acquisition")
        last_close = closing
        prices = row["prices"]
        if not isinstance(prices, dict) or set(prices) != set(study["assets"]):
            raise ValueError("Every study and benchmark asset needs every expected session bar")
        for symbol, bar in prices.items():
            _object(bar, f"prices.{symbol}", ("open", "close"))
            _number(bar["open"], f"{symbol}.open", positive=True)
            _number(bar["close"], f"{symbol}.close", positive=True)
    if set(flow["session"] for flow in study["contributions"]) - set(expected):
        raise ValueError("A contribution falls outside the supplied expected sessions")
    if "notes" in dataset:
        _text(dataset["notes"], "notes", 16384)
    return dataset


def validate_decisions(decisions, study):
    validate_study(study)
    if not isinstance(decisions, list) or len(decisions) > MAX_DECISIONS:
        raise ValueError("decisions must be a bounded list")
    identities = set()
    last_time = None
    for decision in decisions:
        _object(decision, "decision", ("decision_id", "strategy_sha256", "decided_at", "information_cutoff",
                                       "weights", "origin", "evidence"), ("recorded_at",))
        _identifier(decision["decision_id"], "decision_id")
        if decision["decision_id"] in identities:
            raise ValueError("Duplicate decision ID")
        identities.add(decision["decision_id"])
        if decision["strategy_sha256"] != study["strategy_sha256"]:
            raise ValueError("Decision rule fingerprint differs from the frozen study")
        decided = utc(decision["decided_at"])
        cutoff = utc(decision["information_cutoff"])
        if cutoff > decided:
            raise ValueError("A decision cannot use information from its future")
        if decision["origin"] not in ("retrospective", "paper"):
            raise ValueError("Decision origin must be retrospective or paper")
        effective = decided
        if "recorded_at" in decision:
            recorded = utc(decision["recorded_at"])
            if recorded < decided:
                raise ValueError("A decision cannot be recorded before it was made")
            effective = max(effective, recorded)
        elif decision["origin"] == "paper":
            raise ValueError("Paper decisions require a local application recording time")
        if last_time is not None and effective < last_time:
            raise ValueError("Decisions must be supplied in chronological knowledge order")
        last_time = effective
        _weights(decision["weights"], study["assets"], "decision weights",
                 study["limits"]["max_weight"], study["limits"]["max_gross"])
        evidence = decision["evidence"]
        if not isinstance(evidence, list) or len(evidence) > 128:
            raise ValueError("Evidence must be a list with at most 128 archive references")
        seen = set()
        for ref in evidence:
            _object(ref, "evidence", ("archive_sha256", "generated_at", "known_at", "health_status"))
            _hash(ref["archive_sha256"], "archive_sha256")
            if ref["archive_sha256"] in seen:
                raise ValueError("Duplicate evidence archive")
            seen.add(ref["archive_sha256"])
            generated, known = utc(ref["generated_at"]), utc(ref["known_at"])
            if generated > known or known > cutoff:
                raise ValueError("Evidence cannot be known before generation or after the information cutoff")
            if ref["health_status"] not in ("OK", "WARN"):
                raise ValueError("ERROR or unclassified evidence is ineligible")
    return decisions


def load_decisions(path, study):
    value = load_json(path)
    _object(value, "decisions document", ("schema_version", "decisions"))
    if value["schema_version"] != "portfolio-lab.decisions.v1":
        raise ValueError("Unsupported decisions schema")
    return validate_decisions(value["decisions"], study)
