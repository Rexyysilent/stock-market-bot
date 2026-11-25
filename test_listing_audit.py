"""Current-directory coverage is separate from market-wide historical recall."""
import json
import unittest

from listing_audit import build_listing_audit
from registry_snapshot import PARTS, enrich
from test_registry_snapshot import NASDAQ, OTHER, SnapshotCase, seed

AS_OF = "2026-09-28T12:00:00Z"


class RegistryAuditTests(unittest.TestCase):
    def test_missing_snapshot_and_unobserved_capabilities_are_unknown(self):
        report = build_listing_audit(seed(), as_of=AS_OF)
        self.assertEqual(report["registry"]["instruments"], 8)
        self.assertEqual(report["registry"]["by_verification"], {"unverified": 8})
        self.assertEqual(report["registry"]["capabilities"]["records"], 0)
        self.assertEqual(report["registry"]["capabilities"]["instruments_without_dated_observation"], 8)
        self.assertEqual(report["registry"]["unknown_listing_fields"]["valid_from"], 8)
        self.assertIsNone(report["coverage"]["denominator"])
        self.assertFalse(report["historical_market_recall"]["available"])

    def test_future_capability_observation_is_not_counted_as_known(self):
        registry = seed().to_dict()
        registry["capabilities"] = [{"instrument_id": "seed1:ALFA", "provider": "fixture",
                                     "endpoint": "prices", "status": "supported",
                                     "checked_at": "2099-01-01T00:00:00Z"}]
        report = build_listing_audit(registry, as_of=AS_OF)
        self.assertEqual(report["registry"]["capabilities"]["instruments_without_dated_observation"], 8)


class DirectoryAuditTests(SnapshotCase):
    def audit(self, **kwargs):
        return build_listing_audit(seed(), self.dir, as_of=kwargs.pop("as_of", AS_OF), **kwargs)

    def test_current_denominator_excludes_test_issues_and_counts_uncovered(self):
        nasdaq = NASDAQ.replace("File Creation Time", "NEWW|New Issuer Common Stock|Q|N|N|100|N|N\nFile Creation Time")
        self.captured(nasdaq=nasdaq)
        report = self.audit()
        coverage = report["coverage"]
        self.assertEqual(coverage["raw_directory_rows"], 7)
        self.assertEqual(coverage["denominator"], 6)
        self.assertEqual(coverage["covered_by_tier"]["instrumented"]["count"], 5)
        self.assertAlmostEqual(coverage["instrumented_fraction"], 5 / 6)
        self.assertEqual(coverage["uncovered_symbol_sample"], ["NEWW"])
        self.assertEqual(coverage["configured_exclusions"]["test_issue"], ["ZTST"])
        self.assertEqual(report["snapshot"]["status"], "complete")
        self.assertEqual(report["activations"], [])
        self.assertEqual(report["inferred_delistings"], [])

    def test_directory_only_is_complete_for_directory_but_not_full_identity(self):
        self.captured(directory_only=True)
        report = self.audit()
        self.assertTrue(report["snapshot"]["directory_complete"])
        self.assertFalse(report["snapshot"]["full_snapshot_complete"])
        self.assertTrue(report["snapshot"]["requested_complete"])
        self.assertEqual(report["snapshot"]["parts"]["sec_company_tickers_exchange"]["status"],
                         "not_requested")
        self.assertEqual(report["coverage"]["denominator"], 5)

    def test_partial_capture_never_reports_complete_population_fraction(self):
        self.captured(fail="nasdaq_other_listed")
        report = self.audit()
        self.assertEqual(report["snapshot"]["status"], "partial")
        self.assertIsNone(report["coverage"]["denominator"])
        self.assertIsNone(report["coverage"]["instrumented_fraction"])
        self.assertEqual(report["coverage"]["observed_eligible_subset"], 2)

    def test_changed_raw_bytes_are_excluded_after_hash_verification(self):
        self.captured()
        path = self.dir / PARTS["nasdaq_listed"]["file"]
        path.write_text(NASDAQ.replace("Alpha", "Omega"), encoding="utf-8")
        report = self.audit()
        self.assertEqual(report["snapshot"]["parts"]["nasdaq_listed"]["status"], "integrity_failed")
        self.assertIsNone(report["coverage"]["denominator"])
        self.assertEqual(report["coverage"]["observed_eligible_subset"], 3)

    def test_future_capture_cannot_supply_an_earlier_denominator(self):
        self.captured(now="2026-09-29T10:30:00Z")
        report = self.audit()
        self.assertEqual(report["snapshot"]["status"], "future_snapshot")
        self.assertIsNone(report["coverage"]["denominator"])
        self.assertEqual(report["coverage"]["reason"], "snapshot_not_known_at_audit_cutoff")

    def test_upstream_file_date_is_not_refreshed_by_a_later_download(self):
        self.captured(now="2026-11-25T10:30:00Z")
        report = self.audit(as_of="2026-11-25T12:00:00Z")
        self.assertEqual(report["snapshot"]["freshness"], "recent")
        self.assertEqual(report["snapshot"]["status"], "stale")
        self.assertGreater(report["snapshot"]["upstream_creation"]["nasdaq_listed"]["age_calendar_days"], 30)

    def test_future_upstream_file_date_cannot_establish_coverage(self):
        self.captured(nasdaq=NASDAQ.replace("0928202610:01", "0101209910:01"))
        report = self.audit()
        self.assertEqual(report["snapshot"]["status"], "future_source_date")
        self.assertIsNone(report["coverage"]["denominator"])

    def test_security_classes_and_duplicates_are_not_silently_counted_as_equities(self):
        extra = ("WRNT|Example Warrants to buy Common Stock|Q|N|N|100|N|N\n"
                 "UNIT|Example Units each containing Common Stock|Q|N|N|100|N|N\n"
                 "MYST|Example Unclassified Instrument|Q|N|N|100|N|N\n"
                 "PREF|Example Preferred Stock|Q|N|N|100|N|N\n"
                 "ALFA|Another Issuer Common Stock|Q|N|N|100|N|N\n")
        self.captured(nasdaq=NASDAQ.replace("File Creation Time", extra + "File Creation Time"))
        coverage = self.audit()["coverage"]
        self.assertEqual(coverage["denominator"], 4)
        self.assertEqual(coverage["ambiguous_directory_symbols"], ["ALFA"])
        exclusions = coverage["exclusion_counts_by_reason"]
        self.assertEqual(exclusions["ambiguous_duplicate_symbol"], 2)
        self.assertEqual(exclusions["security_class_warrant"], 1)
        self.assertEqual(exclusions["security_class_unit"], 1)
        self.assertEqual(exclusions["security_class_unknown"], 1)
        self.assertEqual(exclusions["security_class_preferred_stock"], 1)

    def test_financial_condition_flags_do_not_create_survivor_filter(self):
        self.captured(nasdaq=NASDAQ.replace("|Q|N|N|100|", "|Q|N|Q|100|"))
        coverage = self.audit()["coverage"]
        self.assertEqual(coverage["denominator"], 5)
        self.assertEqual(coverage["eligible_financial_status_flags"]["Q"], 2)

    def test_malformed_or_unterminated_parts_cannot_establish_full_coverage(self):
        self.captured(nasdaq=NASDAQ.replace("File Creation Time", "BROKEN|Too Few Fields\nFile Creation Time"))
        report = self.audit()
        self.assertFalse(report["snapshot"]["directory_complete"])
        self.assertEqual(report["snapshot"]["directory_parse"]["nasdaq_listed"]["malformed_rows"], 1)
        self.assertIsNone(report["coverage"]["denominator"])
        self.assertEqual(report["coverage"]["observed_eligible_subset"], 3)

    def test_registry_and_snapshot_bytes_are_not_modified_by_audit(self):
        self.captured()
        registry, _ = enrich(seed(), self.dir)
        before_registry = registry.fingerprint()
        before_files = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        report = build_listing_audit(registry, self.dir, as_of="2026-11-25T12:00:00Z")
        self.assertEqual(registry.fingerprint(), before_registry)
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir()}, before_files)
        self.assertTrue(all(s["freshness"] == "stale" for s in report["registry"]["latest_captures"]))


if __name__ == "__main__":
    unittest.main()
