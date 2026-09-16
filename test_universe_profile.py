"""Deterministic, provider-free configuration and workspace contracts."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from marketbot import prepare_workspace, _require_contact
from universe_profile import apply_profile, fingerprint, load_profile, validate_profile


def example():
    return {"schema_version": 1, "name": "test", "equities": [{"symbol": "AAA", "aliases": ["Example Issuer"]}],
            "funds": ["SPY"], "futures": [], "indices": ["^VIX"], "focus_ticker": "AAA"}


class ProfileTests(unittest.TestCase):
    def test_canonical_identity_includes_aliases_and_baskets(self):
        profile = validate_profile(example())
        self.assertEqual(fingerprint(profile), fingerprint(validate_profile(profile)))
        changed = copy.deepcopy(profile)
        changed["equities"][0]["aliases"].append("other company")
        self.assertNotEqual(fingerprint(profile), fingerprint(changed))

    def test_unknown_duplicate_inactive_and_foreign_symbols_rejected(self):
        invalid = []
        for key, value in (("unexpected", True), ("funds", ["AAA"]), ("focus_ticker", "ZZZ"),
                           ("baskets", {"growth": ["ZZZ"]}), ("equities", [{"symbol": "ABC.NS"}]),
                           ("schema_version", True)):
            item = example(); item[key] = value; invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                validate_profile(item)

    def test_bounded_universe_and_duplicate_json_keys(self):
        item = example(); item["equities"] = [{"symbol": f"A{i}"} for i in range(65)]; item["focus_ticker"] = "A0"
        with self.assertRaises(ValueError): validate_profile(item)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "profile.json"
            path.write_text('{"schema_version":1,"schema_version":1}')
            with self.assertRaisesRegex(ValueError, "duplicate"): load_profile(path)

    def test_profile_application_does_not_inherit_sample_groups(self):
        config = SimpleNamespace(WATCHLIST_URANIUM=["OLD"])
        apply_profile(config, example())
        self.assertEqual(config.ALL_TICKERS, ["AAA", "SPY", "^VIX"])
        self.assertEqual(config.WATCHLIST_URANIUM, [])
        self.assertEqual(config.TICKER_ALIASES, {"AAA": ["example issuer"]})
        self.assertIn(fingerprint(example()), config.UNIVERSE_NAME)

    def test_workspace_is_idempotent_and_cannot_mix_universes(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root) / "research"
            profile = validate_profile(example())
            self.assertEqual(prepare_workspace(workspace, profile), workspace.resolve())
            (workspace / "state").mkdir()
            self.assertEqual(prepare_workspace(workspace, profile), workspace.resolve())
            changed = copy.deepcopy(profile); changed["name"] = "different"
            with self.assertRaisesRegex(ValueError, "differs"): prepare_workspace(workspace, changed)
            self.assertEqual(load_profile(workspace / "universe.json"), profile)

    def test_unmarked_data_is_never_adopted(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / "state.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "nonempty"): prepare_workspace(root, example())
            self.assertFalse((Path(root) / "universe.json").exists())

    def test_contact_templates_rejected(self):
        for contact in ("", "bot", "StockMarketBot contact@example.com", "name you@email.com"):
            with self.assertRaises(ValueError): _require_contact(contact)
        _require_contact("ExampleProject contact@research.invalid")

    def test_plan_runs_without_site_packages_or_network(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "profile.json"; path.write_text(json.dumps(example()))
            process = subprocess.run([sys.executable, "-S", str(Path(__file__).with_name("marketbot.py")),
                                      "plan", "--universe", str(path)], capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(process.stdout)
            self.assertFalse(result["coverage_verified"])
            self.assertEqual(result["active_count"], 3)


if __name__ == "__main__": unittest.main()
