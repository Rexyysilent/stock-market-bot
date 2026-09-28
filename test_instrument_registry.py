"""T9 dated instrument registry: R01-R04, R06, R07 on synthetic data (no network)."""
import copy
import unittest

import coverage_policy
from config import EDITORIAL_ONLY_TICKERS, SIGNAL_ELIGIBLE_TICKERS
from instrument_registry import Registry, reconcile_listing_snapshot

EVIDENCE = ["synthetic:fixture"]


def listing(listing_id, instrument_id, symbol, *, valid_from=None, valid_to=None,
            known_from="2026-09-28T00:00:00Z", terminal=None, venue="XNAS",
            calendar="XNYS", currency="USD"):
    return {"listing_id": listing_id, "instrument_id": instrument_id, "venue": venue,
            "symbol": symbol, "calendar": calendar, "currency": currency, "price_scale": 1,
            "valid_from": valid_from, "valid_to": valid_to, "known_from": known_from,
            "terminal": terminal, "evidence": EVIDENCE, "verification": "snapshot_matched"}


def instrument(instrument_id, issuer_id, *, tier="instrumented", kind="common_stock",
               share_class=None):
    return {"instrument_id": instrument_id, "issuer_id": issuer_id, "type": kind,
            "share_class": share_class, "tier": tier, "evidence": EVIDENCE,
            "verification": "snapshot_matched"}


def issuer(issuer_id):
    return {"issuer_id": issuer_id, "legal_name": issuer_id, "cik": None,
            "evidence": EVIDENCE, "verification": "snapshot_matched"}


def registry(issuers=(), instruments=(), listings=(), capabilities=()):
    return Registry({"contract": "instrument-registry-1", "version": "synthetic-1",
                     "issuers": list(issuers), "instruments": list(instruments),
                     "listings": list(listings), "provider_bindings": [],
                     "capabilities": list(capabilities)})


class TickerReuseTests(unittest.TestCase):
    """R01: a reused symbol is a different instrument."""

    def setUp(self):
        delisted = {"state": "delisted", "asserted_at": "2024-06-30",
                    "evidence": ["synthetic:exchange-notice"]}
        self.reg = registry(
            [issuer("old-issuer"), issuer("new-issuer")],
            [instrument("inst:old", "old-issuer"), instrument("inst:new", "new-issuer")],
            [listing("l:old", "inst:old", "AAA", valid_from="2020-01-01", valid_to="2024-06-30",
                     terminal=delisted),
             listing("l:new", "inst:new", "AAA", valid_from="2025-01-01")])

    def test_as_of_date_selects_the_listing_valid_then(self):
        self.assertEqual(self.reg.resolve("AAA", on="2023-05-01")["instrument_id"], "inst:old")
        self.assertEqual(self.reg.resolve("AAA", on="2025-03-01")["instrument_id"], "inst:new")

    def test_missing_date_is_ambiguous_not_a_guess(self):
        result = self.reg.resolve("AAA")
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(sorted(result["candidates"]), ["inst:new", "inst:old"])
        self.assertIsNone(result["instrument_id"])

    def test_gap_between_listings_is_unknown(self):
        self.assertEqual(self.reg.resolve("AAA", on="2024-09-01")["status"], "unknown")

    def test_knowledge_time_hides_later_learned_listings(self):
        reg = registry([issuer("i")], [instrument("inst:x", "i")],
                       [listing("l:x", "inst:x", "XXX", valid_from="2020-01-01",
                                known_from="2026-09-28T00:00:00Z")])
        self.assertEqual(reg.resolve("XXX", on="2021-01-01")["status"], "resolved")
        strict = reg.resolve("XXX", on="2021-01-01", known_by="2026-01-01T00:00:00Z")
        self.assertEqual(strict["status"], "unknown")


class ShareClassRenameTests(unittest.TestCase):
    """R02: classes stay distinct; a rename keeps the class identity."""

    def setUp(self):
        self.reg = registry(
            [issuer("I1")],
            [instrument("inst:A", "I1", share_class="A"), instrument("inst:B", "I1", share_class="B")],
            [listing("l:A1", "inst:A", "AAA", valid_from="2020-01-01", valid_to="2025-06-01"),
             listing("l:A2", "inst:A", "AAB", valid_from="2025-06-01"),
             listing("l:B1", "inst:B", "AAC", valid_from="2020-01-01")])

    def test_rename_changes_the_binding_not_the_instrument(self):
        before = self.reg.resolve("AAA", on="2025-01-01")
        after = self.reg.resolve("AAB", on="2025-07-01")
        self.assertEqual(before["instrument_id"], after["instrument_id"])
        self.assertNotEqual(before["listing"]["listing_id"], after["listing"]["listing_id"])
        self.assertEqual(self.reg.resolve("AAA", on="2025-07-01")["status"], "unknown")

    def test_classes_share_an_issuer_but_never_join_prices(self):
        a, b = self.reg.instrument("inst:A"), self.reg.instrument("inst:B")
        self.assertEqual(a["issuer_id"], b["issuer_id"])
        self.assertFalse(self.reg.price_join_allowed("inst:A", "inst:B"))
        self.assertTrue(self.reg.price_join_allowed("inst:A", "inst:A"))


class AbsenceTests(unittest.TestCase):
    """R03: absence or a failed fetch is not delisting."""

    def test_failed_fetch_and_missing_ticker_are_not_terminal(self):
        reg = registry([issuer("i")], [instrument("inst:x", "i")],
                       [listing("l:x", "inst:x", "XXX", valid_from="2020-01-01")],
                       [{"instrument_id": "inst:x", "provider": "yfinance", "endpoint": "prices",
                         "status": "unavailable", "checked_at": "2026-09-28T11:31:00Z",
                         "cause": "fetch_failed"}])
        state = reg.listing_state("inst:x", on="2026-09-28")
        self.assertEqual(state["state"], "listed_as_last_known")
        self.assertNotIn("delist", json_text(state))
        capability = reg.capability("inst:x", "yfinance", "prices")
        self.assertEqual(capability["status"], "unavailable")

    def test_terminal_state_needs_a_dated_source(self):
        bad = listing("l:x", "inst:x", "XXX", valid_from="2020-01-01", valid_to="2024-01-01",
                      terminal={"state": "delisted", "asserted_at": None, "evidence": []})
        with self.assertRaisesRegex(ValueError, "dated source"):
            registry([issuer("i")], [instrument("inst:x", "i")], [bad])
        good = copy.deepcopy(bad)
        good["terminal"] = {"state": "delisted", "asserted_at": "2024-01-02",
                            "evidence": ["synthetic:notice"]}
        reg = registry([issuer("i")], [instrument("inst:x", "i")], [good])
        self.assertEqual(reg.listing_state("inst:x", on="2024-06-01")["state"], "delisted")

    def test_capability_statuses_are_closed(self):
        with self.assertRaisesRegex(ValueError, "capability status"):
            registry([issuer("i")], [instrument("inst:x", "i")], [],
                     [{"instrument_id": "inst:x", "provider": "p", "endpoint": "e",
                       "status": "probably_fine", "checked_at": None, "cause": None}])


class CalendarCurrencyTests(unittest.TestCase):
    """R04: no unqualified cross-calendar or cross-currency comparison."""

    def setUp(self):
        self.reg = registry(
            [issuer("us"), issuer("jp")],
            [instrument("inst:us", "us"), instrument("inst:jp", "jp")],
            [listing("l:us", "inst:us", "USA", valid_from="2020-01-01"),
             listing("l:jp", "inst:jp", "7203", valid_from="2020-01-01", venue="XTKS",
                     calendar="XTKS", currency="JPY")])

    def test_unqualified_comparison_is_refused(self):
        result = self.reg.comparison("inst:us", "inst:jp", on="2026-09-28")
        self.assertFalse(result["allowed"])
        self.assertEqual(set(result["reasons"]), {"calendar_mismatch", "currency_mismatch"})

    def test_explicit_alignment_and_dated_fx_allow_it(self):
        fx = {"pair": "USDJPY", "as_of": "2026-09-28", "source": "synthetic"}
        result = self.reg.comparison("inst:us", "inst:jp", on="2026-09-28",
                                     session_alignment="close_to_close_utc", fx_convention=fx)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["fx_convention"], fx)
        undated = self.reg.comparison("inst:us", "inst:jp", on="2026-09-28",
                                      session_alignment="close_to_close_utc",
                                      fx_convention={"pair": "USDJPY"})
        self.assertFalse(undated["allowed"])

    def test_native_currency_return_needs_no_fx(self):
        native = self.reg.native_return_basis("inst:jp", on="2026-09-28")
        self.assertEqual((native["allowed"], native["currency"], native["calendar"]),
                         (True, "JPY", "XTKS"))


class SnapshotTests(unittest.TestCase):
    """R06: an incomplete directory snapshot infers nothing."""

    def setUp(self):
        self.reg = registry([issuer("i")],
                            [instrument("inst:x", "i"), instrument("inst:y", "i")],
                            [listing("l:x", "inst:x", "XXX", valid_from="2020-01-01"),
                             listing("l:y", "inst:y", "YYY", valid_from="2020-01-01")])

    def test_incomplete_snapshot_is_marked_and_infers_no_delisting(self):
        report = reconcile_listing_snapshot(self.reg, {
            "source": "synthetic-directory", "captured_at": "2026-09-28T00:00:00Z",
            "expected_pages": 3, "successful_pages": 2, "symbols": ["XXX", "NEWCO"]})
        self.assertFalse(report["complete"])
        self.assertEqual(report["inferred_delistings"], [])
        self.assertEqual(report["activations"], [])
        self.assertEqual(report["registry_symbols"]["YYY"], "unobserved_incomplete_snapshot")
        self.assertEqual(report["unregistered_symbols"], ["NEWCO"])

    def test_even_a_complete_snapshot_only_marks_absence(self):
        report = reconcile_listing_snapshot(self.reg, {
            "source": "synthetic-directory", "captured_at": "2026-09-28T00:00:00Z",
            "expected_pages": 1, "successful_pages": 1, "symbols": ["XXX"]})
        self.assertTrue(report["complete"])
        self.assertEqual(report["registry_symbols"]["YYY"], "unobserved")
        self.assertEqual(report["inferred_delistings"], [])


class SeedAndIsolationTests(unittest.TestCase):
    """R07 and the protected cohort: candidates never change the frozen core."""

    def test_seed_covers_the_protected_cohort_without_upgrading_identity(self):
        reg = Registry.from_config()
        self.assertEqual(reg.tier_symbols("instrumented"), list(SIGNAL_ELIGIBLE_TICKERS))
        self.assertEqual(reg.tier_symbols("editorial_only"), list(EDITORIAL_ONLY_TICKERS))
        mrk = reg.resolve("MRK", on="2026-09-28")
        self.assertEqual(reg.instrument(mrk["instrument_id"])["verification"], "primary_verified")
        tsla = reg.instrument(reg.resolve("TSLA", on="2026-09-28")["instrument_id"])
        self.assertEqual(tsla["verification"], "unverified")
        self.assertEqual(reg.instrument(reg.resolve("^VIX", on="2026-09-28")["instrument_id"])["type"],
                         "index")
        self.assertIsNone(reg.resolve("TSLA", on="2026-09-28")["listing"]["valid_from"])

    def test_catalogue_candidates_leave_core_outputs_unchanged(self):
        before = coverage_policy.coverage_policy_manifest("shadow")
        base = Registry.from_config()
        extended = base.with_candidates([
            {"symbol": f"CND{i}", "legal_name": f"Candidate {i}", "venue": "XNAS",
             "evidence": ["synthetic:directory"]} for i in range(5)])
        self.assertEqual(extended.tier_symbols("catalogue_candidate"),
                         [f"CND{i}" for i in range(5)])
        self.assertEqual(extended.tier_symbols("instrumented"), base.tier_symbols("instrumented"))
        self.assertEqual(extended.tier_symbols("editorial_only"), base.tier_symbols("editorial_only"))
        self.assertEqual(coverage_policy.coverage_policy_manifest("shadow"), before)
        self.assertNotEqual(extended.fingerprint(), base.fingerprint())
        with self.assertRaisesRegex(ValueError, "catalogue"):
            base.with_candidates([{"symbol": "TSLA", "legal_name": "x", "venue": "XNAS",
                                   "evidence": ["x"]}])

    def test_committed_registry_matches_the_configured_cohorts(self):
        from instrument_registry import DEFAULT_PATH, load_registry
        self.assertTrue(DEFAULT_PATH.is_file())
        reg = load_registry()
        self.assertEqual(reg.tier_symbols("instrumented"), list(SIGNAL_ELIGIBLE_TICKERS))
        self.assertEqual(reg.tier_symbols("editorial_only"), list(EDITORIAL_ONLY_TICKERS))
        self.assertTrue(reg.snapshots)
        mrk = reg.instrument(reg.resolve("MRK", on="2026-09-28")["instrument_id"])
        self.assertEqual(mrk["verification"], "primary_verified")

    def test_a_registry_that_drifted_from_config_is_refused(self):
        import json
        import tempfile
        from pathlib import Path
        from instrument_registry import load_registry
        data = Registry.from_config().to_dict()
        data["instruments"] = [i for i in data["instruments"] if i["instrument_id"] != "seed1:TSLA"]
        data["listings"] = [r for r in data["listings"] if r["instrument_id"] != "seed1:TSLA"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "regenerate"):
                load_registry(path)
            self.assertEqual(load_registry(Path(tmp) / "missing.json").version,
                             Registry.from_config().version)

    def test_registry_file_round_trips_with_a_stable_fingerprint(self):
        reg = Registry.from_config()
        again = Registry(reg.to_dict())
        self.assertEqual(again.fingerprint(), reg.fingerprint())


def json_text(value):
    import json
    return json.dumps(value)


if __name__ == "__main__":
    unittest.main()
