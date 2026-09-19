# Public pipeline parity and boundaries

The public implementation follows the current private runtime through OMNI-02,
with public-facing documentation and neutral research-tooling constraints. It is
not an advisory product, a signal subscription, or a regulatory exemption.

## Coverage and editorial interpretation

The sample configuration has 29 instrumented names and 13 editorial-only names
(42 headline-coverage names). Editorial-only coverage defaults to shadow and
requires manual activation. It does not expand price, options, earnings, social
baseline, per-ticker SEC, or technical collection. Existing legacy fields named
`signal`, `alert`, or `confluence` represent deterministic research classifications,
not instructions to trade or predictions of returns.

Dynamic focus selects a fresh, evidence-backed editorial subject, or no subject
when evidence is inadequate. It is not a stock pick. Pins cannot bypass mode,
identity, evidence, or freshness checks. New editorial names cannot enter the core
classification/state/ledger paths through discovery or social mentions.

## Operational parity

- Pipeline and ledger version 2.6.4; daily schema 2.8, with version-dispatched
  legacy schema 2.6/2.7 validation.
- Shared run-scoped OpenInsider acquisition, bounded retries, explicit health,
  stale-cache exclusion, SEC EDGAR fallback, and HTTP narrative-only degradation.
- Source-purpose policy and registry fingerprints, zero-spend defaults, and
  unchanged acquisition targets during shadow evaluation.
- Hash-locked dependencies, deterministic offline checks, secret scanning, and
  versioned illustrative schema fixtures (see `fixtures/README.md`).

The sample ticker list is public example configuration, not disclosed holdings or
personal allocation. Private credentials, archives, databases, operational state,
logs, machine paths, and private roadmap/research documents are not ported.

## OMNI-02: delivered and deferred

`EVIDENCE_INTAKE_MODE=shadow|off` defaults to shadow. It retains purpose-approved
parsed metadata before the five-headline cap, including dropped candidates, and
exports loss/gap diagnostics. The review accessor is not connected to active focus,
classifications, state, or ledger promotion. Replay runs selection only, without
network calls, using matching selector and policy fingerprints.

Limits: 2,000 records, 2,000,000 metadata bytes plus manifest overhead, 4,096
characters per text field. Omission, sanitization or unresolved terminal identity
makes replay incomplete; incomplete/error captures refuse exact replay.

No raw bodies or summaries are newly retained, no model/redistribution rights are
granted, and no independent polling or upstream reconciliation is added. Identical
canonical references share an origin ID, but cross-URL event provenance remains
unverified. Snapshot time is not historical first observation. Source completeness,
watermarks and intervals remain unknown. Google fallback retains its conditional
acquisition gate. These are explicit limits, not completed later OMNI packages.

Disable intake with `EVIDENCE_INTAKE_MODE=off`; additive provider diagnostics remain.
No schema migration, backfill, automatic activation, or historical archive rewrite
is necessary.

## Verification and publication

The public implementation additionally rejects automatic OpenInsider redirects
before any follow-up request, bounds shared geological-text parsing, and limits
the optional dashboard to eight concurrent connections with finite idle and
absolute connection deadlines. These are defensive limits, not an authentication
layer or a guarantee that this lightweight dashboard is suitable for the internet.
Geological extraction rejects non-text or inputs over 100,000 characters rather
than truncating them into potentially misleading grades; ordinary supported
integer/decimal forms and evidence provenance remain unchanged. Unsupported
signed, grouped, exponent and Unicode numeric tokens, and non-finite conversions,
cannot supply extracted grades. These checks do not verify a poster's claims.

The public metadata-manifest validator also fails closed on malformed container
shapes. These public defensive differences do not modify private operational
state, historical archives, schema identifiers, or the configured ticker cohorts.

Run the commands in [PUBLISHING_CHECKLIST.md](PUBLISHING_CHECKLIST.md) before a
release. Offline fixtures and fake providers are not evidence of live availability,
complete market coverage, licensing, or a completed scheduled observation streak.
Publishing artifacts requires separate source-rights and privacy review. Source
text may itself contain directional language; it remains attributed input, never
the tool's own recommendation.

See [INTEGRATION_2_6_4.md](INTEGRATION_2_6_4.md) for the reconciled observability, measurement and optional schema 2.9 profile changes.
