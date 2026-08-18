# v3.5 Remaining Roadmap Reminder

Last updated: 2026-08-18

## Current checkpoint

- [x] PR 1 — reproducible CI and clean baseline
- [x] PR 2 — headline relevance repair and editorial lanes
- [ ] PR 3 through PR 12 — remaining work below

Implemented locally in commits `f788e2b` and `09d933b`. Do not treat the
presence of the v3.5 changelog as completion of the full roadmap.

## Next recommended task

### PR 3 — editorial candidate ranking and dynamic focus

- Replace the mandatory hardcoded ROKU deep dive with an optional pinned
  focus, deterministic dynamic focus, or no-focus result.
- Build candidates from mapped entities and supporting evidence.
- Retain separate impact, confidence, novelty, authority, audience relevance,
  timeliness, corroboration, and contradiction components.
- On the frozen August 17 evidence, NXE should outrank an unsupported ROKU
  shell. Empty focus cards must be impossible.

## Remaining ordered backlog

### PR 4 — reconcile and tier social data

- Make all social partitions reconcile exactly to the total.
- Separate universe narrative, outside-universe discovery, unanchored
  diagnostics, structured attention, and explicit exclusions.
- Keep raw diagnostics in JSON while suppressing unanchored noise from the
  default human brief.

### PR 5 — family-specific confluence evidence lifecycles

- Replace the universal elapsed 48-hour window with family policies based on
  elapsed time, completed sessions, filing age, or scheduled-event lifecycle.
- Cover Friday-to-Monday and holiday behavior without allowing reruns to
  refresh old evidence.
- This changes signal populations and therefore requires a new
  `pipeline_version`, explicit state migration, and ledger segmentation.

### PR 6 — price and quote timestamp semantics

- Separate session date, bar boundaries, quote timestamp, provider observation
  time, completion state, price type, and latency class.
- Never present a daily-bar date as a precise quote timestamp.
- Preserve completed-session eligibility for technical calculations.

### PR 7 — honest proxy-source identity

- Distinguish direct X/Twitter, Nitter, and Google News proxy evidence.
- Add directness and confidence fields; proxy rows remain narrative context
  and cannot become hard factual authority.

### PR 8 — source provenance for cash runway and earnings

- Prefer filing/CompanyFacts-derived runway inputs with accession, fiscal
  period, cash/debt definitions, burn formula, and calculation timestamp.
- Give earnings dates provider observation time, confirmed/estimated/inferred
  status, and update history.
- Do not fabricate `as_of` when the source does not provide one.

### PR 9 — canonical entity graph

- Add stable issuer identities, legal/display names, CIK and other identifiers,
  exchanges, tickers, aliases, sectors, countries, currencies, and verticals.
- Extend relationships to projects, commodities, drug assets, trials,
  subsidiaries, insiders, and contracting entities.
- Preserve unresolved mappings as visible nulls rather than guesses.

### PR 10 — separate research and commercial modes

- Research mode may use yfinance and experimental sources with visible labels.
- Commercial mode must enforce approved source rights, entitlements,
  retention/display rules, and licensed adapters.
- Add and test a source-rights registry; commercial rendering must reject or
  quarantine unapproved evidence.

### PR 11 — first vertical pod: uranium and nuclear catalysts

- Expand first-party and official uranium/nuclear evidence through issuer IR,
  SEDAR+, SEDI, EIA, NRC, USGS, procurement, and appropriately licensed data.
- Add the event taxonomy from `handoff.md`, entity/project relationships, and
  strict relevance fixtures.
- Do not expand the universe before the editorial and entity layers are ready.

### PR 12 — sellable human brief

- Render run quality, market context, three-to-five material developments,
  changes since the prior brief, calendar, vertical coverage, and diagnostics.
- Every story card must expose evidence grade, mapped entities, market
  reaction, next checkpoint, contradictions, timestamps, and source links.
- Keep the front page readable in under five minutes and retain full machine
  diagnostics in JSON or an evidence drawer.

## Cross-cutting rules for every future PR

- Keep fixtures and deterministic CI separate from live network checks.
- Do not modify production `state/`, `archive/`, or `briefs/` during tests.
- Breaking public-contract changes require a schema bump and migration notes.
- Any logic change that alters signal populations requires a new pipeline era.
- Never replace missing source time with run time unless the event is
  observation-native by definition.
- Preserve provider/publisher provenance, health degradation, atomic state,
  immutable archives, rollback behavior, and historical ledger segmentation.
- Do not add buy/sell/hold recommendations, price targets, portfolio sizing,
  brokerage connectivity, or automated execution.

Full requirements and acceptance tests remain authoritative in `handoff.md`.
