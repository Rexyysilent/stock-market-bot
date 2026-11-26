"""Local synthetic lifecycle tests for frozen verification monitoring."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ledger.monitor import _decode, freeze_monitor, monitor_status, run_monitor
from ledger.verification import VerificationPolicy


FROZEN = datetime(2026, 1, 5, 22, tzinfo=timezone.utc)


def policy():
    return VerificationPolicy(policy_id="monitor-test", declared_at="2025-01-01T00:00:00Z",
                              move_threshold=.05, round_trip_cost_bps=20, horizons=(1,))


def archive(generated):
    # Fabricated locally in each test; not distributed or used as market evidence.
    return {"schema_version": "2.8", "pipeline_version": "2.6.5",
            "generated_at": generated.isoformat(), "health": {"status": "OK", "errors": []},
            "universe": {"instrumented_tickers": ["AAA", "SPY"]}, "sections": {}}


class VerificationMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.workspace, self.archives = self.base / "private-monitor", self.base / "archive"

    def freeze(self, *, minimum=28, requested=None):
        with patch("ledger.monitor._now", return_value=FROZEN):
            return freeze_monitor(self.workspace, requested or policy(), self.archives,
                                  min_observation_days=minimum)

    def review(self, *, at=None, **kwargs):
        at = at or FROZEN + timedelta(days=1)
        with patch("ledger.monitor._now", return_value=at), patch("ledger.verification._now", return_value=at):
            return run_monitor(self.workspace, **kwargs)

    def save(self, filename, data):
        self.archives.mkdir(exist_ok=True)
        path = self.archives / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_freeze_corrects_old_declaration_and_is_idempotent(self):
        status = self.freeze()
        self.assertEqual(status["policy"]["declared_at"], "2026-01-05T22:00:00Z")
        before = (self.workspace / "monitor.json").read_bytes()
        self.assertEqual(status, self.freeze())
        self.assertEqual(before, (self.workspace / "monitor.json").read_bytes())
        self.assertFalse(status["independent_timestamp_attestation"])
        changed = policy().to_dict()
        changed["move_threshold"] = .1
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.freeze(requested=changed)
        with self.assertRaisesRegex(ValueError, "frozen"):
            self.freeze(minimum=14)

    def test_empty_source_records_awaiting_archives_without_network(self):
        self.freeze()
        with patch("ledger.verification.fetch_yfinance", side_effect=AssertionError("no provider")):
            result = self.review()
        self.assertEqual(result["status"], "awaiting_archives")
        self.assertEqual(result["report"]["archive_inventory"]["state"], "directory_missing")
        self.assertEqual(result["report"]["verification"]["summary"]["selected_runs"], 0)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 1)
        self.assertFalse(result["report"]["fetch_enabled"])

    def test_inventory_skips_bad_failed_future_and_marked_fixtures(self):
        self.freeze()
        self.save("valid.json", archive(FROZEN + timedelta(hours=1)))
        failed = archive(FROZEN + timedelta(hours=1))
        failed["health"]["status"] = "ERROR"
        self.save("failed.json", failed)
        for field in ("meta", "fixture_metadata"):
            synthetic = archive(FROZEN + timedelta(hours=1))
            synthetic[field] = {"synthetic": True}
            self.save(field + ".json", synthetic)
        demo = archive(FROZEN + timedelta(hours=1))
        demo["demo"] = True
        self.save("demo.json", demo)
        self.save("future.json", archive(FROZEN + timedelta(days=20)))
        self.save("bad.json", [])
        (self.archives / "invalid.json").write_text("{", encoding="utf-8")
        result = self.review()
        inventory = result["report"]["archive_inventory"]
        self.assertEqual(inventory["accepted_files"], 1)
        self.assertEqual(inventory["skipped_files"], 7)
        self.assertEqual(inventory["skip_reasons"]["synthetic_archive"], 3)
        self.assertEqual(inventory["skip_reasons"]["failed_archive"], 1)
        self.assertEqual(inventory["skip_reasons"]["future_archive"], 1)
        self.assertEqual(result["status"], "collecting")

    def test_repository_demo_is_not_accepted_as_real_archive(self):
        self.freeze()
        demo = Path(__file__).parent / "fixtures" / "exports" / "daily_brief.demo.json"
        self.archives.mkdir()
        (self.archives / "demo.json").write_bytes(demo.read_bytes())
        result = self.review(at=FROZEN + timedelta(days=365))
        self.assertEqual(result["status"], "awaiting_archives")
        self.assertEqual(result["report"]["archive_inventory"]["skip_reasons"], {"synthetic_archive": 1})

    def test_one_over_nested_archive_does_not_abort_review_of_healthy_sibling(self):
        self.freeze()
        self.save("healthy.json", archive(FROZEN + timedelta(hours=1)))
        nested = b"[[[]]]"
        (self.archives / "nested.json").write_bytes(nested)

        def decode(raw):
            # The native decoder's recursion threshold varies by platform.
            if raw == nested:
                raise RecursionError
            return _decode(raw)

        with patch("ledger.monitor._decode", side_effect=decode):
            result = self.review()
        inventory = result["report"]["archive_inventory"]
        self.assertEqual(inventory["skip_reasons"], {"archive_nesting_too_deep": 1})
        self.assertEqual(inventory["accepted_files"], 1)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 1)

    def test_malformed_failed_stage_field_cannot_extend_the_observation_window(self):
        self.freeze()
        bad = archive(FROZEN + timedelta(hours=1))
        bad["health"]["sources"] = {"pipeline_stages": {"failed": {}}}
        self.save("bad-stages.json", bad)
        report = self.review()["report"]
        self.assertEqual(report["archive_inventory"]["accepted_files"], 0)
        self.assertEqual(report["observation_window"]["post_freeze_archive_dates"], 0)

    def test_previously_retained_excluded_archive_is_preserved_but_not_counted(self):
        from ledger.ingest import _run_evidence
        self.freeze()
        bad = archive(FROZEN + timedelta(hours=1))
        bad["health"]["sources"] = {"pipeline_stages": {"failed": {}}}
        source = self.save("legacy.json", bad)
        # The earlier inventory admitted this input, although the verifier
        # already excluded it. Reproduce that receipt without changing it.
        inventory_evidence = dict(_run_evidence(bad, bad["generated_at"], ""), failed_stage_shape_valid=True)
        with patch("ledger.monitor._run_evidence", return_value=inventory_evidence):
            first = self.review()
        self.assertEqual(first["report"]["archive_inventory"]["accepted_files"], 1)
        source.unlink()
        result = self.review()["report"]
        self.assertEqual(result["status"], "awaiting_archives")
        self.assertEqual(result["archive_inventory"]["retained_unique_archives"], 1)
        self.assertEqual(result["observation_window"]["post_freeze_archive_dates"], 0)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 2)

    def test_run_context_fixture_and_malformed_markers_are_excluded(self):
        self.freeze()
        for name, extra in (("context", {"run_context": {"synthetic": True}}),
                            ("fixture", {"is_fixture": True}),
                            ("malformed", {"synthetic": "yes"})):
            self.save(name + ".json", {**archive(FROZEN + timedelta(hours=1)), **extra})
        result = self.review()
        self.assertEqual(result["status"], "awaiting_archives")
        self.assertEqual(result["report"]["archive_inventory"]["skip_reasons"],
                         {"synthetic_archive": 2, "invalid_synthetic_marker": 1})

    def test_historical_archive_does_not_become_prospective_after_weeks(self):
        self.freeze()
        self.save("old.json", archive(FROZEN - timedelta(days=5)))
        result = self.review(at=FROZEN + timedelta(days=60))
        self.assertEqual(result["status"], "historical_only")
        window = result["report"]["observation_window"]
        self.assertTrue(window["minimum_window_elapsed"])
        self.assertEqual(window["post_freeze_archive_dates"], 0)
        self.assertFalse(window["statistical_power_established"])

    def test_receipts_append_and_both_report_formats_are_immutable(self):
        self.freeze()
        first = self.review()
        first_json = Path(first["paths"]["json"]).read_bytes()
        first_md = Path(first["paths"]["markdown"]).read_bytes()
        second = self.review(at=FROZEN + timedelta(days=2))
        self.assertEqual(first["receipt"]["receipt_sha256"], second["receipt"]["previous_receipt_sha256"])
        self.assertEqual(first_json, Path(first["paths"]["json"]).read_bytes())
        self.assertEqual(first_md, Path(first["paths"]["markdown"]).read_bytes())
        self.assertEqual(monitor_status(self.workspace)["review_count"], 2)

    def test_report_receipt_policy_and_retained_input_tampering_fail(self):
        for target in ("json", "markdown", "receipt", "policy", "input"):
            with self.subTest(target=target):
                original_workspace = self.workspace
                self.workspace = self.base / ("monitor-" + target)
                self.freeze()
                self.save("valid.json", archive(FROZEN + timedelta(hours=1)))
                result = self.review()
                if target == "policy":
                    path = self.workspace / "policy.json"
                elif target == "input":
                    path = next((self.workspace / "inputs").glob("*.json"))
                else:
                    path = Path(result["paths"][target])
                if target == "markdown":
                    path.write_text("modified", encoding="utf-8")
                else:
                    content = json.loads(path.read_text())
                    content["tampered"] = True
                    path.write_text(json.dumps(content), encoding="utf-8")
                with self.assertRaises((ValueError, KeyError)):
                    monitor_status(self.workspace)
                self.workspace = original_workspace

    def test_removed_tail_receipt_is_detected_by_head(self):
        self.freeze()
        result = self.review()
        Path(result["paths"]["receipt"]).unlink()
        with self.assertRaisesRegex(ValueError, "head"):
            monitor_status(self.workspace)

    def test_uncommitted_orphan_report_is_preserved_and_never_reused(self):
        self.freeze()
        orphan = self.workspace / "reports" / ("review-000001-" + "a" * 32 + ".json")
        orphan.write_text("partial crash output", encoding="utf-8")
        self.assertEqual(monitor_status(self.workspace)["orphan_reports"], [orphan.name])
        result = self.review()
        self.assertEqual(result["receipt"]["sequence"], 1)
        self.assertNotEqual(Path(result["paths"]["json"]), orphan)
        self.assertEqual(orphan.read_text(), "partial crash output")

    def test_head_update_crash_recovers_valid_committed_receipt_chain(self):
        self.freeze()
        with patch("ledger.monitor.atomic_write_json", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                self.review()
        status = monitor_status(self.workspace)
        self.assertEqual(status["review_count"], 1)
        self.assertTrue(status["head_recovery_pending"])
        self.review(at=FROZEN + timedelta(days=2))
        self.assertFalse(monitor_status(self.workspace)["head_recovery_pending"])

    def test_verifier_failure_is_a_durable_failed_review(self):
        self.freeze()
        with patch("ledger.monitor.build_verification", side_effect=RuntimeError("failure")):
            result = self.review()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["report"]["verification"])
        self.assertEqual(result["report"]["errors"], [{"stage": "verification", "error_type": "RuntimeError"}])
        self.assertEqual(monitor_status(self.workspace)["review_count"], 1)
        self.assertEqual(self.review(at=FROZEN + timedelta(days=2))["status"], "awaiting_archives")

    def test_receipt_crash_recovers_ingested_input_when_source_disappears(self):
        self.freeze()
        source = self.save("valid.json", archive(FROZEN + timedelta(hours=1)))
        from ledger.monitor import _write_once as actual_write
        def fail_receipt(path, payload):
            if path.parent.name == "receipts":
                raise OSError("crash before receipt")
            return actual_write(path, payload)
        with patch("ledger.monitor._write_once", side_effect=fail_receipt):
            with self.assertRaises(OSError):
                self.review()
        source.unlink()
        result = self.review(at=FROZEN + timedelta(days=2))
        self.assertEqual(result["receipt"]["sequence"], 1)
        self.assertEqual(result["report"]["archive_inventory"]["recovered_uncommitted_archives"], 1)
        self.assertEqual(monitor_status(self.workspace)["retained_archives"], 1)
        self.assertEqual(len(monitor_status(self.workspace)["orphan_reports"]), 2)

    def test_earlier_cutoff_after_ingest_crash_recovers_without_counting_later_archive(self):
        self.freeze()
        generated = FROZEN + timedelta(hours=2)
        source = self.save("later.json", archive(generated))
        # Verification starts after ingestion. Interrupt before any receipt exists.
        with patch("ledger.monitor.build_verification", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.review()
        source.unlink()
        earlier = self.review(as_of=(generated - timedelta(seconds=1)).isoformat())
        report = earlier["report"]
        self.assertEqual(earlier["receipt"]["sequence"], 1)
        self.assertEqual(earlier["status"], "awaiting_archives")
        self.assertEqual(report["verification"]["summary"]["selected_runs"], 0)
        self.assertEqual(report["observation_window"]["post_freeze_archive_dates"], 0)
        later = self.review(as_of=generated.isoformat())
        self.assertEqual(later["receipt"]["sequence"], 2)
        self.assertEqual(later["report"]["verification"]["summary"]["selected_runs"], 1)
        self.assertEqual(monitor_status(self.workspace)["retained_archives"], 1)

    def test_pre_ingest_crash_recovers_retained_input_without_source_or_database_row(self):
        self.freeze()
        source = self.save("valid.json", archive(FROZEN + timedelta(hours=1)))
        original = source.read_bytes()
        with patch("ledger.monitor.ingest_file", side_effect=KeyboardInterrupt("crash before ingestion")):
            with self.assertRaises(KeyboardInterrupt):
                self.review()
        retained = list((self.workspace / "inputs").glob("*.json"))
        self.assertEqual(len(retained), 1)
        self.assertEqual(retained[0].read_bytes(), original)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 0)
        source.unlink()

        result = self.review(at=FROZEN + timedelta(days=2))
        inventory = result["report"]["archive_inventory"]
        self.assertEqual(result["status"], "collecting")
        self.assertEqual(result["report"]["verification"]["summary"]["selected_runs"], 1)
        self.assertEqual(inventory["recovered_uncommitted_archives"], 1)
        self.assertEqual(inventory["retained_unique_archives"], 1)
        self.assertEqual(inventory["records"][0]["origin"], "recovered_uncommitted_input")
        self.assertEqual(inventory["records"][0]["file"], "inputs/" + retained[0].name)
        self.assertEqual(retained[0].read_bytes(), original)
        subsequent = self.review(at=FROZEN + timedelta(days=3))
        self.assertEqual(subsequent["report"]["archive_inventory"]["recovered_uncommitted_archives"], 0)
        self.assertEqual(monitor_status(self.workspace)["retained_archives"], 1)

    def test_uncommitted_recovery_excludes_invalid_hash_synthetic_and_future_inputs(self):
        self.freeze()
        invalid_identity = json.dumps(archive(FROZEN + timedelta(hours=1))).encode()
        (self.workspace / "inputs" / ("0" * 64 + ".json")).write_bytes(invalid_identity)
        synthetic = {**archive(FROZEN + timedelta(hours=1)), "is_fixture": True}
        future = archive(FROZEN + timedelta(days=20))
        for data in (synthetic, future):
            raw = json.dumps(data).encode()
            (self.workspace / "inputs" / (hashlib.sha256(raw).hexdigest() + ".json")).write_bytes(raw)

        result = self.review()
        inventory = result["report"]["archive_inventory"]
        self.assertEqual(result["status"], "awaiting_archives")
        self.assertEqual(result["report"]["verification"]["summary"]["selected_runs"], 0)
        self.assertEqual(inventory["recovered_uncommitted_archives"], 0)
        self.assertEqual(inventory["skipped_files"], 3)
        self.assertEqual(inventory["skip_reasons"], {"retained_archive_identity_mismatch": 1,
                                                   "synthetic_archive": 1, "future_archive": 1})
        self.assertTrue(all(row["origin"] == "recovered_uncommitted_input"
                            for row in inventory["records"]))
        self.assertEqual(len(list((self.workspace / "inputs").glob("*.json"))), 3)

    def test_changed_source_archive_is_excluded_and_original_bytes_retained(self):
        self.freeze()
        source = self.save("valid.json", archive(FROZEN + timedelta(hours=1)))
        first = self.review()
        source.write_text(json.dumps(archive(FROZEN + timedelta(hours=2))), encoding="utf-8")
        second = self.review(at=FROZEN + timedelta(days=2))
        self.assertEqual(second["report"]["archive_inventory"]["skip_reasons"],
                         {"archive_changed_after_capture": 1})
        self.assertEqual(monitor_status(self.workspace)["retained_archives"], 1)
        self.assertEqual(second["report"]["verification"]["selected_runs"],
                         first["report"]["verification"]["selected_runs"])

    def test_review_backdating_future_cutoff_and_mutation_are_refused(self):
        self.freeze()
        self.review(at=FROZEN + timedelta(days=2))
        for cutoff in (FROZEN - timedelta(seconds=1), FROZEN + timedelta(days=1), FROZEN + timedelta(days=9)):
            with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                self.review(at=FROZEN + timedelta(days=3), as_of=cutoff)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 1)

    def test_clock_rollback_between_reviews_is_rejected_before_writes(self):
        self.freeze()
        self.review(at=FROZEN + timedelta(days=10), as_of=FROZEN + timedelta(days=2))
        before = {str(path.relative_to(self.workspace)): path.read_bytes()
                  for path in self.workspace.rglob("*") if path.is_file() and path.name != ".monitor.lock"}
        with patch("ledger.monitor.connect", side_effect=AssertionError("must reject before database access")):
            with self.assertRaisesRegex(ValueError, "clock moved backward"):
                self.review(at=FROZEN + timedelta(days=5), as_of=FROZEN + timedelta(days=3))
        after = {str(path.relative_to(self.workspace)): path.read_bytes()
                 for path in self.workspace.rglob("*") if path.is_file() and path.name != ".monitor.lock"}
        self.assertEqual(before, after)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 1)
        result = self.review(at=FROZEN + timedelta(days=11), as_of=FROZEN + timedelta(days=3))
        self.assertEqual(result["receipt"]["sequence"], 2)
        self.assertEqual(monitor_status(self.workspace)["review_count"], 2)

    def test_runtime_relocation_preserves_policy_identity(self):
        frozen = self.freeze()
        relocated = self.base / "relocated"
        relocated.mkdir()
        (relocated / "new.json").write_text(json.dumps(archive(FROZEN + timedelta(hours=1))), encoding="utf-8")
        result = self.review(archive_dir=relocated)
        self.assertEqual(result["receipt"]["archive_dir"], str(relocated.resolve()))
        self.assertEqual(result["report"]["policy_sha256"], frozen["policy_sha256"])
        self.assertEqual(monitor_status(self.workspace)["archive_dir"], str(self.archives.resolve()))

    def test_fetch_is_opt_in_and_forwarded_without_backdating(self):
        self.freeze()
        from ledger.monitor import build_verification as real_build
        with patch("ledger.monitor.build_verification", wraps=real_build) as called:
            self.review(fetch=True)
        self.assertIs(called.call_args.kwargs["fetch"], True)
        self.assertIsNone(called.call_args.kwargs["as_of"])
        with self.assertRaises(ValueError):
            self.review(fetch="yes")

    def test_nonprivate_or_nonempty_workspace_is_rejected(self):
        repository = Path(__file__).parent
        with self.assertRaisesRegex(ValueError, "ignored private"):
            freeze_monitor(repository / "not-private-monitor", policy(), self.archives)
        self.workspace.mkdir()
        (self.workspace / "user-file").write_text("retain")
        with self.assertRaisesRegex(ValueError, "nonempty"):
            self.freeze()
        self.assertEqual((self.workspace / "user-file").read_text(), "retain")


if __name__ == "__main__":
    unittest.main()
