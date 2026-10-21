# OMNI integration review — 2026-10-21

The six engineering workstreams supplied on 2026-10-20 have been independently
checked and corrected for integration into the opt-in workbench. Installation
does not activate acquisition, scheduled execution, model processing, expansion,
publication, or commercial distribution. OMNI defaults to `off`.

## Corrections after the supplied completion report

- Amount extraction now abstains on postposed bounds such as `USD 10 million or
  more` and `USD 10 million or less`. Previously these could become incorrect
  exact amounts. Six regression subcases cover postposed bounds in the legacy
  and scoped extraction paths, with an exact-amount control.
- The synthetic demonstration test now reads UTF-8 explicitly and closes its
  SQLite connection. This fixes a Windows code-page failure and a subsequent
  temporary-directory cleanup failure; it does not create operating evidence.

The original research reports and Linux receipts remain historical evidence for
their recorded versions. In particular, their 343 OMNI test count predates the
additional regression method described above.

## Independent Windows verification

| Check | Python 3.11.9 | Python 3.12.14 |
| --- | --- | --- |
| Complete offline suite | 84 scripts passed | 84 scripts passed |
| OMNI unit methods within that suite | 344 in 22 modules | 344 in 22 modules |
| Migration replay | 67 assertions passed | 67 assertions passed |
| Additional dashboard transport/security tests | 14 passed | 14 passed |
| Compilation, export schema, secret scan and dependency consistency | Passed | Passed |

JavaScript syntax and staged/working-tree whitespace checks also passed. The
migration replay included 21 commands, including 17 main CLI commands and seven
expected refusals. Tracked source files remained unchanged throughout the checks.
Existing environments were reused. These were deterministic fixture and local
loopback checks, with non-loopback test requests blocked.

The retained synthetic workflow exercised rights withholding, exact review,
correction invalidation, stale-export refusal and a fresh reviewed correction.
Its sources, approvals, reviews, feedback and costs are fictional. It cannot
satisfy a real pilot, rights, customer, cost or release gate.

## Remaining operating evidence

OMNI-10, OMNI-11 and OMNI-12 remain pending. Real source-purpose approvals,
human-reviewed prospective cases, independent coverage observations, actual
costs and customer/usefulness evidence must be supplied through the documented
typed workflows. Live route verification and real scheduled execution are
separate from this verification. No predictive-performance or release-readiness
claim follows from the offline suite.

See the [operator guide](../omni-20261020/OPERATOR-GUIDE.md),
[requirements matrix](../omni-20261020/REQUIREMENTS-MATRIX.md),
[source gaps](../omni-20261020/SOURCE-GAPS.md), and
[migration and rollback guide](../omni-20261020/MIGRATION-AND-ROLLBACK.md).
