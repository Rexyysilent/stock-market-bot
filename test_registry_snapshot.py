"""T9 approved-snapshot enrichment from SEC and Nasdaq Trader directories (synthetic)."""
import io
import json
from pathlib import Path
import tempfile
import unittest

from instrument_registry import CONTRACT, Registry
from registry_snapshot import PARTS, capture, enrich

SEC = {"fields": ["cik", "name", "ticker", "exchange"], "data": [
    [1001, "ALPHA HOLDINGS CO", "ALFA", "Nasdaq"],
    [1002, "Gamma Corp", "GAMA", "NYSE"],
    [1003, "Delta Mining Corp.", "DLTA", "NYSE"],
    [1004, "Totally Different Inc", "MISM", "Nasdaq"],
]}
NASDAQ = ("Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
          "ALFA|Alpha Holdings, LLC - Class A Common Stock|Q|N|N|100|N|N\n"
          "MISM|Mismatch Therapeutics - Common Stock|Q|N|N|100|N|N\n"
          "ZTST|Test Issue Corp - Common Stock|Q|Y|N|100|N|N\n"
          "File Creation Time: 0928202610:01|||||||\n")
OTHER = ("ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
         "GAMA|Gamma Corporation American Depositary Shares|N|GAMA|N|100|N|GAMA\n"
         "DLTA|Delta Mining Corp Ordinary Shares (Canada)|A|DLTA|N|100|N|DLTA\n"
         "FUND|Example Uranium ETF|P|FUND|Y|100|N|FUND\n"
         "File Creation Time: 0928202610:01||||||\n")


def seed():
    def entry(symbol, tier="instrumented", kind="unknown", verification="unverified"):
        return ({"instrument_id": f"seed1:{symbol}", "issuer_id": None, "type": kind,
                 "share_class": None, "tier": tier, "evidence": ["config"],
                 "verification": verification},
                {"listing_id": f"seed1:{symbol}", "instrument_id": f"seed1:{symbol}", "venue": None,
                 "symbol": symbol, "calendar": None, "currency": None, "price_scale": None,
                 "valid_from": None, "valid_to": None, "known_from": "2026-09-28T00:00:00Z",
                 "terminal": None, "evidence": ["config"], "verification": verification})
    rows = [entry(s) for s in ("ALFA", "GAMA", "DLTA", "FUND", "MISM", "ZTST", "OTCX")]
    rows.append(entry("VIXX", kind="index"))
    return Registry({"contract": CONTRACT, "version": "seed", "issuers": [],
                     "instruments": [r[0] for r in rows], "listings": [r[1] for r in rows],
                     "provider_bindings": [], "capabilities": []})


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class SnapshotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def captured(self, fail=None, sec=None, nasdaq=None, other=None, *, out=None,
                 now="2026-09-28T10:30:00Z", directory_only=False):
        bodies = {"sec_company_tickers_exchange": json.dumps(sec or SEC).encode(),
                  "nasdaq_listed": (nasdaq or NASDAQ).encode(),
                  "nasdaq_other_listed": (other or OTHER).encode()}
        calls = []

        def opener(request, timeout):
            calls.append(request)
            part = next(p for p in PARTS if PARTS[p]["url"] == request.full_url)
            if part == fail:
                raise OSError("synthetic network failure")
            return FakeResponse(bodies[part])
        manifest = capture(out or self.dir,
                           None if directory_only else "Example research contact@example.org",
                           opener=opener, now=now, directory_only=directory_only)
        return manifest, calls


class CaptureTests(SnapshotCase):
    def test_three_bounded_requests_with_contact_user_agent(self):
        manifest, calls = self.captured()
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(c.get_header("User-agent") for c in calls))
        self.assertTrue(manifest["complete"])
        self.assertEqual({p["status"] for p in manifest["parts"].values()}, {"captured"})
        self.assertEqual(len(manifest["parts"]["nasdaq_listed"]["sha256"]), 64)

    def test_a_failed_part_is_recorded_not_raised(self):
        manifest, _ = self.captured(fail="nasdaq_other_listed")
        self.assertFalse(manifest["complete"])
        self.assertEqual(manifest["parts"]["nasdaq_other_listed"]["status"], "failed")

    def test_capture_requires_a_contact_user_agent(self):
        with self.assertRaisesRegex(ValueError, "contact"):
            capture(self.dir, "no-contact", opener=lambda r, timeout: None)

    def test_default_placeholder_sec_contact_is_refused(self):
        with self.assertRaisesRegex(ValueError, "contact"):
            capture(self.dir, "StockMarketBot contact@example.com",
                    opener=lambda r, timeout: self.fail("no request should run"))

    def test_directory_only_needs_no_sec_contact_and_reports_its_scope(self):
        manifest, calls = self.captured(directory_only=True)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("nasdaqtrader.com" in c.full_url for c in calls))
        self.assertFalse(manifest["complete"])
        self.assertTrue(manifest["directory_complete"])
        self.assertTrue(manifest["requested_complete"])
        self.assertEqual(manifest["parts"]["sec_company_tickers_exchange"]["status"],
                         "not_requested")

    def test_existing_snapshot_is_refused_without_requests_or_byte_changes(self):
        self.captured()
        before = {p.name: p.read_bytes() for p in self.dir.iterdir()}
        with self.assertRaisesRegex(FileExistsError, "already exist"):
            capture(self.dir, "Example contact@example.org",
                    opener=lambda r, timeout: self.fail("no request should run"))
        self.assertEqual({p.name: p.read_bytes() for p in self.dir.iterdir()}, before)

    def test_part_without_manifest_also_prevents_reuse(self):
        path = self.dir / PARTS["nasdaq_listed"]["file"]
        path.write_bytes(b"earlier acquisition")
        with self.assertRaises(FileExistsError):
            capture(self.dir, directory_only=True,
                    opener=lambda r, timeout: self.fail("no request should run"))
        self.assertEqual(path.read_bytes(), b"earlier acquisition")
        self.assertFalse((self.dir / "manifest.json").exists())


class EnrichTests(SnapshotCase):
    def enriched(self, fail=None):
        self.captured(fail=fail)
        return enrich(seed(), self.dir)

    def test_common_stock_with_class_and_venue_is_snapshot_matched(self):
        reg, report = self.enriched()
        alfa = reg.resolve("ALFA", on="2026-09-28")
        inst = reg.instrument(alfa["instrument_id"])
        self.assertEqual(alfa["instrument_id"], "seed1:ALFA")  # identity kept
        self.assertEqual((inst["type"], inst["share_class"], inst["verification"]),
                         ("common_stock", "A", "snapshot_matched"))
        listing = alfa["listing"]
        self.assertEqual((listing["venue"], listing["calendar"], listing["currency"]),
                         ("XNAS", "XNYS", "USD"))
        self.assertEqual(listing["known_from"], "2026-09-28T10:30:00Z")
        self.assertEqual(reg.issuers[alfa["issuer_id"]]["cik"], "0000001001")
        self.assertTrue(any(e.startswith("snapshot:sec_company_tickers_exchange@")
                            for e in listing["evidence"]))
        self.assertIn("ALFA", report["matched"])

    def test_adr_nyse_american_and_etf_types(self):
        reg, _ = self.enriched()
        gama = reg.instrument("seed1:GAMA")
        self.assertEqual(gama["type"], "adr")
        self.assertEqual(reg.resolve("DLTA", on="2026-09-28")["listing"]["venue"], "XASE")
        fund = reg.instrument("seed1:FUND")
        self.assertEqual((fund["type"], fund["issuer_id"], fund["verification"]),
                         ("exchange_traded_product", None, "snapshot_matched"))
        self.assertEqual(reg.resolve("FUND", on="2026-09-28")["listing"]["venue"], "ARCX")

    def test_name_conflict_test_issue_and_absence_change_nothing(self):
        reg, report = self.enriched()
        for symbol in ("MISM", "ZTST", "OTCX", "VIXX"):
            self.assertEqual(reg.instrument(f"seed1:{symbol}"),
                             seed().instrument(f"seed1:{symbol}"), symbol)
        self.assertIn("MISM", report["conflicts"])
        self.assertEqual(report["not_in_directory"], ["OTCX", "VIXX", "ZTST"])
        self.assertEqual(report["inferred_delistings"], [])
        self.assertEqual(reg.listing_state("seed1:OTCX", on="2026-09-28")["state"],
                         "listed_as_last_known")

    def test_operating_companies_need_both_sources(self):
        reg, report = self.enriched(fail="sec_company_tickers_exchange")
        self.assertFalse(report["snapshot_complete"])
        self.assertEqual(reg.instrument("seed1:ALFA")["verification"], "unverified")
        self.assertEqual(reg.instrument("seed1:FUND")["verification"], "snapshot_matched")

    def test_primary_verified_is_never_downgraded_and_snapshots_are_recorded(self):
        base = seed().to_dict()
        base["instruments"][0]["verification"] = "primary_verified"
        self.captured()
        reg, _ = enrich(Registry(base), self.dir)
        self.assertEqual(reg.instrument("seed1:ALFA")["verification"], "primary_verified")
        self.assertEqual(len(reg.to_dict()["snapshots"]), 3)
        self.assertNotEqual(reg.fingerprint(), Registry(base).fingerprint())


class ReviewFixTests(SnapshotCase):
    """Code review of T9 (2026-09-28)."""

    def test_enrich_write_builds_on_the_committed_registry(self):
        # Finding 1: history already in the file must survive a refresh.
        from registry_snapshot import main
        base = seed().to_dict()
        base["instruments"].append({"instrument_id": "inst:gone", "issuer_id": None,
                                    "type": "common_stock", "share_class": None,
                                    "tier": "instrumented", "evidence": ["x"],
                                    "verification": "snapshot_matched"})
        base["listings"].append({"listing_id": "l:gone", "instrument_id": "inst:gone",
                                 "venue": "XNAS", "symbol": "GONE", "calendar": "XNYS",
                                 "currency": "USD", "price_scale": 1, "valid_from": "2020-01-01",
                                 "valid_to": "2024-01-01", "known_from": "2026-09-01T00:00:00Z",
                                 "terminal": {"state": "delisted", "asserted_at": "2024-01-02",
                                              "evidence": ["notice"]},
                                 "evidence": ["x"], "verification": "snapshot_matched"})
        base["snapshots"] = [{"part": "earlier", "sha256": "0" * 64}]
        target = self.dir / "registry.json"
        target.write_text(json.dumps(base), encoding="utf-8")
        self.captured()
        main(["enrich", str(self.dir), "--write", str(target), "--no-config-check"])
        written = Registry(json.loads(target.read_text(encoding="utf-8")))
        self.assertEqual(written.listing_state("inst:gone", on="2025-01-01")["state"], "delisted")
        self.assertEqual([s["part"] for s in written.snapshots][:1], ["earlier"])
        self.assertEqual(len(written.snapshots), 4)
        self.assertEqual(written.instrument("seed1:ALFA")["verification"], "snapshot_matched")

    def test_unknown_type_keeps_the_existing_type_and_unmapped_venue_changes_nothing(self):
        # Finding 2.
        base = seed().to_dict()
        for item in base["instruments"]:
            if item["instrument_id"] in ("seed1:ALFA", "seed1:DLTA"):
                item["type"] = "common_stock"
        nasdaq = NASDAQ.replace("Alpha Holdings, LLC - Class A Common Stock", "Alpha Holdings Units")
        other = OTHER.replace("|Delta Mining Corp Ordinary Shares (Canada)|A|",
                              "|Delta Mining Corp Ordinary Shares (Canada)|Q|")
        self.captured(nasdaq=nasdaq, other=other)
        reg, report = enrich(Registry(base), self.dir)
        self.assertEqual(reg.instrument("seed1:ALFA")["type"], "common_stock")
        self.assertEqual(report["conflicts"]["DLTA"], "unmapped_venue")
        listing = reg.resolve("DLTA", on="2026-09-28")["listing"]
        self.assertEqual((listing["venue"], listing["calendar"], listing["currency"]),
                         (None, None, None))

    def test_issuer_names_must_agree_beyond_the_first_word(self):
        # Finding 3: a symbol moving between "American ..." issuers is a conflict.
        sec = {"fields": SEC["fields"], "data": [[2001, "American Express Co", "AMRX", "NYSE"]]}
        other = OTHER.replace(
            "File Creation Time",
            "AMRX|American Airlines Group Inc. Common Stock|N|AMRX|N|100|N|AMRX\nFile Creation Time")
        base = seed().to_dict()
        base["instruments"].append({"instrument_id": "seed1:AMRX", "issuer_id": None,
                                    "type": "unknown", "share_class": None, "tier": "instrumented",
                                    "evidence": ["config"], "verification": "unverified"})
        base["listings"].append(dict(base["listings"][0], listing_id="seed1:AMRX",
                                     instrument_id="seed1:AMRX", symbol="AMRX"))
        self.captured(sec=sec, other=other)
        _, report = enrich(Registry(base), self.dir)
        self.assertEqual(report["conflicts"]["AMRX"], "issuer_name_disagrees")

    def test_evidence_names_only_the_sources_consulted(self):
        # Finding 4.
        self.captured()
        reg, _ = enrich(seed(), self.dir)

        def parts(symbol):
            return {e.split("@")[0] for e in reg.resolve(symbol, on="2026-09-28")["listing"]["evidence"]
                    if e.startswith("snapshot:")}
        self.assertEqual(parts("ALFA"), {"snapshot:sec_company_tickers_exchange",
                                         "snapshot:nasdaq_listed"})
        self.assertEqual(parts("FUND"), {"snapshot:nasdaq_other_listed"})


class SnapshotHistoryTests(SnapshotCase):
    def test_enrichment_does_not_mutate_the_supplied_registry(self):
        self.captured()
        base = seed()
        before = json.dumps(base.to_dict(), sort_keys=True)
        enriched, _ = enrich(base, self.dir)
        self.assertEqual(json.dumps(base.to_dict(), sort_keys=True), before)
        self.assertNotEqual(enriched.fingerprint(), base.fingerprint())

    def test_identical_refresh_preserves_earlier_known_by_resolution(self):
        self.captured()
        first, _ = enrich(seed(), self.dir)
        previous = first.resolve("ALFA", on="2026-09-28", known_by="2026-09-28T11:00:00Z")
        later = self.dir / "later"
        self.captured(out=later, now="2026-09-29T10:30:00Z")
        second, report = enrich(first, later)
        self.assertEqual(second.resolve("ALFA", on="2026-09-28", known_by="2026-09-28T11:00:00Z"),
                         previous)
        self.assertIn("ALFA", report["reconfirmed"])
        self.assertEqual(len(second.snapshots), 6)

    def test_new_listing_facts_are_not_backdated(self):
        self.captured()
        enriched, _ = enrich(seed(), self.dir)
        self.assertEqual(enriched.resolve("ALFA", on="2026-09-28",
                                         known_by="2026-09-28T09:00:00Z")["status"], "unknown")
        self.assertEqual(enriched.resolve("ALFA")["listing"]["known_from"], "2026-09-28T10:30:00Z")

    def test_ambiguous_directory_symbol_never_overwrites_another_row(self):
        duplicate = NASDAQ.replace("File Creation Time", "ALFA|Other Issuer Common Stock|Q|N|N|100|N|N\nFile Creation Time")
        self.captured(nasdaq=duplicate)
        enriched, report = enrich(seed(), self.dir)
        self.assertEqual(report["conflicts"]["ALFA"], "ambiguous_directory_symbol")
        self.assertEqual(enriched.instrument("seed1:ALFA"), seed().instrument("seed1:ALFA"))

    def test_bad_directory_shape_is_partial_and_cannot_upgrade_members(self):
        self.captured(nasdaq=NASDAQ.split("File Creation Time")[0])
        enriched, report = enrich(seed(), self.dir)
        self.assertFalse(report["directory_complete"])
        self.assertEqual(enriched.instrument("seed1:ALFA")["verification"], "unverified")

if __name__ == "__main__":
    unittest.main()
