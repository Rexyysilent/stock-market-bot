# Security policy

## Reporting a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/Rexyysilent/stock-market-bot/security/advisories/new).
Include the affected commit, deployment conditions, impact, and a minimal
description using synthetic data. Do not send credentials, personal briefs,
private watchlists, or unredacted provider responses. Please keep vulnerability
details out of public issues while a report is being assessed.

This is a volunteer-maintained project. There is no promised response time,
paid bounty, independent security certification, or general authorization to
test other people's installations or data providers.

## Supported code and system boundary

Fixes target the current default branch. Older tags and retained export schemas
are compatibility references, not separately maintained security branches.

The project collects public-market observations into local files and a local
outcome ledger. Its dashboard serves saved data on loopback by default. Explicit
LAN operation is intended only for a trusted network; the server has neither
application authentication nor TLS and is not an Internet deployment service.
The optional Discord surface has a configured guild boundary. The code does not
place orders or connect to brokerage accounts.

Review the acquisition adapters, export/state/archive operations, profile
workspaces, ledger, dashboard and optional bot when evaluating an issue. External
providers and model services are dependencies, not systems this repository owns.

## Trust boundaries and required properties

- Provider responses, headlines, filings, imported brief files, and model output
  are untrusted data. They must not become executable markup, commands, or trusted
  instructions merely because they were retrieved from a named source.
- Local credentials, private research files and unrelated filesystem content
  must not be disclosed by the viewer, diagnostics, logs intended for publication,
  or a profile export. Public fixtures must contain synthetic or cleared data.
- Server paths must remain within the intended assets and explicitly served
  brief files. Host/origin checks and request, file-size and connection bounds
  are meaningful controls even for a local listener.
- Acquisition redirects, timeouts, parser work and retry budgets must remain
  bounded. An insecure or cached fallback must not inherit secure-live evidence
  status or enter signal/state/ledger calculations as trusted evidence.
- Profiles must not mix another universe's state, archives or outcomes. Invalid
  profiles must be rejected before acquisition. Existing archives must not be
  silently overwritten, and failed stages must be represented honestly.
- Optional bot requests must respect configured guild and resource boundaries.
  Merely having access to a public message must not grant filesystem or account
  access.

These are intended properties, not a claim that all defects have been excluded.
Reports should explain the reachable input, affected boundary and concrete impact.
Do not dismiss a reachable issue solely because a related test passes or because
the default listener is local. No blanket vulnerability-class exclusions are
established by this policy.

## Limitations and operational guidance

The process runs with the local user's filesystem privileges. Use a dedicated
workspace and do not expose the dashboard directly to the Internet. Keep `.env`,
state, archives and databases out of public commits. Rotate any disclosed secret;
removing it from a later commit does not revoke it.

Output rollback handles tested exceptions; it is not a crash-atomic transaction
across every output file. Third-party availability, data entitlements, freshness
and measurement validity remain separate concerns. Dependency scanning, tests
and this policy are maintenance controls, not a security guarantee.
