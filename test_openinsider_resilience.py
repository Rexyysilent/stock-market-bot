"""Deterministic source-layer checks for OpenInsider run acquisition."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock

import requests

from openinsider_agent import OpenInsiderAgent


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
ROWS = [
    {
        "ticker": "ROKU",
        "filing_date": "2026-08-18 10:00:00",
        "trade_type": "P - Purchase",
        "insider_name": "Fresh Buyer",
        "value": "$100,000",
        "source": "OpenInsider",
        "link": "https://openinsider.com/screener",
    },
    {
        "ticker": "PLTR",
        "filing_date": "2026-07-01 10:00:00",
        "trade_type": "S - Sale",
        "insider_name": "Old Seller",
        "value": "$50,000",
        "source": "OpenInsider",
        "link": "https://openinsider.com/screener",
    },
    {
        "ticker": "FCX",
        "filing_date": "2026-08-20 10:00:00",
        "trade_type": "P - Purchase",
        "insider_name": "Future Row",
        "value": "$25,000",
        "source": "OpenInsider",
        "link": "https://openinsider.com/screener",
    },
    {
        "ticker": "LMT",
        "filing_date": "",
        "trade_type": "P - Purchase",
        "insider_name": "Undated Row",
        "value": "$10,000",
        "source": "OpenInsider",
        "link": "https://openinsider.com/screener",
    },
]


class Response:
    def __init__(
        self,
        status_code=200,
        text="fixture",
        url="https://openinsider.com/screener",
    ):
        self.status_code = status_code
        self.text = text
        self.url = url


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.closed = False
        self._lock = Lock()

    def get(self, url, **kwargs):
        with self._lock:
            index = len(self.calls)
            self.calls.append((url, deepcopy(kwargs)))
            outcome = self.outcomes[min(index, len(self.outcomes) - 1)]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


class BlockingSession(FakeSession):
    def __init__(self, outcome):
        super().__init__([outcome])
        self.started = Event()
        self.release = Event()

    def get(self, url, **kwargs):
        with self._lock:
            self.calls.append((url, deepcopy(kwargs)))
        self.started.set()
        assert self.release.wait(timeout=5)
        return self.outcomes[0]


def make_agent(cache_path, session, **kwargs):
    agent = OpenInsiderAgent(
        now=NOW,
        session=session,
        cache_path=cache_path,
        min_request_interval=kwargs.pop("min_request_interval", 0),
        sleep=kwargs.pop("sleep", lambda _seconds: None),
        clock=kwargs.pop("clock", lambda: 100.0),
        jitter=kwargs.pop("jitter", lambda _base: 0.0),
        **kwargs,
    )
    agent._parse_tables_with_pandas = lambda _html: deepcopy(ROWS)
    agent._parse_tables_with_bs4 = lambda _html: []
    return agent


def test_single_flight_and_local_views(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    cache_path = temp_dir / "single-flight.json"
    session = BlockingSession(Response())
    agent = make_agent(cache_path, session)

    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [
            pool.submit(agent.acquire_run_trades),
            pool.submit(
                agent.get_run_trades,
                days_back=30,
                limit=500,
                allow_stale=False,
            ),
            pool.submit(
                agent.get_run_trades,
                days_back=7,
                limit=5,
                allow_stale=False,
            ),
        ]
        futures.extend(
            pool.submit(agent.acquire_run_trades) for _ in range(9)
        )
        assert session.started.wait(timeout=5)
        session.release.set()
        results = [future.result(timeout=5) for future in futures]

    assert len(session.calls) == 1
    _, request = session.calls[0]
    assert request["params"]["s"] == ""
    assert request["params"]["fd"] == "30"
    assert request["params"]["cnt"] == "500"
    assert request["timeout"] == 15

    # Canonical consumers receive all parser rows for explicit temporal
    # accounting; a narrower view drops old and future parseable rows.
    assert len(results[0]) == 4
    assert len(results[1]) == 4
    assert len(results[2]) == 2
    assert {row["ticker"] for row in results[2]} == {"ROKU", "LMT"}

    results[0][0]["ticker"] = "MUTATED"
    assert agent.acquire_run_trades()[0]["ticker"] == "ROKU"
    assert agent.has_live_run_data is True
    assert agent.run_uses_stale_cache is False

    health = agent.get_health()
    health["scope"]["limit"] = -1
    assert agent.get_health()["scope"] == {
        "ticker": "ALL",
        "days_back": 30,
        "limit": 500,
    }
    assert agent.get_health()["undated_rows"] == 1
    assert agent.get_health()["cache_status"] == "saved"

    cached = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cached["scope"] == {
        "ticker": "ALL",
        "days_back": 30,
        "limit": 500,
    }
    assert cached["rows"][0]["filing_date"] == "2026-08-18 10:00:00"
    assert all("source_stale" not in row for row in cached["rows"])

    # The source uses the caller-owned session but never closes it.
    agent.close()
    assert session.closed is False


def test_retry_cache_and_stale_separation(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    cache_path = temp_dir / "last-good.json"
    seed_session = FakeSession([Response()])
    seed = make_agent(cache_path, seed_session)
    assert seed.acquire_run_trades()
    assert seed.get_health()["status"] == "OK"

    sleeps = []
    failure = requests.exceptions.ConnectionError(
        "[WinError 10061] target machine actively refused it"
    )
    failed_session = FakeSession([failure])
    agent = make_agent(
        cache_path,
        failed_session,
        sleep=sleeps.append,
        allow_insecure_http_fallback=False,
    )

    stale_rows = agent.get_run_trades(
        days_back=30,
        limit=500,
        allow_stale=True,
    )
    assert len(failed_session.calls) == 4
    assert sleeps == [2.0, 5.0, 10.0]
    assert stale_rows
    assert all(row["source_stale"] is True for row in stale_rows)
    assert all(
        row["source_cache_fetched_at"] == "2026-08-18T12:00:00Z"
        for row in stale_rows
    )
    cached_snapshot = agent.acquire_run_trades()
    assert cached_snapshot[0]["source_cache_fetched_at"] == "2026-08-18T12:00:00Z"
    assert agent.get_run_trades(
        days_back=30,
        limit=500,
        allow_stale=False,
    ) == []
    assert len(failed_session.calls) == 4

    health = agent.get_health()
    assert health["status"] == "ERROR"
    assert health["failure_reason"] == "connection_refused"
    assert health["attempts"] == 4
    assert health["retries"] == 3
    assert health["retry_delays_seconds"] == [2.0, 5.0, 10.0]
    assert health["cache_used"] is True
    assert health["cache_status"] == "hit"
    assert health["run_origin"] == "stale_cache"
    assert health["live"] is False
    assert health["stale"] is True
    assert health["fallback_used"] is False
    assert agent.run_uses_stale_cache is True
    assert agent.has_live_run_data is False

    rendered = agent.format_for_whispers(
        days_back=7,
        limit=5,
        allow_stale=True,
    )
    assert rendered[0].startswith("[OpenInsider/WARN]")
    assert any(item.startswith("[OpenInsider/STALE]") for item in rendered)
    # Rendering freshness must never downgrade the transport error.
    assert agent.get_health()["status"] == "ERROR"
    assert len(failed_session.calls) == 4


def test_connection_refusal_uses_narrative_only_http(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    cache_path = temp_dir / "http-narrative-only.json"
    sleeps = []
    failure = requests.exceptions.ConnectionError(
        "[WinError 10061] target machine actively refused it"
    )
    session = FakeSession([
        failure,
        failure,
        failure,
        failure,
        Response(url="http://openinsider.com/screener"),
    ])
    agent = make_agent(cache_path, session, sleep=sleeps.append)

    rows = agent.acquire_run_trades()
    assert rows
    assert len(session.calls) == 5
    assert [call[0] for call in session.calls[:4]] == [
        OpenInsiderAgent.SECURE_BASE_URL
    ] * 4
    assert session.calls[4][0] == OpenInsiderAgent.INSECURE_FALLBACK_URL
    assert sleeps == [2.0, 5.0, 10.0]
    assert all(row["source_transport_scheme"] == "http" for row in rows)
    assert all(row["source_transport_secure"] is False for row in rows)
    assert all(row["signal_eligible"] is False for row in rows)
    assert all(row["link"].startswith("http://") for row in rows)
    assert agent.get_run_trades(
        days_back=30, limit=500, allow_stale=False
    ) == []
    assert len(session.calls) == 5

    health = agent.get_health()
    assert health["status"] == "WARN"
    assert health["failure_reason"] == "connection_refused"
    assert health["https_failure_reason"] == "connection_refused"
    assert health["https_error"]
    assert health["attempts"] == 5
    assert health["retries"] == 3
    assert health["http_fallback_attempted"] is True
    assert health["http_fallback_used"] is True
    assert health["http_fallback_attempts"] == 1
    assert health["transport_scheme"] == "http"
    assert health["transport_secure"] is False
    assert health["run_origin"] == "insecure_http"
    assert health["live"] is False
    assert health["cache_used"] is False
    assert health["cache_status"] == "not_written_insecure_transport"
    assert not cache_path.exists()
    assert agent.run_uses_insecure_http is True
    assert agent.run_uses_stale_cache is False
    assert agent.has_live_run_data is False

    rendered = agent.format_for_whispers(
        days_back=7, limit=5, allow_stale=True
    )
    assert rendered[0].startswith("[OpenInsider/WARN]")
    assert any(
        item.startswith("[OpenInsider/HTTP-INSECURE]")
        for item in rendered
    )
    assert agent.get_health()["status"] == "WARN"
    assert len(session.calls) == 5


def test_retry_only_transport_errors(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    cache_path = temp_dir / "retry-success.json"
    sleeps = []
    session = FakeSession([
        requests.exceptions.Timeout("read timed out"),
        requests.exceptions.ConnectionError("temporary route failure"),
        Response(),
    ])
    agent = make_agent(cache_path, session, sleep=sleeps.append)

    assert agent.acquire_run_trades()
    health = agent.get_health()
    assert len(session.calls) == 3
    assert sleeps == [2.0, 5.0]
    assert health["attempts"] == 3
    assert health["retries"] == 2
    assert health["retry_delays_seconds"] == [2.0, 5.0]
    assert health["status"] == "OK"
    assert health["failure_reason"] is None
    assert health["error"] is None

    for status in (403, 429, 503):
        http_session = FakeSession([Response(status_code=status)])
        http_agent = make_agent(
            temp_dir / f"http-{status}.json",
            http_session,
        )
        assert http_agent.acquire_run_trades() == []
        http_health = http_agent.get_health()
        assert len(http_session.calls) == 1
        assert http_health["attempts"] == 1
        assert http_health["retries"] == 0
        assert http_health["failure_reason"] == f"http_{status}"
        assert http_health["cache_status"] == "missing"

    unexpected_session = FakeSession([RuntimeError("unexpected transport bug")])
    unexpected = make_agent(temp_dir / "unexpected.json", unexpected_session)
    assert unexpected.acquire_run_trades() == []
    unexpected_health = unexpected.get_health()
    assert len(unexpected_session.calls) == 1
    assert unexpected_health["attempts"] == 1
    assert unexpected_health["retries"] == 0
    assert unexpected_health["failure_reason"] == "unexpected_error"
    assert unexpected_health["cache_status"] == "missing"


def test_parser_and_redirect_reasons(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    assert OpenInsiderAgent._trusted_response_url(
        "https://openinsider.com:443/screener"
    )
    assert not OpenInsiderAgent._trusted_response_url(
        "https://openinsider.com:444/screener"
    )
    assert OpenInsiderAgent._trusted_response_url(
        "http://openinsider.com:80/screener", expected_scheme="http"
    )
    assert not OpenInsiderAgent._trusted_response_url(
        "http://openinsider.com:8080/screener", expected_scheme="http"
    )
    fallback_session = FakeSession([Response()])
    fallback = make_agent(
        temp_dir / "parser-fallback.json",
        fallback_session,
    )
    fallback._parse_tables_with_pandas = lambda _html: []
    fallback._parse_tables_with_bs4 = lambda _html: deepcopy(ROWS[:1])
    assert fallback.acquire_run_trades()
    fallback_health = fallback.get_health()
    assert fallback_health["parser"] == "beautifulsoup"
    assert fallback_health["fallback_used"] is True
    assert fallback_health["cache_used"] is False
    assert fallback_health["status"] == "OK"

    failed_session = FakeSession([Response()])
    failed = make_agent(temp_dir / "parser-failed.json", failed_session)
    failed._parse_tables_with_pandas = lambda _html: []
    failed._parse_tables_with_bs4 = lambda _html: []
    assert failed.acquire_run_trades() == []
    failed_health = failed.get_health()
    assert len(failed_session.calls) == 1
    assert failed_health["failure_reason"] == "parser_failed"
    assert failed_health["fallback_used"] is True
    assert failed_health["cache_used"] is False
    assert failed_health["status"] == "ERROR"

    redirect_session = FakeSession([
        Response(url="http://openinsider.com/screener")
    ])
    redirect = make_agent(
        temp_dir / "untrusted-redirect.json",
        redirect_session,
    )
    parse_called = []
    redirect._parse_tables_with_pandas = (
        lambda _html: parse_called.append(True) or deepcopy(ROWS)
    )
    assert redirect.acquire_run_trades() == []
    assert parse_called == []
    redirect_health = redirect.get_health()
    assert redirect_health["failure_reason"] == "untrusted_response_url"
    assert redirect_health["request_status"] == 200
    assert len(redirect_session.calls) == 1


def test_cache_scope_and_soft_diagnostics(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    cache_path = temp_dir / "scoped.json"
    seed = make_agent(cache_path, FakeSession([Response()]))
    assert seed.acquire_run_trades()

    failure = requests.exceptions.Timeout("timeout")
    mismatch_session = FakeSession([failure])
    mismatch = make_agent(
        cache_path,
        mismatch_session,
        run_days_back=60,
        run_limit=500,
    )
    assert mismatch.get_run_trades(
        days_back=60,
        limit=500,
        allow_stale=True,
    ) == []
    mismatch_health = mismatch.get_health()
    assert mismatch_health["failure_reason"] == "timeout"
    assert mismatch_health["cache_status"] == "scope_mismatch"
    assert mismatch_health["cache_used"] is False
    assert mismatch_health["status"] == "ERROR"

    corrupt_path = temp_dir / "corrupt.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")
    corrupt_session = FakeSession([failure])
    corrupt = make_agent(corrupt_path, corrupt_session)
    assert corrupt.acquire_run_trades() == []
    corrupt_health = corrupt.get_health()
    assert corrupt_health["cache_status"] == "read_error"
    assert corrupt_health["cache_error"]
    assert corrupt_health["status"] == "ERROR"

    valid_payload = json.loads(cache_path.read_text(encoding="utf-8"))
    invalid_cases = {
        "wrong-source": ("source", "Elsewhere", "source identity"),
        "untrusted-url": ("source_url", "https://example.com", "source URL"),
        "naive-time": ("fetched_at", "2026-08-18T12:00:00", "fetched_at"),
        "future-time": ("fetched_at", "2026-08-19T12:00:00Z", "future"),
    }
    for label, (field, value, fragment) in invalid_cases.items():
        case_path = temp_dir / f"{label}.json"
        payload = deepcopy(valid_payload)
        payload[field] = value
        case_path.write_text(json.dumps(payload), encoding="utf-8")
        bad_session = FakeSession([failure])
        bad = make_agent(case_path, bad_session)
        assert bad.acquire_run_trades() == []
        bad_health = bad.get_health()
        assert bad_health["cache_status"] == "invalid"
        assert fragment in bad_health["cache_error"]


def test_default_cache_paths_are_scope_safe(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    original_default = OpenInsiderAgent.DEFAULT_CACHE_PATH
    default_path = temp_dir / "openinsider_last_good.json"
    OpenInsiderAgent.DEFAULT_CACHE_PATH = str(default_path)
    try:
        daily_seed = make_agent(None, FakeSession([Response()]))
        deep_seed = make_agent(
            None,
            FakeSession([Response()]),
            run_days_back=60,
            run_limit=500,
        )
        deep_path = temp_dir / "openinsider_last_good_60d_500.json"
        assert daily_seed.cache_path == default_path
        assert deep_seed.cache_path == deep_path
        assert daily_seed.get_health()["cache_path"] == str(default_path)
        assert deep_seed.get_health()["cache_path"] == str(deep_path)
        assert daily_seed.acquire_run_trades()
        assert deep_seed.acquire_run_trades()

        failure = requests.exceptions.Timeout("source unavailable")
        daily_outage = make_agent(None, FakeSession([failure]))
        deep_outage = make_agent(
            None,
            FakeSession([failure]),
            run_days_back=60,
            run_limit=500,
        )
        daily_rows = daily_outage.get_run_trades(
            days_back=30, limit=500, allow_stale=True
        )
        deep_rows = deep_outage.get_run_trades(
            days_back=60, limit=500, allow_stale=True
        )
        assert daily_rows and deep_rows
        assert all(row["source_stale"] for row in daily_rows)
        assert all(row["source_stale"] for row in deep_rows)
        assert daily_outage.get_health()["cache_status"] == "hit"
        assert deep_outage.get_health()["cache_status"] == "hit"
    finally:
        OpenInsiderAgent.DEFAULT_CACHE_PATH = original_default


def test_view_scope_expansion_rejected_before_acquisition(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    session = FakeSession([Response()])
    agent = make_agent(temp_dir / "view-scope.json", session)
    invalid_views = [
        ({"days_back": 31, "limit": 500}, "days_back"),
        ({"days_back": 30, "limit": 501}, "limit"),
    ]
    for kwargs, field in invalid_views:
        try:
            agent.get_run_trades(allow_stale=False, **kwargs)
        except ValueError as exc:
            assert field in str(exc)
            assert "canonical" in str(exc)
        else:
            raise AssertionError("scope expansion was not rejected")

    assert session.calls == []
    ticker_rows = agent.get_ticker_trades("ROKU")
    assert [row["ticker"] for row in ticker_rows] == ["ROKU"]
    assert len(session.calls) == 1


def test_cache_write_failure_keeps_live_rows(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    directory_path = temp_dir / "cache-is-a-directory"
    directory_path.mkdir()
    session = FakeSession([Response()])
    agent = make_agent(directory_path, session)
    rows = agent.acquire_run_trades()
    assert rows
    health = agent.get_health()
    assert health["status"] == "OK"
    assert health["cache_status"] == "write_error"
    assert health["cache_error"]
    assert health["live"] is True
    assert health["run_origin"] == "live"
    assert len(session.calls) == 1


def test_source_request_gate(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    agent = make_agent(
        temp_dir / "gate.json",
        FakeSession([Response()]),
        min_request_interval=2.0,
        sleep=sleep,
        clock=lambda: now[0],
    )
    agent._wait_for_request_slot()
    agent._wait_for_request_slot()
    assert sleeps == [2.0]


def test_process_request_gate_serializes_agents(temp_dir):
    OpenInsiderAgent._reset_source_request_gate_for_tests()
    first_session = BlockingSession(Response())
    second_session = BlockingSession(Response())
    first_agent = make_agent(
        temp_dir / "gate-first.json",
        first_session,
    )
    second_agent = make_agent(
        temp_dir / "gate-second.json",
        second_session,
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first_agent.acquire_run_trades)
        first_seen = first_session.started.wait(timeout=5)
        if not first_seen:
            first_session.release.set()
        assert first_seen

        second_future = pool.submit(second_agent.acquire_run_trades)
        second_started_early = second_session.started.wait(timeout=0.1)
        first_session.release.set()
        second_started_after_release = second_session.started.wait(timeout=5)
        second_session.release.set()

        assert second_started_early is False
        assert second_started_after_release is True
        assert first_future.result(timeout=5)
        assert second_future.result(timeout=5)

    assert len(first_session.calls) == 1
    assert len(second_session.calls) == 1


def main():
    with TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        test_single_flight_and_local_views(temp_dir)
        test_retry_cache_and_stale_separation(temp_dir)
        test_connection_refusal_uses_narrative_only_http(temp_dir)
        test_retry_only_transport_errors(temp_dir)
        test_parser_and_redirect_reasons(temp_dir)
        test_cache_scope_and_soft_diagnostics(temp_dir)
        test_default_cache_paths_are_scope_safe(temp_dir)
        test_view_scope_expansion_rejected_before_acquisition(temp_dir)
        test_cache_write_failure_keeps_live_rows(temp_dir)
        test_process_request_gate_serializes_agents(temp_dir)
        test_source_request_gate(temp_dir)
    print("OpenInsider resilience checks passed")


if __name__ == "__main__":
    main()
