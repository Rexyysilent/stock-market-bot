# Publishing Checklist

Use this before publishing a release or sample artifact.

## Required

- Confirm `.env`, credentials, cookies, tokens, and personal contact details are not committed.
- Confirm generated briefs, archives, state, logs, databases, browser captures, and private notes remain ignored.
- Confirm `.env.example` contains placeholders only and no machine-specific archive path.
- Review the diff for private watchlists, holdings, sizing, broker data, account identifiers, and local absolute paths.
- Confirm user-facing text describes measurements and source evidence, not buy/sell/hold instructions, return promises, or personalized recommendations.
- Confirm the README retains the non-advisory/no-order-placement scope and the MIT license reference.

## Verification

```powershell
python -m compileall -q export_for_gemini.py openinsider_agent.py signals.py timeutil.py stateutil.py agents ledger
python scripts/validate_export_schema.py
python scripts/run_offline_checks.py
pip-audit --strict --require-hashes -r requirements.lock
python scripts/run_secret_scan.py
```

The required CI matrix repeats compile, schema, and offline checks on Python
3.11 and 3.12. Live network checks remain manual and must not be promoted into
required deterministic CI.

Live network checks should be run manually with reviewed credentials. Do not commit their generated output.

## Samples and screenshots

- Prefer synthetic or delayed sample data.
- Remove personal research context and identifiers.
- Show `health`, timestamps, schema version, and source limitations.
- Verify redistribution rights for any third-party data included.
