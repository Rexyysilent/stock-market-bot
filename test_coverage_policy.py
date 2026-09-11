"""OMNI-01 policy, identity, schema and acquisition acceptance tests."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import agents.news_agent as news
from agents.news_agent import NewsAgent
from agents.news_providers import FMPNewsProvider
import config
from coverage_policy import (
    SECURITY_CAPABILITIES, SOURCE_POLICIES, LEGACY_COVERAGE_TICKERS,
    coverage_policy_manifest, fmp_acquisition_tickers, fmp_coverage_contract,
    require_source_use, source_use_decision,
)
from scripts.validate_export_schema import (
    DEFAULT_FIXTURE, DEFAULT_SCHEMA, load_json, validation_errors,
)
from export_for_gemini import audit_signal_eligible_tickers

assert config.PIPELINE_VERSION == "2.6.3" and config.SCHEMA_VERSION == "2.8"
assert len(LEGACY_COVERAGE_TICKERS) == 41
assert tuple(LEGACY_COVERAGE_TICKERS) + ("MRK",) == config.EDITORIAL_COVERAGE_TICKERS
assert config.EDITORIAL_ONLY_TICKERS[-2:] == ("HOOD", "MRK")
assert len(SECURITY_CAPABILITIES) == 42
assert SECURITY_CAPABILITIES["MRK"]["issuer"]["cik"] == "0000310158"
assert SECURITY_CAPABILITIES["MRK"]["issuer"]["exchange"] == "NYSE"
assert SECURITY_CAPABILITIES["MRK"]["production_signal_cohort"] is False
assert not any(SECURITY_CAPABILITIES["MRK"]["acquisition_targets"].values())
assert SECURITY_CAPABILITIES["ITA"]["security_type"] == "exchange_traded_product"
assert SECURITY_CAPABILITIES["ITA"]["acquisition_targets"]["sec_cik_lookup"]
assert SECURITY_CAPABILITIES["ITA"]["availability"]["sec_registrant"] == "unknown"
assert SECURITY_CAPABILITIES["SI=F"]["availability"]["sec_registrant"] == "not_applicable"
assert SECURITY_CAPABILITIES["STTDF"]["availability"]["options"] == "unknown"
try:
    SECURITY_CAPABILITIES["MRK"]["issuer"]["cik"] = "bad"
except TypeError:
    pass
else:
    raise AssertionError("Registry must be deeply immutable")

for source in tuple(SOURCE_POLICIES) + ("unknown-provider",):
    for purpose in ("raw_storage", "model_processing", "commercial_redistribution",
                    "excerpt_redistribution", "unknown-purpose"):
        assert source_use_decision(source, purpose)["allowed"] is False
        try:
            require_source_use(source, purpose)
        except PermissionError:
            pass
        else:
            raise AssertionError((source, purpose))
assert require_source_use("fmp", "existing_local_metadata")["rights_status"] == "unknown"
assert not source_use_decision("unknown-provider", "existing_local_metadata")["allowed"]

now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
payloads = {}
queries = {}
previous_mode = news.EDITORIAL_COVERAGE_MODE
try:
    for mode in ("off", "shadow", "active"):
        news.EDITORIAL_COVERAGE_MODE = mode
        agent = NewsAgent(now=now)
        fmp = next(p for p in agent._providers if isinstance(p, FMPNewsProvider))
        payloads[mode] = tuple(fmp.tickers)
        queries[mode] = tuple(agent._build_gdelt_queries())
    assert payloads["off"] == payloads["shadow"] == fmp_acquisition_tickers("invalid")
    assert len(payloads["off"]) == 27 and len(payloads["active"]) == 40
    assert "MRK" not in payloads["shadow"] and "MRK" in payloads["active"]
    assert queries["off"] == queries["shadow"] == queries["active"]
    news.EDITORIAL_COVERAGE_MODE = "shadow"
    for title in (
        "Merck & Co. reports quarterly results",
        "Merck and Co announces clinical trial results",
        "Merck (NYSE: MRK) reports quarterly results",
        "MRK stock reports quarterly results",
        "Merck KGaA and Merck & Co. announce separate results",
    ):
        assert "MRK" in NewsAgent._matched_universe_tickers({"title": title, "tickers": []}), title
    for title in (
        "Merck reports quarterly results",
        "Merck KGaA reports quarterly results",
        "Merck KGaA (MRK) stock reports quarterly results",
        "Darmstadt Merck MRK stock reports quarterly results",
        "Merck (ETR: MRK) reports quarterly results",
        "Xetra MRK stock reports quarterly results",
    ):
        # Explicit foreign context also vetoes US-style provider subject tags.
        tickers = [] if title == "Merck reports quarterly results" else ["MRK"]
        assert "MRK" not in NewsAgent._matched_universe_tickers(
            {"title": title, "tickers": tickers, "ticker_metadata_kind": "subject"}), title
    assert NewsAgent._matched_universe_tickers({
        "title": "Unrelated issuer reports results", "tickers": ["MRK"],
        "ticker_metadata_kind": "related",
    }) == []
finally:
    news.EDITORIAL_COVERAGE_MODE = previous_mode

class Response:
    def __init__(self, status):
        self.status_code = status
    def json(self):
        return []
calls = []
def response(url, **kwargs):
    calls.append((url, kwargs["params"]))
    return Response(402 if len(calls) == 1 else 200)
p = FMPNewsProvider("fixture-key", tickers=payloads["shadow"], now=now, request_get=response)
result = p.fetch()
assert len(calls) == 2
assert result.metadata["coverage"] == "fmp_articles"
contract = result.metadata["coverage_contract"]
assert contract == fmp_coverage_contract("fmp_articles")
assert contract["narrower_than_requested"] and not contract["complete_market_coverage"]
assert not contract["model_processing_allowed"] and not contract["commercial_redistribution_allowed"]
assert fmp_coverage_contract("unrecognized")["source_scope"] == "unknown"

schema = load_json(DEFAULT_SCHEMA)
current = load_json(DEFAULT_FIXTURE)
assert validation_errors(current, schema) == []
legacy = load_json(DEFAULT_FIXTURE.with_name("daily_brief.current-2.7.json"))
assert validation_errors(legacy, schema) == []
assert len(legacy["universe"]["tickers"]) == 41 and "coverage_policy" not in legacy["universe"]
for mode in ("off", "shadow", "active"):
    manifest = coverage_policy_manifest(mode)
    assert len(manifest["fingerprint"]) == 64
    manifest["collector_targets"]["fmp_symbols"].append("INJECTED")
    assert "INJECTED" not in coverage_policy_manifest(mode)["collector_targets"]["fmp_symbols"]
for path, value in (
    (("schema_version",), "9.9"),
    (("universe", "coverage_policy", "cohort_version"), "unknown"),
    (("universe", "coverage_policy", "fingerprint"), "0" * 64),
    (("universe", "coverage_policy", "license_attestation"), True),
    (("universe", "coverage_policy", "cloud_model_budget_usd"), 1),
    (("universe", "coverage_policy", "collector_targets", "fmp_symbols"), ["MRK"]),
):
    bad = deepcopy(current)
    target = bad
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    assert validation_errors(bad, schema), path
bad = deepcopy(current)
bad["universe"]["instrumented_tickers"][0] = "OUTSIDE"
bad["universe"]["tickers"][0] = "OUTSIDE"
bad["universe"]["focus_eligible_tickers"][0] = "OUTSIDE"
for field, names in (("version", "tickers"), ("instrumented_version", "instrumented_tickers")):
    bad["universe"][field] = hashlib.sha256(",".join(bad["universe"][names]).encode()).hexdigest()[:8]
assert any("versioned policy" in e.message for e in validation_errors(bad, schema))

# Every new ticker and an arbitrary outsider is rejected at every explicit export sink.
templates = {
    "social_alerts": {}, "baseline_alerts": {}, "confluence": {},
    "insider_clusters": {}, "cash_runway_alerts": {},
    "social_attention": {"universe_member": True},
    "clinical_catalysts": {"fragile_alert": "FRAGILE_CATALYST"},
    "fda_catalysts": {"alert": "FDA_CATALYST_NEAR"},
    "ceo_ca_signals": {"signal_eligible": True},
}
for ticker in config.EDITORIAL_ONLY_TICKERS + ("OUTSIDE",):
    for section, fields in templates.items():
        doc = {"schema_version": "2.8", "pipeline_version": "2.6.3",
               "sections": {section: [{"ticker": ticker, **fields}]}}
        try:
            audit_signal_eligible_tickers(doc)
        except ValueError:
            pass
        else:
            raise AssertionError((ticker, section))
    doc = {"schema_version": "2.8", "pipeline_version": "2.6.3",
           "sections": {"options_flow": {ticker: {}}}}
    try:
        audit_signal_eligible_tickers(doc)
    except ValueError:
        pass
    else:
        raise AssertionError((ticker, "options_flow"))
print("PASS OMNI-01 cohorts, source-purpose denial, MRK identity, acquisition and sink contracts")

