"""OMNI-03: difficult aliases, distinct trials, knowledge time and no promotion."""
import copy
import unittest

from omni.identities import EntityRegistry, load_registry, time_interval

NOW = "2026-10-18T12:00:00Z"


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_registry()

    def resolve(self, text, **kwargs):
        return self.registry.resolve(text, known_by=NOW, **kwargs)

    def ids(self, text):
        return {m["entity_id"] for m in self.resolve(text)["mentions"]}

    def test_t04_outside_cohort_asset_and_partners_remain_review_data(self):
        result = self.resolve("INTerpath-001 evaluated V940 with KEYTRUDA")
        self.assertIn("trial:NCT05933577", {m["entity_id"] for m in result["mentions"]})
        self.assertEqual({r["from_id"] for r in result["relationships"] if r["role"] == "jointly_develops"},
                         {"cik:0001682852", "cik:0000310158"})
        self.assertFalse(result["signal_eligible"])
        self.assertTrue(all(m["role"] == "mentioned" for m in result["mentions"]))

    def test_t09_vendor_tags_do_not_establish_subject(self):
        result = self.resolve("General Motors reports earnings", related_symbols=("GM", "F", "TSLA"))
        self.assertNotIn("cik:0001318605", {m["entity_id"] for m in result["mentions"]})
        self.assertEqual(result["related_symbol_hints"], ["F", "GM", "TSLA"])

    def test_t10_generic_words_and_unknown_issuer(self):
        for text in ("beam rare mRNA MP hood LEU", "An unknown biotechnology company reports a trial"):
            self.assertEqual(self.resolve(text)["mentions"], [])
            self.assertEqual(self.resolve(text)["status"], "review_required")
        self.assertIn("cik:0001745999", self.ids("NASDAQ: BEAM reports an update"))

    def test_t11_legal_mercks_distinct_and_generic_ambiguous(self):
        self.assertEqual(self.ids("Merck KGaA"), {"issuer:merck-kgaa-de"})
        self.assertEqual(self.ids("Merck & Co"), {"cik:0000310158"})
        self.assertEqual(self.ids("NYSE:MRK"), {"cik:0000310158"})
        self.assertEqual(self.resolve("Merck reports results")["unresolved"][0]["reason"], "ambiguous_alias")

    def test_t13_trials_remain_distinct(self):
        ids = self.ids("KEYNOTE-942 and INTerpath-001 both evaluate mRNA-4157")
        self.assertTrue({"trial:NCT03897881", "trial:NCT05933577", "asset:intismeran"} <= ids)

    def test_alias_validity_and_known_by_are_enforced(self):
        self.assertEqual(self.registry.resolve("V940", known_by="2026-08-19T12:00:00Z")["mentions"], [])
        data = copy.deepcopy(self.registry.data)
        alias = next(a for a in data["aliases"] if a["alias"] == "V940")
        alias["valid_to"] = "2026-10-18"
        self.assertEqual(EntityRegistry(data).resolve("V940", known_by=NOW)["mentions"], [])

    def test_t15_t16_precision_not_invented(self):
        self.assertEqual(time_interval("2026-10-15 02:02:48")["precision"], "unknown")
        self.assertEqual(time_interval("Q4 2026"), {"raw":"Q4 2026", "precision":"quarter", "start":"2026-10-01", "end":"2027-01-01"})
        self.assertEqual(time_interval("2026-02")["end"], "2026-03-01")
        self.assertEqual(time_interval("2026-02-30")["precision"], "unknown")
        self.assertEqual(time_interval("2026-10-18T11:00:00+05:30")["start"], "2026-10-18T05:30:00Z")

    def test_reject_unsourced_dangling_and_duplicate_entities(self):
        data = copy.deepcopy(self.registry.data)
        data["entities"].append(data["entities"][0])
        with self.assertRaises(ValueError):
            EntityRegistry(data)
        data = copy.deepcopy(self.registry.data)
        data["relationships"][0]["to_id"] = "missing"
        with self.assertRaises(ValueError):
            EntityRegistry(data)


if __name__ == "__main__":
    unittest.main()
