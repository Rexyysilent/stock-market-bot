"""Windows launcher regressions with synthetic children, never a live export."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"

HARNESS = r'''
param([string]$TestPython, [string]$Scenario)
$ErrorActionPreference = "Stop"
$script:invocations = 0
[Console]::WriteLine("POWERSHELL_VERSION=" + $PSVersionTable.PSVersion.Major)
function Start-Process {
    param(
        [string]$FilePath, [string[]]$ArgumentList, [string]$WorkingDirectory,
        [string]$WindowStyle, [string]$RedirectStandardOutput,
        [string]$RedirectStandardError, [switch]$Wait, [switch]$PassThru
    )
    if ($FilePath -ne (Join-Path $PSScriptRoot ".venv/Scripts/python.exe")) { throw "Wrong Python target" }
    if ($WorkingDirectory -ne $PSScriptRoot) { throw "Wrong working directory" }
    if ($WindowStyle -ne "Hidden" -or -not $Wait -or -not $PassThru) { throw "Wrong process options" }
    if ($RedirectStandardOutput -eq $RedirectStandardError) { throw "Streams must be separate" }
    $script:invocations += 1
    Add-Content -LiteralPath (Join-Path $PSScriptRoot "calls.txt") -Value ($ArgumentList -join " ")
    if ($Scenario -eq "launch-failure") { throw "fixture launch failure" }
    $code = 0
    if ($Scenario -eq "export-failure" -and $script:invocations -eq 1) { $code = 7 }
    if ($Scenario -eq "ledger-failure" -and $script:invocations -eq 2) { $code = 9 }
    # Substitute only the child program. Exercise actual Windows PowerShell
    # Start-Process stream capture and exit-code behavior, without bot imports.
    $child = Microsoft.PowerShell.Management\Start-Process -FilePath $TestPython `
        -ArgumentList @(('"' + (Join-Path $PSScriptRoot "child.py") + '"'), $code) `
        -WorkingDirectory $WorkingDirectory -WindowStyle $WindowStyle `
        -RedirectStandardOutput $RedirectStandardOutput -RedirectStandardError $RedirectStandardError `
        -Wait -PassThru
    if ($Scenario -eq "missing-exit-code") { return [pscustomobject]@{ExitCode=$null} }
    return $child
}
try {
    if ($Scenario -eq "validate") { & (Join-Path $PSScriptRoot "run_scheduled.ps1") -ValidateOnly }
    else { & (Join-Path $PSScriptRoot "run_scheduled.ps1") -Force }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
'''


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class ScheduledLauncherTests(unittest.TestCase):
    def run_case(self, scenario, expected_calls, succeeds=False, marker=False):
        self.assertTrue(POWERSHELL.is_file(), "Windows PowerShell 5.1 is required")
        with tempfile.TemporaryDirectory(prefix="scheduler fixture with spaces ") as directory:
            root = Path(directory)
            (root / "run_scheduled.ps1").write_bytes((ROOT / "run_scheduled.ps1").read_bytes())
            stub = root / ".venv/Scripts/python.exe"
            stub.parent.mkdir(parents=True)
            stub.touch()
            (root / "harness.ps1").write_text(HARNESS, encoding="utf-8")
            (root / "child.py").write_text(
                "import sys\nprint('stdout sentinel')\n"
                "print('x' * 100000)\n"
                "print('routine warning sentinel', file=sys.stderr)\n"
                "sys.exit(int(sys.argv[1]))\n", encoding="utf-8",
            )
            result = subprocess.run(
                [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", str(root / "harness.ps1"), "-TestPython", sys.executable,
                 "-Scenario", scenario],
                cwd=root, capture_output=True, text=True, timeout=45,
            )
            self.assertIn("POWERSHELL_VERSION=5", result.stdout)
            self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
            calls = (root / "calls.txt").read_text().splitlines()
            self.assertEqual(calls, expected_calls)
            self.assertEqual((root / "state/last_scheduled_run_utc.txt").exists(), marker)
            summaries = [p for p in (root / "logs").glob("scheduled-*.log")
                         if not p.name.endswith((".stdout.log", ".stderr.log"))]
            self.assertEqual(len(summaries), 1)
            summary = summaries[0].read_text(encoding="utf-8-sig")
            if scenario != "launch-failure":
                self.assertIn("routine warning sentinel", summary)
                self.assertIn("stdout sentinel", summary)
                self.assertEqual(len(list((root / "logs").glob("*.stderr.log"))), len(calls))
            if not succeeds:
                self.assertIn("failed" if scenario != "launch-failure" else "fixture launch failure", result.stderr)

    def test_warning_stderr_succeeds_and_commits_marker(self):
        self.run_case("success", ["-u export_for_gemini.py", "-u -m ledger update"], True, True)

    def test_export_failure_stops_before_ledger_and_marker(self):
        self.run_case("export-failure", ["-u export_for_gemini.py"])

    def test_ledger_failure_does_not_commit_marker(self):
        self.run_case("ledger-failure", ["-u export_for_gemini.py", "-u -m ledger update"])

    def test_missing_exit_code_fails_closed(self):
        self.run_case("missing-exit-code", ["-u export_for_gemini.py"])

    def test_launch_failure_does_not_commit_marker(self):
        self.run_case("launch-failure", ["-u export_for_gemini.py"])

    def test_validation_warning_succeeds_without_export(self):
        self.run_case("validate", ["-m ledger --help"], True)


if __name__ == "__main__":
    unittest.main()
