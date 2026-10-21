# Audit originals, repository renderings and manifests

The retained runtime commands produce exact stdout/stderr and JSON receipts.
Their input manifests bind the tested source bytes. Later final research/report
formatting is not a claim that a different implementation was executed.

The unchanged repository secret scanner initially flagged 139 high-entropy public
checksum/path strings in eight audit files. They were examined as public Git
objects, source-download checksums, synthetic database/receipt hashes and local
execution paths. No credentials were involved. Neither the scanner, its reviewed
baseline, its exclusions nor source-policy defaults were changed.

For these eight initial files, the candidate contains an explicit audit rendering:

| Original record | Repository manifest |
| --- | --- |
| `source-route-observations.json` | `source-route-observations-hashes.txt` |
| `workstream-1-test-receipt.json` | `workstream-1-test-receipt-hashes.txt` |
| `verification/workstream-3-python311.json` | `verification/workstream-3-python311-hashes.txt` |
| `verification/workstream-3-python312.json` | `verification/workstream-3-python312-hashes.txt` |
| `verification/migration-python311.json` and `.txt` | `verification/migration-python311-hashes.txt` |
| `verification/migration-python312.json` and `.txt` | `verification/migration-python312-hashes.txt` |

WS3/migration structured fields use an object containing `algorithm`, `manifest`
and `entry`; embedded strings use `@audit[manifest#entry]` tokens. Manifest entries
contain the exact original public values, including unencoded paths. The other
two records use their explicitly named checksum references. These are audit
renderings, not replacements for immutable operational receipt envelopes or
untouched stdout.

The sealed-tree recheck also flagged the exact corrected-checkpoint Git hash in
`verification/final-static-unchanged-scanner-locks.txt`. That command transcript
now explicitly uses the existing `migration-python311-hashes.txt#h002` reference.
Its untouched original is retained alongside the prior eight, and the failed
sealed scan is retained under `evidence/first-sealed-scan/`. No runtime source file
or tested input changed for this formatting correction.

The delivery retains all nine originals under
`evidence/original-audit-receipts/`, and the original failing scan is retained as
`evidence/secret-scan-preflight.txt`. Originals and renderings are both covered by
the outer delivery checksum manifest. The six WS3/migration records were inverted
and compared byte-for-byte to all untouched originals: 206,958 bytes, six matching
preservation-time hashes, 758 structured references and 240 embedded tokens across
four manifests, with no mismatch. The four manifests contain 138 hash entries,
36 path entries and six original-file entries.

The final full tracked-file scan is run after all candidate files are staged.
Its actual command, stdout/stderr, timestamps and exit code are recorded in
`verification/final-static.json` and referenced transcripts. A passing scan of the
candidate is not claimed as a separate scan of the outer audit-original folder.
That folder contains the original public checksum metadata that triggered the
conservative entropy heuristic.

For reproduction, use the untouched original records, their exact commands and
the checksum manifests together. Do not remove or reinterpret an old receipt
because its implementation, policy or measurement era is no longer current.
