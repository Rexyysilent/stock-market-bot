# Integrated public release notes

Prepared September 19, 2026 for the reconciled public release candidate. These
notes describe repository behavior; they do not claim that a release was
published or that external provider checks passed.

## Data and compatibility

- Default exports use schema 2.8 and pipeline/measurement era 2.6.4. Explicit
  profile workspaces use schema 2.9. Historical schema 2.6, 2.7, and 2.8
  documents remain validator-dispatched by their own version and are not
  rewritten.
- The ordered 29-name instrumented cohort remains separate from the 13-name
  editorial-only cohort. Editorial coverage cannot enter technical collection,
  confluence, mutable signal state, or ledger outcomes.
- Basket and pair measurements use exact completed-session endpoints and fixed
  membership. Missing coverage produces unavailable output; it is not converted
  to zero or a neutral regime.
- Headline rows retain acquisition-provider and underlying-publisher identity,
  source and observation times, exclusions, candidate accounting, and optional
  bounded OMNI-02 metadata-intake diagnostics.
- Secure live, stale-cache, and explicit plaintext-fallback OpenInsider evidence
  have different eligibility. Narrative fallback does not become signal,
  confluence, state, trusted cache, or ledger evidence.

## Viewer and local server

The dashboard now puts run health, coverage, missing values, source timestamps,
and provenance before interpretation. It preserves the previous snapshot after a
failed refresh, accepts a saved JSON brief entirely in the browser, renders all
current data families, limits displayed record counts, and treats external values
as text. `serve_dump.py` defaults to loopback, requires explicit trusted-LAN
configuration, rejects cross-origin and unsafe Host/path requests (including
Windows drive paths), limits file size and concurrent requests, and provides a
credential-free `--demo` mode.

## Historical roadmap phase called “PR 3”

The August v3.5 roadmap uses “PR 3” as an internal phase name for planned
editorial-focus work. It is not GitHub pull request #3. Its deferred statement
is historical; the integrated tree implements that presentation-only phase as
follows:

| Earlier PR 3 item | Integrated location and behavior |
|---|---|
| Optional pinned, dynamic, or no-focus selection | `agents/news_agent.py` emits evidence-gated selection modes; invalid requests fall back to deterministic dynamic selection and an empty eligible set emits `no_focus`. |
| Separate ranking components | `deep_dive.score_components` retains impact, confidence, novelty, source authority, audience relevance, timeliness, independent corroboration, and contradiction penalty without blending them into a claimed return score. |
| Evidence lineage | `scripts/validate_export_schema.py` and the 2.8/2.9 schemas validate selected-row identity, provenance, timestamps, component derivation, and claim evidence references. |
| UI presentation | `dashboard/app.js` renders focus evidence with its source metadata and states that editorial materiality is not expected return. |

This supersedes the older roadmap-phase snapshot for integration purposes. The
historical roadmap remains in `Roadmap/v3.5/`; it should not be treated as a
separate implementation waiting to merge. Later roadmap items are complete only
where the current roadmap or code and tests say so.

## GitHub pull request #3

[GitHub PR #3](https://github.com/Rexyysilent/stock-market-bot/pull/3), represented
by commit `c2e107f` (`feat: harden market evidence, session returns and isolated
research workspaces`), is a separate September 16 observability/workspace change.
The integrated release selectively supersedes its implementation while retaining
its intended boundaries:

| PR #3 surface | Integrated result |
|---|---|
| Completed-session returns and watcher comparisons | `session_returns.py` and `agents/watcher_agent.py` retain exact aligned endpoints, fixed basket membership, and explicit unavailable coverage. |
| Ledger prices and statistics | `ledger/prices.py` retains same-response endpoint refill and retryable missing prices; `ledger/stats.py` retains a separate directional sample gate and directionless option anomalies. |
| Viewer and server | The later viewer/server keeps PR #3's synthetic fixture and local inspection workflow, then adds source-health-first rendering, missing-value handling, failed-refresh retention, local import, Host/origin controls, and bounded connections. |
| CLI and isolated profiles | `marketbot.py`, `universe_profile.py`, profile examples, and `docs/WORKSPACES.md` retain demo/doctor/plan/inspect/diff and workspace isolation; later profile exports use schema 2.9. |
| Versioned export contract | PR #3's 2.6.3-era work is retained where compatible, while the integrated default is schema 2.8 and era 2.6.4, explicit profiles use schema 2.9, and OMNI metadata contracts remain present. |

The integrated release therefore supersedes GitHub PR #3; merging that older
snapshot afterward would reintroduce obsolete versions of these surfaces.

## Reproduction

Run `python marketbot.py demo` (or `python serve_dump.py --demo`) to open the
viewer over its bundled synthetic fixture. This does not create a new export.
Validate existing exports with
`python scripts/validate_export_schema.py`; run the deterministic suite with
`python scripts/run_offline_checks.py`. Dashboard transport checks are separate:

```powershell
node --check dashboard/app.js
python -m unittest -v test_dashboard_server test_dashboard_security
```

Provider availability, source rights, and data completeness remain source- and
time-dependent. A successful synthetic or offline check is not a live-provider
test, adoption evidence, or a performance claim.
