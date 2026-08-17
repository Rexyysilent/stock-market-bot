# Public Roadmap

This roadmap keeps the project centered on data tooling, provenance, reproducibility, and operational safety. It does not include brokerage connectivity, order placement, portfolio allocation, or personalized recommendations.

## Shipped in the current port

- Schema 2.6 with a shared UTC/NYSE run context and completed-session eligibility.
- Atomic current outputs, point-in-time snapshots, append-only archives, transactional state, and a process lock.
- Source-time honesty: `as_of`, `observed_at`, scheduled event time, and effective session remain separate.
- Publisher-diverse headline providers with provenance, capped aggregator fill, syndication dedupe, and provider health.
- Explicit universe, macro, and impact-gated discovery headline lanes with component scores and a frozen editorial regression fixture.
- Hash-locked Python 3.11/3.12 CI with export-schema, vulnerability, and secret checks.
- Structured retail-attention measurements, clinical/FDA event extraction, registry diffs, options volume/OI anomalies, and cross-family confluence.
- A separate version-segmented outcome ledger for retrospective measurement.
- Dashboard support for schema 2.6 sections.

## Near term

1. Configuration files
   - Move sample universes, aliases, and source lists into optional YAML or JSON.
   - Keep a documented, neutral sample configuration.
   - Validate configuration without making network calls.

2. Deterministic CI follow-through
   - Keep live provider checks manual and clearly separated.
   - Add Windows scheduler validation without registering a task.
   - Add restore drills and an SBOM while preserving the current hash-locked baseline.

3. Source contracts
   - Standardize adapter metadata, timestamps, parser versions, and exclusion reasons.
   - Add fixture coverage for provider schema changes and quota responses.
   - Preserve raw checksums where source terms permit.

4. Data-quality surfaces
   - Show run health, stale sections, null timestamps, and partial coverage first in the dashboard.
   - Add machine-readable comparisons against the prior immutable brief.
   - Distinguish quiet data from transport, parser, freshness, and relevance failures.

5. Public examples
   - Publish a synthetic or delayed sample export with no private watchlist or account context.
   - Document consumer checks for health, timestamp eligibility, and schema version.

## Longer term

- Replace research-grade market-data fallbacks with pluggable licensed adapters where needed.
- Add dataset manifests, dependency locks, SBOM generation, and restore verification.
- Improve entity resolution and independence grouping without converting context into advice.
- Add reproducible notebooks for descriptive diagnostics and bias checks.

Any future execution-oriented work belongs outside this public tooling repository and would require a separate scope, controls, review, and applicable regulatory analysis.
