"""Validate the reviewed baseline and scan tracked files for secrets."""

import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / ".secrets.baseline"


def find_baseline_audit_failures(baseline):
    """Return baseline entries that are not explicitly reviewed false positives."""
    if not isinstance(baseline, dict):
        return ["baseline root must be a JSON object"]

    results = baseline.get("results")
    if not isinstance(results, dict):
        return ["baseline results must be a JSON object"]

    failures = []
    for filename in sorted(results):
        findings = results[filename]
        if not isinstance(findings, list):
            failures.append(f"{filename}: findings must be a JSON array")
            continue
        for index, finding in enumerate(findings, start=1):
            if not isinstance(finding, dict):
                failures.append(f"{filename}: finding {index} must be a JSON object")
                continue
            location = f"{filename}:{finding.get('line_number', '?')}"
            decision = finding.get("is_secret")
            if decision is True:
                failures.append(f"{location}: confirmed secret cannot be baselined")
            elif decision is not False:
                failures.append(
                    f"{location}: missing explicit is_secret=false audit decision"
                )
    return failures


def validate_baseline_audit_status(path=BASELINE):
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unable to read reviewed baseline: {exc}"]
    return find_baseline_audit_failures(baseline)


def main():
    audit_failures = validate_baseline_audit_status()
    if audit_failures:
        print("Secret baseline audit check failed:", file=sys.stderr)
        for failure in audit_failures:
            print(f"- {failure}", file=sys.stderr)
        return 2
    files = subprocess.check_output(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
    ).splitlines()
    files = [path for path in files if path != ".secrets.baseline"]
    command = [
        sys.executable,
        "-m",
        "detect_secrets.pre_commit_hook",
        "--baseline",
        str(BASELINE),
        *files,
    ]
    return subprocess.call(command, cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
