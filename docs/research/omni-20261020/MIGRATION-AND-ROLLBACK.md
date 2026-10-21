# OMNI migration and rollback: verified operator replay

The actual `python -m omni` commands successfully backed up a legacy database
before upgrade, applied the additive extension twice, preserved the existing
rows and payload bytes, backed up the upgraded store, and restored both formats
to new paths. The replay passed on Python 3.11 and 3.12. Each run executed
**17 main CLI commands, including seven expected refusals, and passed 67 checks**.

All database contents were disposable, explicitly synthetic storage markers.
The replay did not open a production database, collect source data, record a real
human approval, change a running process, or activate OMNI. AI labels remain
`pending_human_review`. No SQLite database or runtime sidecar is tracked.

## Verification receipts and exact inputs

| Runtime | Platform and SQLite | Result | Receipts |
| --- | --- | --- | --- |
| Python 3.11.17 | Linux x86_64, glibc 2.39; SQLite 3.53.1 | 67/67 checks; 17 main CLI commands | [JSON rendering](verification/migration-python311.json), [transcript rendering](verification/migration-python311.txt), [exact value manifest](verification/migration-python311-hashes.txt) |
| Python 3.12.14 | Linux x86_64, glibc 2.39; SQLite 3.53.1 | 67/67 checks; 17 main CLI commands | [JSON rendering](verification/migration-python312.json), [transcript rendering](verification/migration-python312.txt), [exact value manifest](verification/migration-python312-hashes.txt) |

The two hash-locked environments also passed `pip check`. Each original JSON
receipt records the interpreter, dependency-lock hashes, command arguments,
working directory, start/end clocks, stdout, stderr, expected and actual exit
codes, schema/row/payload fingerprints, backup hashes and every assertion. There are
21 subprocess commands in each receipt: the 17 main CLI operations plus Git
provenance, interpreter version and dependency checks.

The repository copies are explicitly labelled **scan-friendly renderings**, not
raw command stdout. The unchanged originals were copied byte-for-byte into the
delivery's `original-audit-receipts/verification/` directory before conversion.
The adjacent plain-text manifests retain every exact public Git/SHA value and
absolute execution path, including the SHA-256 of each original file. Structured
JSON references and `@audit[manifest#entry]` text tokens resolve to those values.
Reversing these substitutions reproduced the exact original JSON and transcript
bytes. This representation change did not rerun tests or alter outcomes, scanner
rules, ignores or the secret baseline. The delivered replay script still
produces the original raw receipt format.

The legacy database was created from the **literal `SCHEMA` assignment** in
`omni/store.py` at corrected checkpoint:

```text
1a0ae6d2a277642e9466495323c11469f43f911a
```

The replay obtains that exact file with `git show`, parses it with `ast.parse`,
and extracts the assignment using `ast.literal_eval`. It does not execute the
old module and does not use the current, extended schema to create the legacy
fixture. The original schema contains 19 tables and no `omni_receipts` table or
`extensions_contract` metadata.

| Artifact | SHA-256 |
| --- | --- |
| Exact checkpoint `omni/store.py` | `9ca5ae460766ce09e8d2e010898e997392b16ad23fe22461ce59a62b43026e83` |
| Extracted legacy `SCHEMA` literal | `f4223caa1ca1eac0a1c2c45529bba7959420f1968993811a08d943891789492e` |
| Delivered replay script | `5fc4f484893a75f50d76b4b1b2ed578a3c641d63aeee5712ca83b149c3106ab6` |
| Tested integrated implementation fingerprint | `15896afa4388dccf24abe129599a7102a49e220370874d031bcb0d8a40b336da` |

The implementation fingerprint was identical before and after both runs. The
Git HEAD recorded during this replay was
`d778796fc3a3cb310b3571ae48c0d01487e3e12e`; the integrated working tree also
contained the final source changes awaiting the parent integration commit.
The implementation fingerprint, rather than Git HEAD alone, identifies the
source tested here.

## Storage changes and preservation

The base contract remains `omni-store-1`. The additive extension contract is
`omni-extension-schema-1`. It creates these tables:

- `omni_receipts`
- `source_heads`
- `event_change_links`
- `evidence_heads`
- `event_versions`
- `event_packets`
- `event_packet_heads`

Two triggers prevent updating or deleting append-only receipts. The migration
records `extensions_contract` and its actual migration clock in metadata, and
builds a conservative evidence-head index from retained legacy evidence.
Existing source evidence and review payloads are preserved. An old review does
not acquire the new current-state binding; current use still requires the
appropriate review under the current evidence, lifecycle and policy state.

The replay's legacy evidence and review contain deliberately noncanonical JSON
whitespace, escaped characters and a pending review marker. Every original
table's ordered row fingerprint was unchanged after upgrade, excluding the two
new extension metadata entries. The 155-byte legacy review retained SHA-256
`79af40d135ab11ff44dc31ed78350458ecb9c0120377613ebfb5a006c1e933e6`
in the upgraded store and both restored formats.

The first upgrade returned:

```json
{
  "schema": "omni-extension-schema-1",
  "status": "upgraded",
  "legacy_records_rewritten": false
}
```

The second returned `already_current`. The schema, all rows and the original
migration clock were unchanged. The replay then imported two linked synthetic
receipts through the main `receipt-import` command and replayed the second
import. Exactly two receipts remained. Their complete envelopes, hashes and
references validated after restoring the upgraded backup.

This schema migration is separate from `prospective-migrate-legacy`, which
links older pilot material into historical audit records. That workflow cannot
promote legacy records into prospective evidence; see
[Workstream 3](WORKSTREAM-3-PROSPECTIVE.md#schema-upgrade-rollback-and-history).

## Operator sequence: backup before opening the new store

Use the locked Python environment for the selected code revision. Choose the
exact OMNI database and **new** backup paths. The paths below are examples to
replace with the operator's intended local paths:

```sh
MIGRATION_PYTHON=/absolute/locked-venv/bin/python
MIGRATION_REPO=/absolute/stock-market-bot
MIGRATION_DATABASE=/absolute/omni/events.sqlite3
MIGRATION_OLD_BACKUP=/absolute/omni/backups/before-extension.sqlite3
MIGRATION_NEW_BACKUP=/absolute/omni/backups/after-extension.sqlite3
cd "$MIGRATION_REPO"

"$MIGRATION_PYTHON" -m omni --db "$MIGRATION_DATABASE" store-backup "$MIGRATION_OLD_BACKUP"
```

Retain the command's complete output, including its `sha256` and `bytes`, with
the backup. This main CLI branch opens the existing source database read-only
and uses SQLite's online backup API. It does not construct `EventStore` and
therefore does not migrate the source before preserving it. The replay checked
that both the source database bytes and legacy schema/rows remained unchanged.

Normal store-opening commands construct `EventStore`, which automatically
applies a supported additive extension. Run the backup command first; even a
current `status` command would open and upgrade a compatible legacy store.

After the backup has been retained and verified, the explicit upgrade sequence
is:

```sh
"$MIGRATION_PYTHON" -m omni --db "$MIGRATION_DATABASE" store-upgrade
"$MIGRATION_PYTHON" -m omni --db "$MIGRATION_DATABASE" store-upgrade
"$MIGRATION_PYTHON" -m omni --db "$MIGRATION_DATABASE" store-backup "$MIGRATION_NEW_BACKUP"
```

The second upgrade is the idempotency check. Keep the old and upgraded backups
as separate immutable artifacts; a later backup has its own hash. The backup
command refuses an existing destination.

## Restore and rollback to a new path

Restore requires the recorded hash of the selected backup and a nonexistent
destination. Substitute each backup's own recorded hash in these examples:

```sh
"$MIGRATION_PYTHON" -m omni --db "$MIGRATION_DATABASE" store-restore \
  "$MIGRATION_OLD_BACKUP" /absolute/omni/recovery/legacy-restored.sqlite3 \
  --sha256 RECORDED_OLD_BACKUP_SHA256

"$MIGRATION_PYTHON" -m omni --db "$MIGRATION_DATABASE" store-restore \
  "$MIGRATION_NEW_BACKUP" /absolute/omni/recovery/upgraded-restored.sqlite3 \
  --sha256 RECORDED_NEW_BACKUP_SHA256
```

The restore path verifies the backup hash, SQLite integrity and base OMNI
contract, then creates the new file exclusively. It does not open the restored
file with `EventStore`, replace the original database, change an application
configuration or switch a running process. Both restored files were
byte-identical to their corresponding backup immediately after restoration.
Both original backup hashes remained unchanged through the entire replay.

For rollback to the old format, select the old backup and keep its restored
copy unopened by the current application until the operator has selected the
compatible code. Opening it with current `EventStore` would upgrade it again.
The replay inspected this old restored file directly through read-only SQLite
and confirmed that it retained the legacy schema without extension metadata.
It did not launch an old production service or perform a process cutover.

For the upgraded format, the replay opened the restored copy using the actual
`store-upgrade` CLI. It returned `already_current`; legacy bytes and the two
linked new receipts were preserved.

Restoring a backup recovers the state at that backup's capture. It does not merge
later receipts into an earlier snapshot. Preserve the current database and its
upgraded backup before any operator-directed rollback so that later history
remains available for separate reconciliation. No in-place downgrade or table
deletion is part of this procedure.

### Independent read-only verification

This command checks the selected file without invoking migration. Pass the
restored path and the hash from its backup receipt:

```sh
"$MIGRATION_PYTHON" - /absolute/omni/recovery/legacy-restored.sqlite3 RECORDED_BACKUP_SHA256 <<'PY'
import hashlib, json, sqlite3, sys
from pathlib import Path
p = Path(sys.argv[1]).resolve()
actual = hashlib.sha256(p.read_bytes()).hexdigest()
if actual != sys.argv[2]:
    raise SystemExit("backup hash mismatch")
conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True)
try:
    integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    metadata = dict(conn.execute("SELECT key,value FROM metadata"))
finally:
    conn.close()
if integrity != ["ok"] or metadata.get("contract") != "omni-store-1":
    raise SystemExit("integrity or store contract mismatch")
print(json.dumps({"sha256": actual, "integrity_check": integrity,
                  "contract": metadata["contract"],
                  "extensions_contract": metadata.get("extensions_contract")}, indent=2))
PY
```

An old-format restored snapshot has no extension contract; the upgraded
snapshot has `omni-extension-schema-1`. Storage validation does not confer
compatibility with an unsupported extension version. Normal opening rejects an
unknown extension before changing the journal mode or applying schema.

## Direct refusal receipts

Each refusal below was exercised by a subprocess running the real main CLI.
The expected exit code was 1 with a typed JSON `ValueError`; it was counted as
a successful negative control, not a successful migration.

| Attempt | Exact detail | Preservation check |
| --- | --- | --- |
| Backup to an existing file | `backup destination already exists` | Original backup hash retained |
| Restore over the original working store | `restore requires a new destination; existing store is preserved` | Working database hash unchanged |
| Restore over an existing restored file | `restore requires a new destination; existing store is preserved` | Restored legacy hash retained |
| Restore with the wrong hash | `backup hash mismatch` | Destination was not created |
| Upgrade an unsupported extension | `unsupported OMNI extension schema` | Fixture database bytes unchanged |
| Upgrade a foreign preexisting database | `not an OMNI database` | Fixture database bytes unchanged |
| Upgrade a different base store contract | `unsupported OMNI database` | Fixture database bytes unchanged |

The foreign-database fixture contains a table named `production_ledger` and no
OMNI metadata. It proves refusal of that preexisting foreign schema. It does
not contain or claim inspection of an actual production ledger.

The existing acceptance test
`test_omni_lifecycle.MigrationTests.test_legacy_upgrade_is_idempotent_and_backup_restore_preserves_original`
also covers the migration API. These additional receipts supply a concrete
operator replay through the main CLI and the exact corrected legacy schema;
they do not add another script to the repository test suite.

## Reproduce the receipt

The delivery includes `omni-migration-replay-20261020.py` outside the repository.
It requires a checkout containing the corrected checkpoint Git object and the
current OMNI implementation, plus either supported locked Python environment.
The script rejects an existing work directory, a work directory inside the
checkout, and existing receipt destinations. Every database and receipt-input
fixture stays in the new external work directory.

Example invocation for each runtime, using fresh external paths:

```sh
/absolute/locked-python311/bin/python /absolute/omni-migration-replay-20261020.py \
  --repo /absolute/stock-market-bot \
  --work-dir /absolute/disposable/migration-python311 \
  --receipt-prefix /absolute/results/migration-python311

/absolute/locked-python312/bin/python /absolute/omni-migration-replay-20261020.py \
  --repo /absolute/stock-market-bot \
  --work-dir /absolute/disposable/migration-python312 \
  --receipt-prefix /absolute/results/migration-python312
```

The script writes one JSON receipt and one text transcript per invocation. A
failed assertion leaves a `failed` receipt with the captured commands and exits
nonzero. The schema hash and preservation checks are reproducible. The upgraded
database's binary hash varies with its actual migration timestamp; each restore
must use the hash from its own backup receipt.

The measured boundary is an isolated local replay on the recorded Linux/SQLite
platform. Concurrent-writer stress, power-loss recovery and another operating
system were not exercised here. The replay does not claim those results or any
real evidence gate readiness.
