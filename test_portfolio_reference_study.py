"""Offline orchestration checks using fabricated prices and the real NYSE calendar.

Calendar metadata is the installed locked dependency; no market-data request is
made. These checks establish timing, accounting and custody, never performance.
"""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas_market_calendars as mcal

from portfolio_lab.data import digest, utc
from scripts import portfolio_reference_study as runner


class PortfolioReferenceStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schedule = mcal.get_calendar("NYSE").schedule(
            start_date="2023-01-01", end_date="2024-03-15")
        rows = []
        for position, (stamp, row) in enumerate(schedule.iterrows()):
            day = stamp.date().isoformat()
            # A rising warmup, then an observable loss and eventual cash regime.
            # These are authored test values, unrelated to any real instrument.
            if day < "2024-01-01":
                opening, change = 100.0 + position * 0.1, 0.05
            elif day < "2024-02-01":
                opening, change = 150.0 + int(day[-2:]) * 0.05, 0.1
            elif day < "2024-03-01":
                opening, change = 100.0 - int(day[-2:]) * 0.15, -0.05
            else:
                opening, change = 90.0 + int(day[-2:]) * 0.15, 0.05
            rows.append({"session": day, "open_at": row["market_open"].isoformat(),
                         "close_at": row["market_close"].isoformat(),
                         "prices": {"ALFA": {"open": opening, "close": opening + change}}})
        cls.fixture = {
            "schema_version": "portfolio-lab.dataset.v1", "dataset_id": "synthetic-reference-study",
            "synthetic": True, "currency": "USD", "calendar": "XNYS",
            "price_basis": "total_return_proxy", "adjustment_policy": "Authored synthetic return units.",
            "acquired_at": "2024-03-16T00:00:00Z",
            "source": {"name": "Authored offline fixture", "url": "https://example.invalid/reference",
                       "rights_note": "Synthetic prices only; no provider payload.",
                       "calendar_source": "Installed pandas_market_calendars NYSE schedule."},
            "expected_sessions": [row["session"] for row in rows], "sessions": rows,
        }

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.acquisition = self.root / "acquisition.json"
        self.output = self.root / "reference"
        self._save(self.fixture)
        # A replay is a local operation; an accidental provider call fails this
        # test immediately instead of silently converting it into a smoke test.
        no_network = patch("portfolio_lab.providers.fetch_yahoo",
                           side_effect=AssertionError("Offline replay requested a provider"))
        self.fetch = no_network.start()
        self.addCleanup(no_network.stop)

    def _save(self, dataset):
        self.acquisition.write_text(json.dumps(dataset, allow_nan=False), encoding="utf-8")

    def _run(self, *, now="2024-04-01T12:00:00Z", **overrides):
        options = {"symbol": "ALFA", "history-start": "2023-01-01",
                   "evaluation-start": "2024-01-02", "end": "2024-03-15",
                   "holdout-start": "2024-02-01", "initial-cash": 10000,
                   "annual-fixed-cost": 120, "dataset": self.acquisition, "out": self.output}
        options.update(overrides)
        argv = [item for name, value in options.items() if value is not None
                for item in ("--" + name, str(value))]
        output, error = io.StringIO(), io.StringIO()
        with patch.object(runner, "datetime") as clock, redirect_stdout(output), redirect_stderr(error):
            clock.now.return_value = utc(now)
            try:
                code = runner.main(argv)
            except SystemExit as exc:
                code = exc.code
        return code, output.getvalue(), error.getvalue()

    def _json(self, relative):
        return json.loads((self.output / relative).read_text(encoding="utf-8"))

    def _rejected(self, *, contains=None, **options):
        code, stdout, stderr = self._run(**options)
        self.assertEqual(code, 2, (stdout, stderr))
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("Error:"), stderr)
        self.assertNotIn("Traceback", stderr)
        if contains:
            self.assertIn(contains, stderr)
        self.assertFalse(self.output.exists())

    def test_completed_warmup_and_partial_month_keep_the_exact_evaluation_window(self):
        code, stdout, error = self._run()
        self.assertEqual(code, 0, error)
        summary = self._json("summary.json")
        report = self._json("base/report.json")
        dataset = self._json("base/dataset.json")
        decisions = self._json("base/decisions.json")["decisions"]
        expected = [day for day in self.fixture["expected_sessions"] if day >= "2024-01-02"]
        self.assertEqual(dataset["expected_sessions"], expected)
        self.assertEqual([row["session"] for row in report["strategy"]["daily_path"]], expected)
        self.assertEqual(summary["evaluation_start"], "2024-01-02")
        self.assertEqual(summary["end"], "2024-03-15")
        self.assertEqual(summary["sessions"], len(expected))
        self.assertEqual(json.loads(stdout)["sessions"], len(expected))
        self.assertEqual([item["decision_id"] for item in decisions], [
            "sma-ALFA-2023-12-29", "sma-ALFA-2024-01-31", "sma-ALFA-2024-02-29"])
        self.assertIn("2024-03-28", self._json("month-end-sessions.json"))
        self.assertNotIn("2024-03-15", self._json("month-end-sessions.json"))
        first_trade = report["strategy"]["trades"][0]
        first_bar = next(row for row in self.fixture["sessions"] if row["session"] == "2024-01-02")
        self.assertEqual(first_trade["session"], "2024-01-02")
        self.assertEqual(first_trade["reference_proxy_open"], first_bar["prices"]["ALFA"]["open"])
        self.assertEqual(report["strategy"]["metrics"]["initial_value"], 10000)
        self.assertEqual(report["evaluation"]["holdout_paper_decisions"], 0)
        self.assertFalse(report["evaluation"]["statistical_or_investment_approval"])
        self.assertFalse(report["evaluation"]["declared_before_holdout"])
        self.assertTrue(summary["synthetic"])
        self.assertTrue(summary["not_private_bot_performance"])
        self.assertEqual(summary["declared_at"], "2024-04-01T12:00:00+00:00")
        self.fetch.assert_not_called()

    def test_cost_and_delay_stresses_share_inputs_without_parameter_selection(self):
        self.assertEqual(self._run()[0], 0)
        summary = self._json("summary.json")
        reports = {name: self._json(name + "/report.json")
                   for name in ("base", "double-costs", "one-session-delay")}
        base, double, delayed = (reports[name] for name in reports)
        self.assertEqual(summary["parameter_trials"], 1)
        self.assertEqual([row["name"] for row in summary["scenarios"]], list(reports))
        self.assertEqual([row["cost_multiplier"] for row in reports.values()], [1.0, 2.0, 1.0])
        self.assertEqual([row["extra_delay_sessions"] for row in reports.values()], [0, 0, 1])
        self.assertEqual(base["gross_strategy"], double["gross_strategy"])
        self.assertEqual(base["benchmark"], delayed["benchmark"])
        self.assertAlmostEqual(double["strategy"]["metrics"]["costs"]["overhead"],
                               2 * base["strategy"]["metrics"]["costs"]["overhead"])
        self.assertLess(double["strategy"]["metrics"]["ending_value"],
                        base["strategy"]["metrics"]["ending_value"])
        self.assertEqual(base["decision_executions"][0]["effective_session"], "2024-01-02")
        self.assertEqual(delayed["decision_executions"][0]["effective_session"], "2024-01-03")
        self.assertEqual(base["decision_executions"][-1]["effective_session"], "2024-03-01")
        self.assertEqual(delayed["decision_executions"][-1]["effective_session"], "2024-03-04")
        self.assertEqual(base["strategy"]["metrics"]["contributions"], 0)
        for field in ("study_sha256", "dataset_sha256", "decisions_sha256"):
            self.assertEqual(len({item[field] for item in summary["scenarios"]}), 1)

    def test_replay_is_reproducible_and_receipts_bind_saved_full_warmup(self):
        self.assertEqual(self._run()[0], 0)
        first = self.output
        self.output = self.root / "replay"
        self.assertEqual(self._run()[0], 0)
        for relative in ("summary.json", "rule.json", "month-end-sessions.json", "calendar-validation.json", "full-acquisition.json",
                         "base/report.json", "base/study.json", "base/dataset.json", "base/decisions.json"):
            self.assertEqual((first / relative).read_bytes(), (self.output / relative).read_bytes())
        self.assertEqual(self._json("full-acquisition.json"), self.fixture)
        for name in ("base", "double-costs", "one-session-delay"):
            receipt = self._json(name + "/receipt.json")
            for relative, fingerprint in receipt["inputs"].items():
                self.assertEqual(fingerprint, digest(self._json(name + "/" + relative)))
            self.assertEqual(receipt["context"]["full_acquisition_sha256"], digest(self.fixture))
            self.assertEqual(receipt["context"]["rule_sha256"], digest(self._json("rule.json")))
            self.assertEqual(receipt["context"]["month_end_sessions_sha256"],
                             digest(self._json("month-end-sessions.json")))
            self.assertEqual(receipt["context"]["calendar_validation_sha256"],
                             digest(self._json("calendar-validation.json")))
            self.assertEqual(receipt["context"]["requested_range"],
                             {"history_start": "2023-01-01", "evaluation_start": "2024-01-02", "end": "2024-03-15"})
        audit = self._json("calendar-validation.json")
        expected_schedule = [{key: row[key] for key in ("session", "open_at", "close_at")}
                             for row in self.fixture["sessions"]]
        self.assertEqual(audit["schedule_sha256"], digest(expected_schedule))
        self.assertEqual(audit["library_version"], mcal.__version__)

    def test_no_ten_completed_months_before_evaluation_fails_without_output(self):
        dataset = deepcopy(self.fixture)
        dataset["sessions"] = [row for row in dataset["sessions"] if row["session"] >= "2023-05-01"]
        dataset["expected_sessions"] = [row["session"] for row in dataset["sessions"]]
        self._save(dataset)
        self._rejected(contains="warmup", **{"history-start": "2023-05-01"})

    def test_replay_cannot_silently_extend_or_shorten_requested_end(self):
        self._rejected(contains="calendar", end="2024-03-14")
        self._rejected(contains="calendar", end="2024-03-18")

    def test_replay_cannot_silently_change_requested_history_start(self):
        self._rejected(contains="calendar", **{"history-start": "2023-01-04"})
        self._rejected(contains="calendar", **{"history-start": "2022-12-30"})

    def test_removed_interior_session_cannot_hide_in_a_matching_self_reported_schedule(self):
        dataset = deepcopy(self.fixture)
        dataset["sessions"] = [row for row in dataset["sessions"] if row["session"] != "2023-06-20"]
        dataset["expected_sessions"] = [row["session"] for row in dataset["sessions"]]
        self._save(dataset)
        self._rejected(contains="calendar")

    def test_real_early_close_clock_must_match_even_when_local_date_is_correct(self):
        dataset = deepcopy(self.fixture)
        row = next(row for row in dataset["sessions"] if row["session"] == "2023-11-24")
        self.assertEqual(utc(row["close_at"]).hour, 18)
        row["close_at"] = "2023-11-24T21:00:00Z"
        self._save(dataset)
        self._rejected(contains="calendar")

    def test_currency_calendar_and_symbol_mismatch_fail_before_output(self):
        for key, value in (("currency", "INR"), ("calendar", "XNSE")):
            with self.subTest(key=key):
                dataset = deepcopy(self.fixture)
                dataset[key] = value
                self._save(dataset)
                self._rejected(contains="currency/calendar")
        self._save(self.fixture)
        self._rejected(contains="asset", symbol="BETA")

    def test_future_requested_date_fails_even_if_it_is_a_nontrading_day(self):
        self._rejected(contains="Future", now="2024-03-16T00:30:00Z", end="2024-03-16")
        self.fetch.assert_not_called()

    def test_incomplete_current_session_is_rejected_before_replay_or_acquisition(self):
        for source in (self.acquisition, None):
            with self.subTest(replay=source is not None):
                self._rejected(contains="incomplete", now="2024-03-15T19:00:00Z", dataset=source)
        self.fetch.assert_not_called()

    def test_existing_output_is_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "prior-private-result.txt"
        sentinel.write_text("preserve exact prior result\n", encoding="utf-8")
        code, stdout, error = self._run()
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Output exists", error)
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve exact prior result\n")

    def test_public_repository_output_is_forbidden(self):
        self.output = runner.ROOT / "docs" / "never-write-private-reference-study"
        self._rejected(contains="ignored workspace")

    def test_cache_and_staging_cannot_be_created_at_repository_root(self):
        repository = self.root / "fake-repository"
        repository.mkdir()
        self.output = repository / "workspaces"
        with patch.object(runner, "ROOT", repository), patch(
                "portfolio_lab.__main__.__file__", str(repository / "portfolio_lab" / "__main__.py")):
            self._rejected(contains="study subdirectory", dataset=None)
        self.assertEqual(list(repository.iterdir()), [])
        self.fetch.assert_not_called()

    def test_a_failed_stress_cannot_publish_a_partial_study(self):
        original = runner.simulate

        def fail_second(*args, **kwargs):
            if kwargs.get("cost_multiplier") == 2:
                raise ValueError("Synthetic stress failure")
            return original(*args, **kwargs)

        with patch.object(runner, "simulate", side_effect=fail_second):
            self._rejected(contains="Synthetic stress failure")

    def test_unbounded_calendar_range_is_rejected_before_constructing_a_schedule(self):
        with patch.object(mcal, "get_calendar", side_effect=AssertionError("Unbounded schedule constructed")):
            self._rejected(contains="bounded", **{"history-start": "1800-01-01"})


if __name__ == "__main__":
    unittest.main()
