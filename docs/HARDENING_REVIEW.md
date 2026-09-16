# 2.6.3 hardening review: make missing evidence visible

Review date: 2026-09-16. Audited base: `b214ca8aa0d9`.

## Decision

Keep the collection pipeline, shared run clock, immutable exports, headline lanes,
source fallbacks, and descriptive ledger. Repair demonstrable measurement and
presentation failures instead of replacing them with a larger agent framework.
This change set is an opt-in research-tool hardening release, not a certification
of live coverage, predictive value, or internet-facing production readiness.

## Repaired behavior and evidence

| Area | Before | In this change | Deterministic check |
|---|---|---|---|
| Ledger cache | Any cached row made an entire window look complete | Require valid entry open and exit close; refill both from one response; incomplete stays retryable | `test_ledger_price_cache.py` |
| Directional statistics | A sufficiently large overall group could expose a tiny directional subset | Gate the directional subset separately; report entry-session count and IID dependence caveat | `test_ledger_statistics.py` |
| Basket horizons | Five observations were described as five daily return intervals | Use six closes for five exact comparison sessions and twenty-one for twenty | `test_session_returns.py`, `test_watcher_comparisons.py` |
| Missing baskets | Missing measurements could leave a neutral label or partial composition | Full fixed membership required; null and UNAVAILABLE plus exclusions | Same checks |
| Pair windows | Variable calendar-window or undersized histories could enter comparisons | Same exact five-session dates for both legs; excluded-pair reasons | Same checks |
| Duplicate requests | Duplicate watchlist members and repeated horizon history requests | Deduplicate watchlist; reuse each basket member's history across both horizons | `test_watcher_comparisons.py` |
| Viewer trust | Source warnings hidden; missing RSI substituted with 50 | Health first, real numeric coverage, timestamps, baseline maturity, missing RSI and valid zero distinguished | `test_dashboard_contract.py`; browser verification |
| Viewer robustness | Text-export failure could prevent JSON display | Independent JSON rendering, bounded fetches, explicit refresh errors, local file import | Browser verification; server tests |
| Viewer exposure | Broad listener/CORS defaults and external fonts | Loopback default, explicit LAN, Host/origin checks, same-origin headers, DOM text rendering, local assets | `test_dashboard_server.py`, `test_security_regressions.py` |
| Configuration | Code edits and shared mutable directories | Strict optional profiles, immutable fingerprints, separate state/archive/ledger workspaces | `test_universe_profile.py` |
| Inspection | Raw brief reading was the main debugging path | Read-only inspect/diff with actual nested schema and comparability checks | `test_brief_tools.py` |

The synthetic example covers positive, zero, unavailable, and degraded states.
It is not a performance example or redistributed market feed. The dashboard
keeps its existing tabs and evidence families rather than adding a new framework.

## What was intentionally not changed

No trading rules were optimized, no learned predictive model was trained, and no
broker, hosted service, telemetry collector, paid data dependency, or autonomous
publishing system was introduced. The existing default universe and legacy
scheduler remain in place. The new 45-symbol sample is opt-in and unverified for
live provider coverage. Baskets and pairs in a profile need explicit members.

The schema stays 2.6, but measurement logic moves to pipeline 2.6.3. Existing
archives are not rewritten, and current baselines start in a new pipeline era.
More provenance and stricter interpretation are preferable to pretending old and
new comparison windows are interchangeable.

## Verification scope

The CI runner enumerates 30 deterministic check scripts on Python 3.11/3.12,
validates schema, checks JavaScript syntax, audits locked runtime dependencies,
and scans tracked files for secrets. Check the actual PR/run result; this file
is not a frozen assertion that every future commit is green. Reports now record
configured versus executed checks, duration, and failure/timeout status.

Local review also exercised the HTML/CSS/JS using Chromium with deterministic
fetch fixtures: missing RSI, zero RSI, missing basket data, correct insider counts,
filtering, text escaping, text-endpoint failure, local import, invalid import,
and narrow-screen overflow. Browser HTTP navigation was blocked by the review
environment, so browser rendering and loopback HTTP-server checks were separate,
not a claimed end-to-end network-browser certification.

No credentialed live-provider burn-in, Windows scheduled-task execution, rate-limit
load test, or independent security-service assessment is claimed. New regressions
are useful evidence, not proof that all possible source failures are covered.

## Remaining defects and release gates

1. **Retrospective baseline replay:** the existing scorer excludes the same session
   rather than enforcing strictly earlier stored sessions. Do not use the current
   mutable baseline file as a historical replay engine. Build a pure, dated replay
   path with future-row invariance tests before advertising point-in-time scoring.
2. **Adjustment lineage:** new price refills keep their two endpoints together, but
   legacy cached rows lack acquisition/basis identities. Add raw/adjusted/corporate-
   action lineage and immutable run manifests before stronger reproducibility claims.
3. **Dependence:** the current ledger bootstrap is IID over ticker/entry-session
   clusters. Same-session cross-ticker and overlapping-horizon dependence remain.
   Its new warning and sample gate are not a dependence-corrected confidence interval.
4. **Missing outcomes:** a provider-empty response can still become permanently
   unpriceable. Separate transient empty/quota/unsupported/delisted cases with a
   retry policy and inspectable exclusions.
5. **Coverage denominator:** the ledger's universe comparison currently averages
   available prices, unlike the newly strict fixed baskets. Persist expected/used
   membership and do not describe it as a complete-universe benchmark.
6. **Common timing contract:** exact completeness was tightened for basket and pair
   returns, not every technical, volatility, and fundamental calculation. Apply a
   provider-neutral eligibility envelope to the other consumers next.
7. **Censored social samples:** absence from a ranked social endpoint is not a
   measured zero. Preserve censored/unknown status before interpreting attention
   emergence. Cross-family confluence also does not prove independent evidence.
8. **Operational durability:** exception rollback and atomic individual writes are
   not a crash-atomic multi-file transaction. Add interruption/restore drills and
   profile-aware Windows scheduler validation before unattended-release claims.
9. **Scale:** the 64-member cap is a guard, not a benchmark. Centralize per-host
   budgets and measure duplicate calls, quota events, elapsed time, and coverage
   before increasing active acquisition.
10. **Distribution:** code licensing does not grant third-party data redistribution
    rights. Keep the public demo synthetic and review each adapter's terms before
    any public feed, cache sharing, or commercial deployment.

Each gate is expanded into an acceptance test in [ROADMAP.md](ROADMAP.md).
The primary-source reasoning is in [RESEARCH_REGISTER.md](RESEARCH_REGISTER.md).
