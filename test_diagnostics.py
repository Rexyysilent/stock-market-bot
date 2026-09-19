"""Diagnostics disclose status without secret values or filesystem mutation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from diagnostics import diagnose


class DiagnosticTests(unittest.TestCase):
    def test_runtime_install_does_not_require_optional_validation_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("diagnostics.importlib.util.find_spec",
                       side_effect=lambda name: None if name == "jsonschema" else object()):
                result = diagnose(Path(directory))
            checks = {item["check"]: item for item in result["checks"]}
            self.assertEqual(result["missing_dependencies"], [])
            self.assertEqual(checks["provider_dependencies"]["status"], "ok")
            self.assertEqual(checks["schema_validation_tool"]["status"], "optional_missing")
            self.assertIn("requirements-ci.lock", checks["schema_validation_tool"]["next_action"])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_secrets_and_source_errors_are_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            brief = {"health": {"status": "WARN", "warnings": ["url?api_key=source-private"], "errors": []}, "sections": {}}
            path = root / "daily_brief.json"
            path.write_text(json.dumps(brief))
            before = path.read_bytes()
            with patch.dict(os.environ, {"FMP_API_KEY": "private-token", "SEC_USER_AGENT": "Project contact@research.invalid"}):  # pragma: allowlist secret -- synthetic redaction sentinel
                result = diagnose(root)
            text = json.dumps(result)
            self.assertNotIn("private-token", text)
            self.assertNotIn("source-private", text)
            self.assertNotIn("contact@research.invalid", text)
            self.assertEqual(result["network_requests"], 0)
            self.assertEqual(result["saved_brief"]["warning_count"], 1)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [path])

    def test_command_runs_without_provider_packages_and_does_not_create_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "new workspace"
            p = subprocess.run([sys.executable, "-S", str(Path(__file__).with_name("marketbot.py")),
                                "doctor", "--workspace", str(target)], capture_output=True, text=True, timeout=15)
            self.assertEqual(p.returncode, 0, p.stderr)
            result = json.loads(p.stdout)
            self.assertTrue(result["read_only"])
            self.assertIn("yfinance", result["missing_dependencies"])
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
