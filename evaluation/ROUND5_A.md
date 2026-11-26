# Round 5 A: BIGINT row probes and verifier node calibration

This checkpoint extends Experiment A's real production entry point. It does not
implement a translator or replace David's verifier. Starting revision:
`ed313c42fc2de305c5ef52c785a7666dead0ca6b`.

The fetched dependency references remain `origin/David` and `origin/main` at
`c2030c1784ec0517add6fe0536ac8d0d40fec06f`. They contain plans only, so all four
production exports are still unavailable. Production measurements remain NOT_RUN
with N/A/null metrics, and the formal command actually returns exit code 2.

## Eight independent fixed-row probes

`verifier_probes.py` uses `CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT
NULL)` and `SELECT row_id FROM t WHERE x >= 0`, without additional CHECKs.
For each probe the other column is the legal integer zero.

| Column | Supplied Python row value | Actual DuckDB insertion | Expected schema_holds |
| --- | ---: | --- | --- |
| row_id | -9223372036854775809 (MIN-1) | REJECTED_TYPE | false |
| row_id | -9223372036854775808 (MIN) | OBSERVED | true |
| row_id | 9223372036854775807 (MAX) | OBSERVED | true |
| row_id | 9223372036854775808 (MAX+1) | REJECTED_TYPE | false |
| x | -9223372036854775809 (MIN-1) | REJECTED_TYPE | false |
| x | -9223372036854775808 (MIN) | OBSERVED | true |
| x | 9223372036854775807 (MAX) | OBSERVED | true |
| x | 9223372036854775808 (MAX+1) | REJECTED_TYPE | false |

All eight expectations come from actual DuckDB CREATE/INSERT operations. The
out-of-range values are bound Python integers, never finite-domain SQL literals.
The existing 23 finite cases still contain exactly 182 assignments and 157 legal
rows. Probe counts are separate fields and do not enlarge those denominators.

The formal `run_production_suite` always compares these observations with the
real `evaluate_encoded_row`. Each report entry retains probe ID, column, exact
value, row, schema/query, expected observation, actual observation and matched
flag. Missing, inconclusive and erroneous observations remain visible separately.

## Node consistency and absolute calibration

For each successful full-range or bounded CheckResult, the original node count
must equal the READY count for that same original SQL. Discrepancies retain the
case, stage, expected/actual counts and reproducing request.

The formal suite also directly calls production validation and verification for
three independently counted examples:

| Example | Verdict | Original to candidate nodes |
| --- | --- | --- |
| x >= 0 versus x > 0 | DIFFERENT | 3 to 3 |
| E2 original versus start_day >= 10 | EQUIVALENT | 7 to 3 |
| E2 original versus an exact copy | EQUIVALENT | 7 to 7 |

These fixed counts do not come from a second parser/translator. They catch a
production implementation that reports mutually consistent but absolutely wrong
counts. Calibration calls and their SAT/replay totals are explicitly separated
from the fixture full-range/bounded verdict statistics.

## Actual regression evidence

All 110 existing evaluation tests and 14 new A regressions passed: **124/124**.
`tests/test_verifier_probes.py` uses explicitly injected hand-built Z3 expressions
for only the named examples and scripted validation/verdict responses. The real
row evaluator and formal command path are exercised, but these tests are not
production-verifier measurements and provide no runtime fallback.

The reproducible test-double diagnostics are saved separately in
`results/experiment_a_probe_regressions.json`, labeled
`TEST_DOUBLE_REGRESSION_NOT_PRODUCTION`:

- Omitting only row_id bounds: two discrepancies, at row_id=MIN-1 and MAX+1.
  DuckDB rejects each row, while the defective encoding says schema_holds=true.
- Omitting only x bounds: the corresponding two x probes fail in the same way.
  The old small-domain fixture row comparisons alone show no mismatch.
- Correct verdicts and legal witnesses with original_nodes=4 instead of READY's
  3: both full-range and bounded comparisons fail; absolute calibration also fails.
- READY and verify both claiming 4 for the 3-node original: absolute calibration
  still records the two wrong fields rather than accepting their agreement.
- Incorrect E2 candidate counts: the 7-to-3 simplification and 7-to-7 copy are
  both identified independently.

The formal CLI returns 1 in the injected missing-bound and incorrect-count
regressions. Without the actual APIs it still returns 2 and records NOT_RUN;
the test-double report is never merged with that real dependency report.

## Reproduce

Use the existing pinned environment from the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest evaluation.tests.test_verifier_check evaluation.tests.test_verifier_probes -v
.\.venv\Scripts\python.exe -m evaluation check-verifier --fixtures evaluation/verifier_cases.jsonl --fixtures-only --timeout-ms 5000 --output evaluation/results/experiment_a_offline.json
.\.venv\Scripts\python.exe -m evaluation check-verifier --fixtures evaluation/verifier_cases.jsonl --timeout-ms 5000 --output evaluation/results/experiment_a_production.json
```

The first command runs 30 focused driver tests. The offline command returns 0:
30 fixtures pass, 10 hand witnesses replay, and eight separate type probes have
zero oracle errors. The formal command returns 2 until David supplies the APIs.
Dependencies are unchanged: Python 3.13.7, DuckDB 2.0.1, SQLGlot 30.35.0 and
z3-solver 5.4.1.0. The existing 32 tasks and their real reviews are unchanged.
