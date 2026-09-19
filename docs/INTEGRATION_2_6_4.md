# Evidence workbench integration — 2.6.4

This change reconciles the September 16 observability handoff (PR #3) with the
later OMNI-01/02 runtime. Applying the older snapshot wholesale would remove
schema 2.8, source-purpose rules, shadow metadata intake and connection bounds.
The integration retains those contracts and brings over the measurement and
usability improvements deliberately.

## Delivered behavior

- Exact five/twenty completed-session basket intervals, full fixed membership,
  deduplicated acquisition, aligned pair windows and explicit unavailable states.
- Ledger windows require the entry open and exit close; incomplete and empty
  responses remain retryable. Directional statistics require their own sample.
- Health-first viewer with numeric coverage, zero/missing distinctions, source
  timestamps, baseline maturity, local JSON import, ticker filtering and readable
  degraded states. Current editorial, event, social and cash-runway families remain.
- Loopback default and explicit LAN controls, preserving connection/deadline limits.
- Dependency-free demo/plan/inspect/diff and read-only doctor. The demo is a
  synthetic legacy-schema example, not live data or a performance record.
- Optional profiles update every purpose-specific cohort before imports and use
  isolated state/archive/ledger paths. They emit schema 2.9 with a normalized
  embedded profile, reproducible policy and full configuration fingerprint.
- Default configuration retains schema 2.8 and the existing shadow policies.
  Pipeline/ledger era is 2.6.4 because the measurement definition changed.
- The Windows launcher keeps separate stdout/stderr capture and judges process
  success by exit code. Its existing scheduler interface remains unchanged.
- Public authored language describes evidence, uncertainty and measurements;
  legacy identifiers remain compatible. This is research tooling, not financial
  advice, a recommendations service or an assertion of regulatory exemption.

## Validation and scope

The integrated suite contains 42 deterministic scripts. Local Windows runs on
Python 3.11.9 and 3.12.14 passed all 42, including a real exporter run with fake
acquisition methods in a temporary profile workspace. That test validates schema
2.9 in a fresh default-config process, checks zero/missing text and JSON, verifies
archive byte identity and checks workspace confinement.

Fourteen loopback server tests passed separately. Chromium was exercised against
the real local server with synthetic data: missing/zero values, filtering, local
file import, invalid-import retention, drag/drop, literal source text, failed text
fetch, failed JSON refresh, previous-snapshot retention, keyboard focus and a
390px viewport. Network failures were deliberately mocked in the browser.

These results do not establish live-provider availability, an expanded-profile
burn-in, a completed scheduled observation streak, Linux CI on the new commit,
predictive validity or production certification. The workflow now includes
Windows and Linux with both supported Python versions; its configuration is not
itself evidence that remote jobs ran.

## Residual limitations and next gates

1. Adjustment lineage is not retroactively established for old cached rows.
   Existing terminal/unpriceable rows are not rewritten; new transient gaps stay
   pending, potentially indefinitely without an explicit terminal provider state.
2. Partial universe benchmarks and overlapping/same-session outcome samples still
   need denominator and dependence work before stronger statistical claims.
3. Historical baseline replay and censored social-leaderboard absence remain
   unresolved. No weights or trading rules were optimized in this integration.
4. Exception rollback is not a crash-atomic multi-file transaction. Profile-aware
   scheduling, interruption/recovery drills and expanded-universe request budgets
   remain future gates.
5. Neutral prompts constrain intended output but do not prove model compliance.
   No model service, Hermes task or Discord message was invoked in this work.

See [ROADMAP.md](ROADMAP.md) for sequenced acceptance gates and
[WORKSPACES.md](WORKSPACES.md) for safe local usage. The September 16 research
register is retained as dated background, not current provider entitlement advice.
