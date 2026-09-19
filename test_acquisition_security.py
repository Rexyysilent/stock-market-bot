import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import requests
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
