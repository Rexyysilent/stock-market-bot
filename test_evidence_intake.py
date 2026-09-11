"""Offline OMNI-02 intake, replay, rights, loss and non-interference acceptance."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

import agents.news_agent as news
from agents.news_agent import NewsAgent
from agents.news_providers import ProviderResult, BatchedGDELTNewsProvider, FMPNewsProvider
import evidence_intake as intake
from editorial_focus import select_focus
from config import SIGNAL_ELIGIBLE_TICKERS, EDITORIAL_COVERAGE_TICKERS
from scripts.validate_export_schema import DEFAULT_FIXTURE, DEFAULT_SCHEMA, load_json, validation_errors
from export_for_gemini import archive_brief, write_snapshot

NOW = datetime(2026, 9, 9, 12, tzinfo=timezone.utc)
TITLES = (
 ("TSLA", "Tesla reports earnings beat and raises guidance"),
 ("ROKU", "Roku announces acquisition of streaming platform"),
 ("PLTR", "Palantir wins major government contract"),
 ("SHOP", "Shopify reports record revenue growth"),
 ("VRT", "Vertiv raises full year outlook"),
 ("COIN", "Coinbase announces strategic partnership"),
)
def row(ticker, title):
    return dict(title=title, link="https://" + ticker.lower() + ".example/release",
                canonical_url="https://" + ticker.lower() + ".example/release",
                published="2026-09-09T11:00:00Z", source_time_kind="published",
                provider_seen_at="2026-09-09T11:05:00Z", provider=ticker+" source",
                publisher=ticker+" issuer", publisher_domain=ticker.lower()+".example",
                source_class="official", source_record_id="frozen:"+ticker,
                tickers=[ticker], ticker_metadata_kind="subject", summary=None)
ROWS = [row(*t) for t in TITLES]
class Provider:
    def __init__(self, rows, name="Official Feeds", metadata=None, intake_records=None):
        self.rows=rows; self.name=name; self.calls=0; self.metadata=metadata or {}
        self.intake_records=intake_records
    def fetch(self):
        self.calls+=1
        return ProviderResult(self.name,"ok",deepcopy(self.rows),metadata=deepcopy(self.metadata),
                              intake_records=deepcopy(self.intake_records))
class NoGoogle:
    def fetch(self):
        raise AssertionError("Top five filled: no additional acquisition allowed")
class Probe(NewsAgent):
    def _process_pool(self, raw, **kwargs):
        if self._intake_mode=="shadow":
            assert self._evidence_snapshot is not None
        return super()._process_pool(raw,**kwargs)
def run(mode, rows=ROWS, provider=None):
    provider=provider or Provider(rows)
    agent=Probe(now=NOW,providers=[provider],google_provider=NoGoogle(),evidence_intake_mode=mode)
    agent.get_global_headlines()
    assert provider.calls==1
    return agent

old_mode=news.EDITORIAL_COVERAGE_MODE
try:
    for coverage in ("off","shadow","active"):
        news.EDITORIAL_COVERAGE_MODE=coverage
        disabled=run("off")
        enabled=run("shadow")
        assert enabled.get_scored_headlines()==disabled.get_scored_headlines()
        assert enabled.get_dropped_headlines()==disabled.get_dropped_headlines()
        assert len(enabled.get_scored_headlines())==5
        for a in (disabled,enabled):
            a.focus=select_focus(a.get_scored_headlines(), SIGNAL_ELIGIBLE_TICKERS, NOW,
                                coverage_tickers=EDITORIAL_COVERAGE_TICKERS,coverage_mode=coverage)
        assert enabled.focus==disabled.focus
        manifest=enabled.get_pool_diagnostics()["evidence_intake"]
        assert "evidence_intake" not in disabled.get_pool_diagnostics()
        assert manifest["retained_count"]==6 and manifest["input_occurrence_count"]==6
        assert len(enabled.get_evidence_candidates())==6
        assert manifest["first_observed_at"] is None
        assert any(r["decision"]["stage"]=="cap_dropped" for r in manifest["records"])
        assert intake.validate_manifest(manifest)==[], intake.validate_manifest(manifest)
        assert intake.replay(manifest)["selection_matches"], coverage
        assert not manifest["google_requested"]
        assert all(r["review_status"]=="unreviewed" and not r["signal_eligible"] for r in manifest["records"])
        manifest["records"].clear()
        assert enabled.get_pool_diagnostics()["evidence_intake"]["retained_count"]==6
finally:
    news.EDITORIAL_COVERAGE_MODE=old_mode

# Body text is never stored, including HTML shells and truncated 200-like input.
special=deepcopy(ROWS)
special[0].update(summary="UNPERMITTED FULL TEXT",body="UNPERMITTED FULL BODY",
                  http_status=200,content_truncated=True)
special[1]["published"]="2026-09-09"
special[2]["published"]="not a timestamp"
special[3]["source_time_kind"]="provider_seen"
special[3]["published"]=None
agent=NewsAgent(now=NOW,providers=[Provider(special)],evidence_intake_mode="shadow")
agent._google_enabled=False
agent.get_global_headlines()
manifest=agent.get_pool_diagnostics()["evidence_intake"]
assert "UNPERMITTED" not in json.dumps(manifest)
assert manifest["records"][0]["content_availability"]=="truncated_or_blocked"
assert manifest["records"][1]["publication"]=={"published_at":None,"published_date":"2026-09-09","precision":"date"}
assert manifest["records"][2]["publication"]["published_at"] is None
assert manifest["records"][3]["publication"]["published_at"] is None
assert all(r["raw_storage_pointer"] is None and not any(r["uses"].values()) for r in manifest["records"])

# Same canonical release through different acquisition paths shares a candidate
# origin reference, not a claimed clinical event or independent corroboration.
copies=[deepcopy(ROWS[0]) for _ in range(3)]
for i,r in enumerate(copies):
    r.update(provider=("issuer","filing","wire")[i],source_record_id="copy:"+str(i))
captured=intake.capture([ProviderResult("Official Feeds","ok",copies)],NOW,"shadow")
assert len({r["origin_cluster_id"] for r in captured["records"]})==1
assert not any(r["origin_verified_as_event"] for r in captured["records"])
assert len({r["evidence_id"] for r in captured["records"]})==3

# Revisions preserve identity; observing the same metadata again is not a revision.
same=intake.capture([ProviderResult("Official Feeds","ok",[ROWS[0]])],NOW,"shadow")["records"][0]
changed=deepcopy(ROWS[0]); changed["title"]+=" (corrected)"
revision=intake.capture([ProviderResult("Official Feeds","ok",[changed])],NOW,"shadow")["records"][0]
assert same["evidence_id"]==revision["evidence_id"] and same["revision_id"]!=revision["revision_id"]
changed=deepcopy(ROWS[0]); changed["provider_seen_at"]="2026-09-09T12:00:00Z"
observed=intake.capture([ProviderResult("Official Feeds","ok",[changed])],NOW,"shadow")["records"][0]
assert same["revision_id"]==observed["revision_id"]

# Unknown-source metadata and budget overruns are counted, not silently stored.
unknown=run("shadow",provider=Provider(ROWS,name="Unapproved provider")).get_pool_diagnostics()["evidence_intake"]
assert unknown["omitted_reasons"]=={"source_policy_denied":6}
assert not unknown["records"] and unknown["replay_status"]=="incomplete"
try: intake.replay(unknown)
except ValueError: pass
else: raise AssertionError("Incomplete capture replayed as complete")
with patch.object(intake,"MAX_RECORDS",2):
    limited=run("shadow").get_pool_diagnostics()["evidence_intake"]
assert limited["retained_count"]==2 and limited["omitted_reasons"]=={"intake_budget_exceeded":4}

# Credentials in link query fields are not persisted; sanitization defeats exact replay.
unsafe=deepcopy(ROWS);unsafe[0]["link"]+="?api_key=NOT_A_REAL_CREDENTIAL"
agent=NewsAgent(now=NOW,providers=[Provider(unsafe)],evidence_intake_mode="shadow")
agent._google_enabled=False;agent.get_global_headlines()
redacted=agent.get_pool_diagnostics()["evidence_intake"]
assert "NOT_A_REAL_CREDENTIAL" not in json.dumps(redacted)
assert redacted["replay_status"]=="incomplete"

# Adapter duplicates remain accessible without changing the ranking corpus.
extra=deepcopy(ROWS)+[deepcopy(ROWS[0])]
d=run("shadow",provider=Provider(ROWS,intake_records=extra)).get_pool_diagnostics()["evidence_intake"]
assert d["retained_count"]==7 and d["loss_counts"]["adapter_duplicate"]==1
assert intake.replay(d)["selection_matches"]

# Real batched adapter: two queries, two raw occurrences, one ranking record.
class Response:
    status_code=200
    def json(self):
        return {"articles":[{"title":"Tesla reports earnings beat","url":"https://tesla.example/release",
                             "domain":"tesla.example","seendate":"20260909T110000Z"}]}
calls=[]
def request(*args,**kwargs):calls.append(kwargs["params"]);return Response()
gdelt=BatchedGDELTNewsProvider(("q1","q2"),now=NOW,limit=1,request_get=request,sleep_fn=lambda _:None)
result=gdelt.fetch()
assert len(calls)==2 and len(result.records)==1 and len(result.intake_records)==2
cap=intake.capture([result],NOW,"shadow")
assert "possible_result_cap_truncation" in cap["providers"][0]["gaps"]
assert cap["providers"][0]["reconciliation"]=="deferred_no_additional_requests"

# Equal request counts do not imply equal acquisition payloads.
class EmptyResponse:
    status_code=200
    def json(self):return []
scope_results=[]
scope_calls=[]
def empty_request(*args,**kwargs):
    scope_calls.append(kwargs["params"]);return EmptyResponse()
for symbols in (["TSLA"],["TSLA","MRNA"]):
    scope_results.append(FMPNewsProvider("fixture-key",now=NOW,tickers=symbols,
                                      request_get=empty_request).fetch())
assert len(scope_calls)==2 and scope_calls[0]["symbols"]!=scope_calls[1]["symbols"]
assert scope_results[0].metadata["request_scope_fingerprint"]!=scope_results[1].metadata["request_scope_fingerprint"]

# Existing FMP fallback counts and coverage remain explicit.
p=Provider(ROWS,name="FMP",metadata={"coverage":"fmp_articles","result_limit_reached":True,
                                  "result_limit":6,"malformed_record_count":2})
gap=run("shadow",provider=p).get_pool_diagnostics()["evidence_intake"]["providers"][0]
assert set(gap["gaps"])=={"provider_entitlement_gap","possible_result_cap_truncation","malformed_or_unusable_record"}

# Failure of the optional capture does not affect active selection.
baseline=run("off").get_scored_headlines()
with patch.object(intake,"capture",side_effect=RuntimeError("injected")):
    failed=run("shadow")
assert failed.get_scored_headlines()==baseline
failure=failed.get_pool_diagnostics()["evidence_intake"]
assert failure["status"]=="error" and intake.validate_manifest(failure)==[]

# Root export contract rejects hash corruption and permission laundering.
manifest=run("shadow").get_pool_diagnostics()["evidence_intake"]
fixture=load_json(DEFAULT_FIXTURE);schema=load_json(DEFAULT_SCHEMA)
fixture["data_quality"]["headline_pool"]["evidence_intake"]=manifest
assert validation_errors(fixture,schema)==[]
for modify in (
    lambda m:m.update(version="unknown"),
    lambda m:m["records"][0]["uses"].update(model_processing=True),
    lambda m:m["records"][0].update(body="forbidden"),
    lambda m:m["records"][0]["metadata"].update(title="tampered"),
    lambda m:m.update(signal_eligible=True),
):
    bad=deepcopy(manifest);modify(bad)
    # Recomputing a top hash must not authorize invalid permissions or record revisions.
    bad["manifest_hash"]=intake.digest({k:v for k,v in bad.items() if k!="manifest_hash"})
    fixture["data_quality"]["headline_pool"]["evidence_intake"]=bad
    assert validation_errors(fixture,schema)
fixture["data_quality"]["headline_pool"]["evidence_intake"]=manifest

# Arbitrary JSON-shaped corruption must fail closed without crashing callers.
for malformed in (
    None,
    [],
    {**manifest, "omitted_reasons": []},
    {**manifest, "providers": [None]},
    {**manifest, "records": [None]},
):
    errors = intake.validate_manifest(malformed)
    assert errors, malformed

with tempfile.TemporaryDirectory(prefix="omni02-copy-") as directory:
    root=Path(directory);current=root/"daily_brief.json"
    current.write_text(json.dumps(fixture,indent=2),encoding="utf-8")
    archive,mirror=archive_brief(str(current),"2026-09-09T12:00:00Z",
                               archive_dir=str(root/"archive"),mirror_dir=str(root/"mirror"))
    assert current.read_bytes()==Path(archive).read_bytes()==Path(mirror).read_bytes()
    cli=subprocess.run([sys.executable,"-I","-B",str(Path(__file__).resolve().parent/"scripts/replay_evidence_intake.py"),str(current)],
                       cwd=root,capture_output=True,text=True,timeout=30)
    assert cli.returncode==0,cli.stdout+cli.stderr
    assert json.loads(cli.stdout)["selection_matches"]
print("PASS OMNI-02 pre-cap retention, replay, rights, provenance, gaps, failures and archive identity")
