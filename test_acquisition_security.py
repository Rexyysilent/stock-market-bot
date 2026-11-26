import gzip
import io
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import requests
import urllib3
from requests.adapters import BaseAdapter
from requests.models import Response

from openinsider_agent import OpenInsiderAgent


REPO_ROOT = Path(__file__).resolve().parent
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
UNTRUSTED_LOCATIONS = (
    "https://attacker.invalid/payload",
    "http://127.0.0.1/admin",
    "http://10.0.0.7/internal",
    "//attacker.invalid/scheme-relative",
)


class RedirectRecordingAdapter(BaseAdapter):
    def __init__(self, *, status=302, location=UNTRUSTED_LOCATIONS[0], refuse_https=False):
        self.status = status
        self.location = location
        self.refuse_https = refuse_https
        self.urls = []

    def send(self, request, **kwargs):
        self.urls.append(request.url)
        if self.refuse_https and request.url.startswith("https://"):
            raise requests.exceptions.ConnectionError("connection refused")
        response = Response()
        response.status_code = self.status
        response.url = request.url
        response.headers["Location"] = self.location
        response._content = b"malicious parser bait"
        response.request = request
        return response

    def close(self):
        pass


class ParserMustNotRunAgent(OpenInsiderAgent):
    def _parse_tables_with_pandas(self, _html):
        raise AssertionError("redirect body reached parser")

    def _parse_tables_with_bs4(self, _html):
        raise AssertionError("redirect body reached fallback parser")


class OpenInsiderRedirectTests(unittest.TestCase):
    def setUp(self):
        OpenInsiderAgent._reset_source_request_gate_for_tests()

    def _agent(self, adapter, cache_path):
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return ParserMustNotRunAgent(
            session=session,
            cache_path=cache_path,
            min_request_interval=0,
            sleep=lambda _seconds: None,
            jitter=lambda: 0,
        )

    def test_https_redirect_is_not_followed_or_parsed_or_cached(self):
        for status in REDIRECT_STATUSES:
            for location in UNTRUSTED_LOCATIONS:
                with self.subTest(status=status, location=location):
                    with tempfile.TemporaryDirectory() as directory:
                        cache_path = Path(directory) / "cache.json"
                        adapter = RedirectRecordingAdapter(
                            status=status, location=location
                        )
                        agent = self._agent(adapter, cache_path)
                        self.assertEqual(agent.acquire_run_trades(), [])
                        health = agent.get_health()

                        self.assertEqual(len(adapter.urls), 1)
                        self.assertTrue(
                            adapter.urls[0].startswith("https://openinsider.com/")
                        )
                        self.assertEqual(health["status"], "ERROR")
                        self.assertEqual(health["request_status"], status)
                        self.assertEqual(
                            health["failure_reason"], f"http_{status}"
                        )
                        self.assertIsNone(health["parser"])
                        self.assertFalse(health["cache_used"])
                        self.assertFalse(cache_path.exists())

    def test_http_fallback_redirect_is_not_followed_or_trusted(self):
        for status in REDIRECT_STATUSES:
            for location in UNTRUSTED_LOCATIONS:
                with self.subTest(status=status, location=location):
                    with tempfile.TemporaryDirectory() as directory:
                        cache_path = Path(directory) / "cache.json"
                        adapter = RedirectRecordingAdapter(
                            status=status,
                            location=location,
                            refuse_https=True,
                        )
                        agent = self._agent(adapter, cache_path)
                        self.assertEqual(agent.acquire_run_trades(), [])
                        health = agent.get_health()

                        self.assertEqual(len(adapter.urls), 5)
                        self.assertEqual(
                            sum(url.startswith("https://") for url in adapter.urls),
                            4,
                        )
                        self.assertEqual(
                            sum(url.startswith("http://") for url in adapter.urls),
                            1,
                        )
                        self.assertNotIn(location, adapter.urls)
                        self.assertEqual(health["status"], "ERROR")
                        self.assertEqual(health["request_status"], status)
                        self.assertEqual(
                            health["https_failure_reason"],
                            "connection_refused",
                        )
                        self.assertEqual(
                            health["failure_reason"],
                            f"http_fallback_http_{status}",
                        )
                        self.assertFalse(health["http_fallback_used"])
                        self.assertIsNone(health["parser"])
                        self.assertFalse(health["cache_used"])
                        self.assertFalse(cache_path.exists())


class _CountingRaw(io.BytesIO):
    """A raw body that records how many bytes the client pulled from the wire."""

    def __init__(self, data):
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size=-1, *args, **kwargs):
        chunk = super().read(size)
        self.bytes_read += len(chunk)
        return chunk


class BodyAdapter(BaseAdapter):
    """Serves one 200 response per request with a caller-built raw body."""

    def __init__(self, make_raw, *, headers=None, refuse_https=False):
        self.make_raw = make_raw
        self.headers = headers or {}
        self.refuse_https = refuse_https
        self.urls = []
        self.raws = []
        self.stream_flags = []

    def send(self, request, **kwargs):
        self.urls.append(request.url)
        self.stream_flags.append(kwargs.get("stream"))
        if self.refuse_https and request.url.startswith("https://"):
            raise requests.exceptions.ConnectionError("connection refused")
        response = Response()
        response.status_code = 200
        response.url = request.url
        response.headers.update({"Content-Type": "text/html; charset=utf-8", **self.headers})
        response.encoding = "utf-8"
        response.raw = self.make_raw()
        self.raws.append(response.raw)
        response.request = request
        return response

    def close(self):
        pass


class RecordingParserAgent(OpenInsiderAgent):
    parsed = None

    def _parse_tables_with_pandas(self, html):
        type(self).parsed = html
        return [{"ticker": "ROKU", "filing_date": "2026-08-18 10:00:00",
                 "trade_type": "P - Purchase", "insider_name": "Fixture",
                 "value": "$1", "source": "OpenInsider",
                 "link": "https://openinsider.com/screener"}]


class OpenInsiderBoundedReadTests(unittest.TestCase):
    LIMIT = OpenInsiderAgent.MAX_RESPONSE_BYTES

    def setUp(self):
        OpenInsiderAgent._reset_source_request_gate_for_tests()

    def _run(self, adapter, agent_cls=ParserMustNotRunAgent, **kwargs):
        session = requests.Session()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache.json"
            agent = agent_cls(session=session, cache_path=cache_path,
                              min_request_interval=0, sleep=lambda _s: None,
                              jitter=lambda: 0, **kwargs)
            rows = agent.acquire_run_trades()
            return rows, agent.get_health(), cache_path.exists()

    def test_oversized_https_body_is_cut_off_before_any_parser(self):
        adapter = BodyAdapter(lambda: _CountingRaw(b"<tr>" * (self.LIMIT // 4 + 4096)))
        rows, health, cached = self._run(adapter)
        self.assertEqual(rows, [])
        self.assertEqual(health["failure_reason"], "response_size_limit")
        self.assertIsNone(health["parser"])
        self.assertFalse(cached)
        self.assertEqual(adapter.stream_flags, [True])
        # Stopped within one chunk of the cap rather than downloading everything.
        self.assertLessEqual(adapter.raws[0].bytes_read, self.LIMIT + 65536)

    def test_oversized_plaintext_fallback_body_is_also_bounded(self):
        adapter = BodyAdapter(lambda: _CountingRaw(b"<tr>" * (self.LIMIT // 4 + 4096)),
                              refuse_https=True)
        rows, health, cached = self._run(adapter)
        self.assertEqual(rows, [])
        self.assertTrue(adapter.urls[-1].startswith("http://"))
        self.assertEqual(health["failure_reason"], "http_fallback_response_size_limit")
        self.assertIsNone(health["parser"])
        self.assertFalse(cached)
        self.assertLessEqual(adapter.raws[-1].bytes_read, self.LIMIT + 65536)

    def test_declared_oversized_length_is_rejected_without_reading(self):
        adapter = BodyAdapter(lambda: _CountingRaw(b"small"),
                              headers={"Content-Length": str(self.LIMIT + 1)})
        rows, health, _cached = self._run(adapter)
        self.assertEqual(rows, [])
        self.assertEqual(health["failure_reason"], "response_size_limit")
        self.assertEqual(adapter.raws[0].bytes_read, 0)

    def test_decompression_bomb_is_bounded_by_decoded_bytes(self):
        bomb = gzip.compress(b"\0" * (self.LIMIT * 4))
        self.assertLess(len(bomb), self.LIMIT // 100)

        def raw():
            return urllib3.response.HTTPResponse(
                body=io.BytesIO(bomb), headers={"Content-Encoding": "gzip"},
                status=200, preload_content=False, decode_content=True)

        adapter = BodyAdapter(raw, headers={"Content-Encoding": "gzip",
                                            "Content-Length": str(len(bomb))})
        rows, health, _cached = self._run(adapter)
        self.assertEqual(rows, [])
        self.assertEqual(health["failure_reason"], "response_size_limit")
        self.assertIsNone(health["parser"])

    @staticmethod
    def _wire(fp):
        return urllib3.response.HTTPResponse(
            body=fp, headers={}, status=200, preload_content=False, decode_content=True)

    def test_deadline_is_checked_per_socket_read_not_per_64k_chunk(self):
        # Review 2026-11-18: a 1-byte-per-read trickle never filled a 64 KiB
        # iter_content chunk, so the deadline was never consulted.
        reads = []

        class Trickle(io.RawIOBase):
            def readable(self):
                return True

            def readinto(self, buffer):
                reads.append(1)
                if len(reads) > 1000:
                    return 0
                buffer[0:1] = b"x"
                return 1

        ticks = iter(range(0, 1_000_000, 20))
        adapter = BodyAdapter(lambda: self._wire(io.BufferedReader(Trickle(), buffer_size=1)))
        rows, health, _cached = self._run(adapter, clock=lambda: float(next(ticks)))
        self.assertEqual(rows, [])
        self.assertEqual(health["failure_reason"], "read_deadline_exceeded")
        self.assertLess(len(reads), 20)

    def test_mid_body_reset_is_retried_like_a_connection_error(self):
        page = b"<table><tr><td>ok</td></tr></table>"

        class Reset(io.RawIOBase):
            sent = False

            def readable(self):
                return True

            def readinto(self, buffer):
                if not self.sent:
                    self.sent = True
                    buffer[0:4] = b"<tab"
                    return 4
                raise ConnectionResetError("connection reset by peer")

        bodies = iter([lambda: self._wire(io.BufferedReader(Reset(), buffer_size=4)),
                       lambda: self._wire(io.BytesIO(page))])
        adapter = BodyAdapter(lambda: next(bodies)())
        rows, health, _cached = self._run(adapter, agent_cls=RecordingParserAgent)
        self.assertEqual(len(rows), 1, health)
        self.assertEqual(len(adapter.urls), 2)
        self.assertEqual(health["retries"], 1)
        self.assertEqual(RecordingParserAgent.parsed, page.decode())

    def test_body_download_stays_inside_the_request_gate(self):
        held = []

        class Watch(io.RawIOBase):
            done = False

            def readable(self):
                return True

            def readinto(self, buffer):
                held.append(OpenInsiderAgent._SOURCE_REQUEST_GATE_LOCK.locked())
                if self.done:
                    return 0
                self.done = True
                buffer[0:2] = b"ok"
                return 2

        adapter = BodyAdapter(lambda: self._wire(io.BufferedReader(Watch(), buffer_size=2)))
        self._run(adapter, agent_cls=RecordingParserAgent)
        self.assertTrue(held and all(held), held)

    def test_total_read_deadline_stops_a_slow_trickle(self):
        ticks = iter(range(0, 10_000, 20))
        adapter = BodyAdapter(lambda: _CountingRaw(b"x" * (65536 * 8)))
        rows, health, _cached = self._run(adapter, clock=lambda: float(next(ticks)))
        self.assertEqual(rows, [])
        self.assertEqual(health["failure_reason"], "read_deadline_exceeded")
        self.assertIsNone(health["parser"])

    def test_normal_page_is_read_fully_and_parsed(self):
        page = "<table><tr><td>Fixture é</td></tr></table>".encode("utf-8")
        adapter = BodyAdapter(lambda: _CountingRaw(page))
        rows, health, _cached = self._run(adapter, agent_cls=RecordingParserAgent)
        self.assertNotEqual(health["status"], "ERROR", health)
        self.assertEqual(len(rows), 1)
        self.assertEqual(RecordingParserAgent.parsed, page.decode("utf-8"))


class GradeExtractionTests(unittest.TestCase):
    def test_controls_boundaries_repetition_whitespace_and_late_match(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        text = (
            "x" * 300
            + " 1%\u2003U3O8, 2.5 g/t Ag, 6. m @; "
            + "7 meters grading and 3% U3O8"
        )
        self.assertEqual(
            agent._extract_grades(text),
            {
                "uranium_pct": [1.0, 3.0],
                "gold_gpt": [2.5],
                "intercept_m": [6.0, 7.0],
            },
        )
        self.assertIsNone(agent._extract_grades("prefix 91.5% U3O8 suffix".replace("91.5", ".5")))
        self.assertIsNone(agent._extract_grades("1.2.5% U3O8"))
        self.assertIsNone(agent._extract_grades(b"1% U3O8"))

    def test_compact_recognized_units_preserve_threshold_qualification(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        grades = agent._extract_grades("1%U3O8; 8g/t Au; 6m @; 7meters grading")
        self.assertEqual(grades, {
            "uranium_pct": [1.0], "gold_gpt": [8.0], "intercept_m": [6.0, 7.0],
        })
        self.assertTrue(agent._passes_drill_filter(grades))

    def test_over_limit_fails_closed_without_suffix_matching(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        payload = "9" * agent.MAX_GRADE_SOURCE_CHARS + "1% U3O8"
        self.assertIsNone(agent._extract_grades(payload))

    def test_unsupported_numbers_cannot_qualify_a_grade(self):
        from agents.research_agent import ResearchAgent

        agent = ResearchAgent()
        for token in ("-9", "- 9", "+9", "\u22129", "1e9", "1e+9",
                      "1,999", "1_999", "\u0669", "\uff199", "9" * 1000):
            for unit in ("% U3O8", "g/t Au", "m @", "metres grading"):
                with self.subTest(token=token[:20], unit=unit):
                    grades = agent._extract_grades(f"{token} {unit}")
                    self.assertIsNone(grades)
                    self.assertFalse(agent._passes_drill_filter(grades))

    def test_adversarial_negatives_finish_in_bounded_subprocess(self):
        script = textwrap.dedent(
            """
            import os, sys
            expected_guard = os.environ.get("ACQUISITION_TEST_SITECUSTOMIZE")
            if expected_guard:
                assert sys.modules.get("sitecustomize").__file__ == expected_guard
            from agents.research_agent import ResearchAgent
            agent = ResearchAgent()
            cases = [
                "9" * 90000 + "X",
                ("9." * 45000) + "X",
                "9" * 99990 + " g/t nope",
                "x" * 500 + " 12. % U3O8",
            ]
            expected = [None, None, None, {
                "uranium_pct": [12.0], "gold_gpt": [], "intercept_m": []
            }]
            assert [agent._extract_grades(value) for value in cases] == expected
            """
        )
        env = os.environ.copy()
        guard = sys.modules.get("sitecustomize")
        if guard is not None and getattr(guard, "__file__", None):
            env["ACQUISITION_TEST_SITECUSTOMIZE"] = guard.__file__
        # Preserve inherited instrumentation (including sitecustomize network
        # guards) before adding the import root needed from a temporary CWD.
        env["PYTHONPATH"] = os.pathsep.join(
            value for value in (env.get("PYTHONPATH"), str(REPO_ROOT)) if value
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
