# Publishing Checklist

Use this before publishing a release or sample artifact.

## Required

- Confirm `.env`, credentials, cookies, tokens, and personal contact details are not committed.
- Confirm generated briefs, archives, state, logs, databases, browser captures, and private notes remain ignored.
- Confirm `.env.example` contains placeholders only and no machine-specific archive path.
- Review the diff for private watchlists, holdings, sizing, broker data, account identifiers, and local absolute paths.
- Confirm user-facing text describes measurements and source evidence, not buy/sell/hold instructions, return promises, or personalized recommendations.
- Confirm the release does not advertise paid or free signal tiers, target prices, security rankings, position sizing, execution access, guaranteed outcomes, or regulatory approval.
- Confirm default operation and offline verification do not require a commercial market-data subscription; document optional provider adapters honestly.
- Confirm the README retains the non-advisory/no-order-placement scope and the MIT license reference.
- Confirm regulatory wording is presented as a scope limitation, not a claim of registration, exemption, or compliance.

## Verification

```powershell
python -m compileall -q export_for_gemini.py openinsider_agent.py signals.py timeutil.py stateutil.py agents ledger
python scripts/validate_export_schema.py
python scripts/run_offline_checks.py
python test_dashboard_security.py
pip-audit --strict --require-hashes -r requirements.lock
python scripts/run_secret_scan.py
```

The secret-scan command first verifies that every `.secrets.baseline` finding
has an explicit `is_secret: false` review decision, then scans the files listed
by `git ls-files`. A real secret must be removed and rotated, not baselined.

The required CI matrix repeats compile, schema, offline checks, and a separate
loopback-only dashboard test on Python 3.11 and 3.12. The dashboard test uses an
ephemeral local listener and synthetic temporary data; it is excluded from the
network-free runner and inherits any caller-installed network restrictions.
Live external-source checks remain manual.

Live network checks should be run manually with reviewed credentials. Do not commit their generated output.

## Samples and screenshots

- Prefer synthetic or delayed sample data.
- Remove personal research context and identifiers.
- Show `health`, timestamps, schema version, and source limitations.
- Verify redistribution rights for any third-party data included.
