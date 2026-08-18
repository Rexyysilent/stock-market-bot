"""Offline regression checks for the validated security boundaries."""

from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import sys
import tempfile
import types

# The transport test replaces the table parser, so keep it independent of the
# optional heavy pandas runtime used by production parsing.
if importlib.util.find_spec("pandas") is None:
    sys.modules["pandas"] = types.ModuleType("pandas")

if importlib.util.find_spec("bs4") is None:
    bs4_stub = types.ModuleType("bs4")
    bs4_stub.BeautifulSoup = object
    sys.modules["bs4"] = bs4_stub

from discord_guard import BoundedChatHistory, RequestGate
from openinsider_agent import OpenInsiderAgent
from scripts.run_secret_scan import (
    find_baseline_audit_failures,
    validate_baseline_audit_status,
)
from serve_dump import resolve_dashboard_path


ROOT = Path(__file__).resolve().parent


# Static dashboard assets stay inside dashboard/, including ordinary queries.
with tempfile.TemporaryDirectory(prefix="dashboard-path-test-") as temp_dir:
    dashboard_root = Path(temp_dir) / "dashboard"
    dashboard_root.mkdir()
    asset = dashboard_root / "app.js"
    asset.write_text("safe", encoding="utf-8")
    outside = dashboard_root.parent / "secret.txt"
    outside.write_text("secret", encoding="utf-8")

    assert resolve_dashboard_path(
        "/dashboard/app.js?cache=1", dashboard_root
    ) == asset.resolve()
    for attack_path in (
        "/dashboard/../secret.txt",
        "/dashboard/%2e%2e/secret.txt",
        "/dashboard/..%2fsecret.txt",
        "/dashboard/..\\secret.txt",
        "/dashboard/C:/Windows/win.ini",
        "/dashboard/c:/Windows/win.ini",
        "/dashboard/C%3A/Windows/win.ini",
        "/dashboard//Windows/win.ini",
    ):
        assert resolve_dashboard_path(attack_path, dashboard_root) is None, attack_path


# Cleartext or unexpected redirect targets must be rejected before parsing.

class Response:
    status_code = 200
    text = "trusted table"

    def __init__(self, url):
        self.url = url


class Session:
    def __init__(self, response_url):
        self.response_url = response_url

    def get(self, *_args, **_kwargs):
        return Response(self.response_url)


with tempfile.TemporaryDirectory(prefix="openinsider-security-test-") as temp_dir:
    cache_path = Path(temp_dir) / "openinsider-cache.json"
    agent = OpenInsiderAgent(
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
        session=Session("https://www.openinsider.com/screener"),
        cache_path=cache_path,
    )
    assert agent.base_url.startswith("https://")
    parse_called = []
    agent._parse_tables_with_pandas = lambda text: parse_called.append(text) or [
        {"filing_date": "2026-08-15 12:00:00"}
    ]
    assert agent.get_recent_trades(days_back=30, limit=1)
    assert parse_called == ["trusted table"]

    untrusted_agent = OpenInsiderAgent(
        now=datetime(2026, 8, 15, tzinfo=timezone.utc),
        session=Session("http://openinsider.com/screener"),
        cache_path=Path(temp_dir) / "untrusted-cache.json",
    )
    untrusted_agent._parse_tables_with_pandas = lambda _text: (_ for _ in ()).throw(
        AssertionError("untrusted response must not be parsed")
    )
    assert untrusted_agent.get_recent_trades(days_back=30, limit=1) == []

assert '"http://openinsider.com' not in (ROOT / "config.py").read_text(
    encoding="utf-8"
)


# Discord requests and retained history are bounded deterministically.
clock = [100.0]
gate = RequestGate(cooldown_seconds=30, max_users=2, clock=lambda: clock[0])
assert gate.allow(10) == (True, 0.0)
clock[0] = 110.0
allowed, retry_after = gate.allow(10)
assert allowed is False and 19.9 < retry_after <= 20.0
clock[0] = 131.0
assert gate.allow(10) == (True, 0.0)
assert gate.allow(11) == (True, 0.0)
assert gate.allow(12) == (True, 0.0)
assert gate.tracked_users == 2

history = BoundedChatHistory(max_channels=2, max_entries_per_channel=4)
for index in range(6):
    history.append(1, "user", str(index))
assert [row["content"] for row in history.recent(1, 10)] == ["2", "3", "4", "5"]
history.append(2, "user", "two")
history.append(3, "user", "three")
assert history.channel_count == 2
assert history.recent(1, 10) == []


# Every detect-secrets baseline finding needs an explicit false-positive audit.
assert validate_baseline_audit_status() == []
assert find_baseline_audit_failures(
    {"results": {"fixture.py": [{"line_number": 7, "is_secret": False}]}}
) == []
string_false = "false"
for unaudited_finding in (
    {"line_number": 7},
    {"line_number": 7, "is_secret": None},
    {"line_number": 7, "is_secret": string_false},
    {"line_number": 7, "is_secret": True},
):
    assert find_baseline_audit_failures(
        {"results": {"fixture.py": [unaudited_finding]}}
    )


# Publisher-controlled values are rendered with DOM text APIs, with CSP backup.
app_js = (ROOT / "dashboard" / "app.js").read_text(encoding="utf-8")
for dangerous_sink in (
    "item.innerHTML = `${sourceTag}${textFn(d)}`",
    "bizItem.innerHTML = `",
    "item.innerHTML = `\n            <span class=\"source-tag\">${f.ticker}",
    "item.innerHTML = `\n            <span class=\"source-tag\">@${tw.account}",
):
    assert dangerous_sink not in app_js, dangerous_sink

index_html = (ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")
assert "onclick=" not in index_html
server_source = (ROOT / "serve_dump.py").read_text(encoding="utf-8")
assert "Content-Security-Policy" in server_source

print("Security regression checks passed")
