"""Paper-research identity, clock, evidence and append-only invariants."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from portfolio_lab.journal import (
    DATABASE, ROOT, _workspace_path, create_workspace, read_journal, record_decision,
)


FREEZE = "2026-10-23T08:00:00Z"
RECORD = "2026-10-23T09:00:00Z"
LATER = "2026-10-24T09:00:00Z"
RULE = "a" * 64


def study_fixture():
    return {
        "schema_version": "portfolio-lab.study.v1", "study_id": "synthetic-journal-test",
        "strategy_sha256": RULE, "currency": "USD", "calendar": "XNYS",
        "assets": {"ALFA": {"kind": "equity"}, "BASE": {"kind": "equity_etf"}},
        "universe_basis": "fixed_operator_selection",
        "universe_declared_at": "2026-10-22T00:00:00Z", "initial_cash": 10000,
        "benchmark_weights": {"BASE": 1},
        "cost_model": {"kind": "generic", "buy_bps": 1, "sell_bps": 1, "fixed_per_order": 0},
        "slippage_bps": 1, "annual_fixed_cost": 0, "benchmark_annual_fixed_cost": 0,
        "execution": {"latency_seconds": 60, "extra_delay_sessions": 0,
                      "max_decision_age_days": 35, "min_trade_value": 0},
        "limits": {"max_weight": 1, "max_gross": 1},
        "evaluation": {"holdout_start": "2026-11-01", "declared_at": "2026-10-22T00:00:00Z",
                       "minimum_holdout_sessions": 252, "minimum_paper_decisions": 12,
                       "objective": "Synthetic invariant tests; no investment inference"},
        "contributions": [],
    }


def decision_fixture(identifier="paper-001"):
    return {"decision_id": identifier, "strategy_sha256": RULE,
            "decided_at": "2026-10-23T08:30:00Z",
            "information_cutoff": "2026-10-23T08:29:00Z",
            "weights": {"ALFA": 0.5}}


class PortfolioJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "private-workspace"
        self.study = study_fixture()
        clock = datetime.fromisoformat(FREEZE.replace("Z", "+00:00"))
        with patch("portfolio_lab.journal._utc_now", return_value=clock):
            self.created = create_workspace(self.path, self.study)

    def source(self, **changes):
        document = {"schema_version": "2.8", "generated_at": "2026-10-23T08:45:00Z",
                    "health": {"status": "OK"}, "sections": {},
                    "synthetic": True}
        document.update(changes)
        path = self.root / "private-brief.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def record(self, decision=None, **kwargs):
        return record_decision(self.path, decision or decision_fixture(), now=RECORD, **kwargs)

    def test_workspace_is_pinned_idempotent_and_independent_of_caller_mutation(self):
        again = create_workspace(self.path, deepcopy(self.study))
        self.assertEqual(again["study_sha256"], self.created["study_sha256"])
        self.assertEqual(again["frozen_at"], FREEZE)
        self.study["initial_cash"] = 999
        with self.assertRaisesRegex(ValueError, "differs"):
            create_workspace(self.path, self.study)
        self.assertEqual(read_journal(self.path)["study"]["initial_cash"], 10000)

    def test_nonempty_unmarked_directory_refuses_and_preserves_user_files(self):
        directory = self.root / "already-used"
        directory.mkdir()
        note = directory / "important.txt"
        note.write_text("keep me", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "nonempty"):
            create_workspace(directory, self.study)
        self.assertEqual(note.read_text(), "keep me")
        self.assertFalse((directory / DATABASE).exists())

    def test_repository_and_parent_cannot_be_workspaces(self):
        for path in (ROOT, ROOT.parent):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "dedicated"):
                create_workspace(path, self.study)

    def test_paper_data_cannot_be_written_to_a_public_repository_path(self):
        for relative in ("private-test", "fixtures/private-account", "docs/paper-account"):
            with self.subTest(relative=relative), self.assertRaisesRegex(ValueError, "ignored"):
                _workspace_path(ROOT / relative)
        for directory in ("workspaces", "study-runs", "output", "review-results"):
            self.assertEqual(_workspace_path(ROOT / directory / "paper"), ROOT / directory / "paper")

    def test_read_missing_does_not_create_directory_or_database(self):
        missing = self.root / "missing"
        with self.assertRaises(ValueError):
            read_journal(missing)
        self.assertFalse(missing.exists())

    def test_recording_clock_prevents_historical_paper_credit(self):
        result = self.record()
        decision = result["decision"]
        self.assertEqual(decision["origin"], "paper")
        self.assertEqual(decision["decided_at"], RECORD)
        self.assertEqual(decision["recorded_at"], RECORD)
        self.assertEqual(result["receipt"]["requested_decided_at"], "2026-10-23T08:30:00Z")
        self.assertEqual(read_journal(self.path)["decisions"], [decision])

    def test_normal_recording_uses_application_clock(self):
        clock = datetime.fromisoformat(RECORD.replace("Z", "+00:00"))
        with patch("portfolio_lab.journal._utc_now", return_value=clock):
            result = record_decision(self.path, decision_fixture())
        self.assertEqual(result["decision"]["recorded_at"], RECORD)

    def test_clock_is_taken_after_archive_read_not_before_it(self):
        from portfolio_lab import journal
        source = self.source()
        clock = [datetime.fromisoformat(RECORD.replace("Z", "+00:00"))]
        original = journal._archive

        def slow_read(path):
            result = original(path)
            clock[0] = datetime.fromisoformat(LATER.replace("Z", "+00:00"))
            return result

        with patch("portfolio_lab.journal._archive", side_effect=slow_read), \
                patch("portfolio_lab.journal._utc_now", side_effect=lambda: clock[0]):
            result = record_decision(self.path, decision_fixture(), archive_paths=[source])
        self.assertEqual(result["decision"]["recorded_at"], LATER)
        self.assertEqual(result["decision"]["evidence"][0]["known_at"], LATER)

    def test_caller_cannot_assert_recording_time_or_paper_evidence(self):
        for addition in ({"recorded_at": FREEZE},
                         {"evidence": [{"archive_sha256": "b" * 64}]},
                         {"origin": "retrospective"}):
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                self.record({**decision_fixture(), **addition})
        self.assertEqual(read_journal(self.path)["decisions"], [])

    def test_future_and_reversed_requests_are_rejected_atomically(self):
        for addition in ({"decided_at": LATER},
                         {"information_cutoff": "2026-10-23T08:31:00Z"}):
            with self.subTest(addition=addition), self.assertRaises(ValueError):
                self.record({**decision_fixture(), **addition})
        self.assertEqual(read_journal(self.path)["receipts"], [])

    def test_recording_cannot_precede_freeze_or_previous_receipt(self):
        early = decision_fixture()
        early.update(decided_at="2026-10-20T00:00:00Z", information_cutoff="2026-10-20T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "clock"):
            record_decision(self.path, early, now="2026-10-22T00:00:00Z")
        self.record()
        with self.assertRaisesRegex(ValueError, "clock"):
            record_decision(self.path, decision_fixture("paper-002"), now="2026-10-23T08:59:00Z")
        self.assertEqual(len(read_journal(self.path)["decisions"]), 1)

    def test_identical_retry_is_idempotent_at_later_clock(self):
        first = self.record()
        second = record_decision(self.path, decision_fixture(), now=LATER)
        self.assertTrue(second["already_recorded"])
        self.assertEqual(first["receipt"], second["receipt"])
        self.assertEqual(first["decision"], second["decision"])
        self.assertEqual(len(read_journal(self.path)["decisions"]), 1)

    def test_omitted_times_retry_is_still_idempotent(self):
        request = {"decision_id": "paper-001", "strategy_sha256": RULE, "weights": {}}
        first = self.record(request)
        later = record_decision(self.path, request, now=LATER)
        self.assertEqual(first["receipt"], later["receipt"])
        self.assertTrue(later["already_recorded"])

    def test_conflicting_decision_id_is_not_overwritten(self):
        self.record()
        changed = decision_fixture()
        changed["weights"] = {"ALFA": 0.6}
        with self.assertRaisesRegex(ValueError, "different content"):
            self.record(changed)
        self.assertEqual(read_journal(self.path)["decisions"][0]["weights"], {"ALFA": 0.5})

    def test_archive_exact_bytes_and_new_knowledge_time_are_bound(self):
        source = self.source()
        expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        result = self.record(archive_paths=[source])
        evidence = result["decision"]["evidence"][0]
        self.assertEqual(evidence["archive_sha256"], expected_hash)
        self.assertEqual(evidence["generated_at"], "2026-10-23T08:45:00Z")
        self.assertEqual(evidence["known_at"], RECORD)
        self.assertEqual(result["decision"]["information_cutoff"], RECORD)
        self.assertNotIn(str(source), json.dumps(read_journal(self.path)))
        self.assertFalse(result["receipt"]["synthetic_or_source_truth_verified"])

    def test_warn_archive_is_retained_without_turning_it_into_ok(self):
        source = self.source(health={"status": "WARN", "warnings": ["synthetic missing source"]})
        result = self.record(archive_paths=[source])
        self.assertEqual(result["decision"]["evidence"][0]["health_status"], "WARN")

    def test_changed_archive_bytes_conflict_even_if_same_path(self):
        source = self.source()
        self.record(archive_paths=[source])
        source.write_bytes(source.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.record(archive_paths=[source])
        self.assertEqual(len(read_journal(self.path)["receipts"]), 1)

    def test_future_error_unknown_and_naive_archives_fail_closed(self):
        for changes in ({"generated_at": LATER}, {"health": {"status": "ERROR"}},
                        {"schema_version": "2099"}, {"schema_version": []},
                        {"generated_at": "2026-10-23T08:45:00"}):
            source = self.source(**changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.record(archive_paths=[source])
        self.assertEqual(read_journal(self.path)["decisions"], [])

    def test_duplicate_sources_and_duplicate_json_keys_fail_closed(self):
        source = self.source()
        with self.assertRaisesRegex(ValueError, "more than once"):
            self.record(archive_paths=[source, source])
        source.write_text('{"schema_version":"2.8","schema_version":"2.7"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate keys"):
            self.record(archive_paths=[source])
        self.assertEqual(read_journal(self.path)["receipts"], [])

    def test_invalid_weights_or_strategy_leave_no_partial_receipt(self):
        for change in ({"weights": {"ALFA": 2}}, {"strategy_sha256": "b" * 64}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.record({**decision_fixture(), **change})
        self.assertEqual(read_journal(self.path)["decisions"], [])

    def test_receipt_chain_connects_every_decision_to_frozen_study(self):
        first = self.record()
        second = record_decision(self.path, decision_fixture("paper-002"), now=LATER)
        self.assertEqual(first["receipt"]["previous_sha256"], self.created["genesis_sha256"])
        self.assertEqual(second["receipt"]["previous_sha256"], first["receipt"]["receipt_sha256"])
        self.assertEqual([r["sequence"] for r in read_journal(self.path)["receipts"]], [1, 2])

    def test_sql_update_and_delete_are_refused(self):
        self.record()
        conn = sqlite3.connect(self.path / DATABASE)
        self.addCleanup(conn.close)
        for sql in ("DELETE FROM decisions", "UPDATE decisions SET decision_id='changed'",
                    "DELETE FROM workspace", "UPDATE workspace SET frozen_at='changed'"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.IntegrityError):
                conn.execute(sql)
        conn.rollback()
        self.assertEqual(len(read_journal(self.path)["decisions"]), 1)

    def test_changed_study_file_is_detected_on_read_and_append(self):
        changed = deepcopy(self.study)
        changed["initial_cash"] = 20000
        (self.path / "study.json").write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "changed"):
            read_journal(self.path)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.record()

    def test_receipt_corruption_is_detected_before_any_new_append(self):
        self.record()
        conn = sqlite3.connect(self.path / DATABASE)
        conn.execute("DROP TRIGGER decisions_no_update")
        conn.execute("UPDATE decisions SET receipt_sha256=?", ("f" * 64,))
        conn.commit()
        conn.close()
        with self.assertRaisesRegex(ValueError, "chain"):
            read_journal(self.path)
        with self.assertRaisesRegex(ValueError, "chain"):
            self.record(decision_fixture("paper-002"))


if __name__ == "__main__":
    unittest.main()
