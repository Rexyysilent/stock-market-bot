# Local workspaces and universe profiles

## Start without risking existing history

Keep the current `config.py`, `state/`, `archive/`, and `ledger/` data in place.
The new launcher is opt-in. A profile workspace owns its state, brief snapshots,
canonical archives, yfinance cache, ledger database, and descriptive statistics.
The launcher changes its working directory before importing the exporter or
ledger, and sets profile configuration before agent imports capture it.

```sh
python marketbot.py plan --universe profiles/existing-sample.json
python marketbot.py run --universe profiles/existing-sample.json --workspace workspaces/sample
python marketbot.py ledger ingest --universe profiles/existing-sample.json --workspace workspaces/sample
python marketbot.py ledger update --universe profiles/existing-sample.json --workspace workspaces/sample
python serve_dump.py --data-dir workspaces/sample
```

Run these commands from the repository root in the locked Python environment.
`plan`, `inspect`, `diff`, and the synthetic `demo` do not need provider packages.
`run` needs configured providers and a real application/contact email string in
`SEC_USER_AGENT`. `ledger update` and `ledger rebuild` can acquire prices.
`ledger ingest` reads existing canonical archives.

The launcher does not register a scheduled task or send Discord messages.
Profile runs disable the optional external archive mirror so a new universe is
not silently copied into a mirror configured for another workflow. Configure and
test a profile-aware mirror explicitly in a later change before using one.

## Identity and isolation

On first use, an empty directory receives `universe.json`, containing the
normalized profile. Subsequent launches must match its fingerprint. A nonempty
unmarked directory is refused, and changing a name, alias, member, group, basket,
or pair requires a new workspace. This deliberately prefers a fresh baseline
over a silent mixture of unlike histories. Merely reformatting JSON is safe;
list order is currently part of the fingerprint.

There is an outer operation lock for profile exporter/ledger commands and the
exporter's existing inner state lock. Call the profile launcher once per process.
Direct imports into a long-lived notebook can retain configuration globals and
are not a supported way to switch profiles.

Do not copy mutable legacy state into a new workspace to avoid warm-up. Existing
archives remain valuable records in their original era, but automatic historical
migration is not implemented. The legacy Windows scheduler and `python -m ledger`
commands still use the old config path. They are not profile-aware.

## What a profile means

Use `profiles/us-core.example.json` as a structure example, or copy a sample to a
file ending in `.local.json` (ignored by Git). Supported fields are equities with
aliases, funds, context futures, context indices, focus ticker, existing specialist
groups, two fixed baskets, and optional relative-return pairs. Duplicate symbols,
unknown keys, inactive group/pair references, and overly large profiles fail
before provider acquisition. The active cap is 64, the file cap is 128 KiB.

The 45-member example is not a recommended portfolio, a verified listing snapshot,
or a provider coverage claim. The validator is deliberately restricted to
U.S.-style symbols; it does not infer `.NS`, `.L`, or other exchange suffixes.
NYSE date alignment is not the native session model for every context instrument.
A plan explicitly reports `coverage_verified: false`.

The existing-sample profile follows the repository's public sample members. It
leaves pair comparisons empty because legacy pairs referenced members outside
that active universe. Add both legs explicitly to a new profile before enabling
a pair. Specialist clinical/social/macro source queries retain their existing
scope; expanding equity symbols does not prove all those sources cover them.

## Local viewing and safe failure

```sh
python marketbot.py demo
python serve_dump.py --data-dir workspaces/core
python serve_dump.py 8081 --data-dir workspaces/core
```

The default listener is `127.0.0.1`, not every interface. A saved JSON brief can be
selected or dropped into the viewer; it is read locally by the browser. Its name
and generation time are shown. Invalid imports keep the previous snapshot, and
a missing text export does not hide a valid JSON export. Reloading the server
snapshot is explicit. This interface does not validate provider truth.

For an explicitly trusted private LAN, select the machine's IPv4 address:

```sh
python serve_dump.py --host 192.168.1.10 --data-dir workspaces/core
```

For a wildcard listener, explicitly allow the address used in the browser:

```sh
python serve_dump.py --host 0.0.0.0 --allow-host 192.168.1.10 --data-dir workspaces/core
```

Replace the illustrative private address with the actual one. There is no
application authentication or TLS. Do not forward the port to the internet or
use this server as a multi-tenant service. Host/origin checks and browser headers
reduce accidental cross-site exposure; they are not an access-control system for
untrusted machines on the same LAN.

## Diagnose before retrying

Read `health.status`, warnings, exact source timestamps, numeric coverage, and
baseline sample counts before interpreting an empty panel. `WARN` can coexist
with useful records. A successful process or valid JSON does not imply complete
coverage. The inspect command reports records and missing fields, not a fabricated
zero. The diff command checks schema, pipeline, and universe identifiers before
comparing; disappearance of a warning is not sufficient evidence of recovery.

For live-source failures, preserve a sanitized diagnostic and retry within the
source's limits. Do not erase state, disable freshness rules, rotate identities,
or claim a source is healthy merely because a fallback returned something.
