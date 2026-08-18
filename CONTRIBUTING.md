# Contributing

This project is a local-first research pipeline. Contributions should preserve that bias:

- Prefer open-source libraries and official, public, or keyless sources for the default path. Keep optional commercial providers replaceable and nonessential to offline verification.
- Prefer readable source adapters over clever abstractions.
- Keep generated exports, secrets, and local databases out of git.
- Add health metadata whenever a source can fail, rate-limit, or silently degrade.
- Keep network-dependent tests as smoke scripts; keep deterministic parser/date tests separate when possible.
- Describe new outputs as observations, measurements, source evidence, or compatibility labels. Do not add recommendations, target prices, position sizing, execution instructions, paid signal tiers, or performance marketing.
- Preserve legacy `signal` and `alert` keys only where compatibility requires them; document their deterministic meaning rather than extending advisory terminology.

Before opening a PR:

```powershell
python -m compileall -q export_for_gemini.py openinsider_agent.py signals.py timeutil.py stateutil.py agents ledger scripts
python scripts/validate_export_schema.py
python scripts/run_offline_checks.py
python scripts/run_secret_scan.py
```

The secret scan reads only paths reported by `git ls-files`. Every finding in
`.secrets.baseline` must be audited and explicitly marked `is_secret: false`.
Use `detect-secrets audit .secrets.baseline` to review new findings; remove and
rotate a real secret instead of adding it to the baseline. CI rejects missing,
unknown, non-boolean, and confirmed-secret audit decisions before scanning.

For data-source changes, include a short note explaining how the source fails and how the exporter surfaces that failure in `health`.

`requirements.txt` and `requirements-ci.txt` are the reviewed inputs; CI installs the generated hash locks. Regenerate both locks in an isolated maintenance environment after changing either input:

```powershell
py -3.12 -m venv .lock-venv
.\.lock-venv\Scripts\python -m pip install pip==24.3.1 pip-tools==7.5.1
.\.lock-venv\Scripts\python -m piptools compile --generate-hashes --strip-extras --resolver=backtracking --output-file=requirements.lock requirements.txt
.\.lock-venv\Scripts\python -m piptools compile --generate-hashes --allow-unsafe --strip-extras --resolver=backtracking --output-file=requirements-ci.lock requirements-ci.txt
```

Verify clean `--require-hashes` installs on both supported Python versions before committing regenerated locks.
