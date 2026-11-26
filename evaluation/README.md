# DuckDB sample checks

This first-stage tool checks finite samples, starting with B3-01 and C1-01.
It does not certify the 100-task benchmark, prove equivalence on all legal rows,
check AST reduction, or substitute for independent review and the production
verifier. No model API or verifier implementation is required.

## Environment and reproduction

Tested on Windows with CPython **3.13.7** and DuckDB **2.0.1**. DuckDB is the
only external dependency; tests use Python's standard-library `unittest`.
Run these PowerShell commands from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r evaluation/requirements.txt
.\.venv\Scripts\python.exe -m evaluation check-samples
.\.venv\Scripts\python.exe -m unittest discover -s evaluation/tests -v
```

The sample command prints the actual Python and DuckDB versions. The virtual
environment and Python caches are ignored by Git. With an activated environment,
the sample command is `python -m evaluation check-samples`.

## What the command checks

By default, it loads the existing `evaluation/B3-01.json` and
`evaluation/C1-01.json`. For each task it:

1. Creates a separate in-memory DuckDB database, executes `schema_sql`, and inserts
   `sample_rows` using the actual declared column names. JSON object key order is
   irrelevant; every input row must supply exactly the table's columns as integers.
2. Checks all input columns and duplicate counts against the matching family's
   SQL seed INSERT data. The seed block is read from
   `data/SQL_Predicate_Family_Examples.sql` into another fresh in-memory database.
   Only its CREATE and INSERT statements run, not its SELECTs or ROLLBACK.
3. Executes `original_sql` and `reference_sql` independently and compares **each**
   result with `expected_rows`. Two equally wrong queries therefore fail.

Comparisons use unordered multisets of complete rows: result order is ignored,
duplicate counts are retained, and projected column positions remain significant.
Input and expected values reject booleans and nulls. Query execution errors and
result mismatches identify the task and query; one query's error does not suppress
the other query's check. For local sample integrity, queries must each be a single
SELECT. This is not the production verifier's full SQL-fragment validation.

A mismatch reports `missing` and `unexpected` entries, each containing the full
row and its exact count. These are expected-minus-actual and actual-minus-expected
multiset differences, respectively. Seed differences use SQL INSERT rows as the
expectation and JSON sample rows as the actual input, with column names displayed.

Exit code **0** means all requested sample checks passed. Exit code **1** means
an input, seed, query execution, or expected-result check failed; command-line
usage errors return **2**. Output explicitly states that these are sample checks.

## Additional task files

The same checker accepts local task JSON files without creating new benchmark
tasks or requiring review metadata. It reads the sample-check fields defined in
the shared contract: `task_id`, `schema_sql`, `original_sql`, `reference_sql`,
`sample_rows`, and `expected_rows`. Other metadata is left untouched.

```powershell
.\.venv\Scripts\python.exe -m evaluation check-samples --tasks path/to/task.json path/to/another.json
```

For explicit `--tasks`, seed consistency is marked SKIPPED unless `--seed-file`
is supplied. Later variants have their own samples and need not match seed 01.
To check explicit variant-01 records against their seeds, include `family` and use:

```powershell
.\.venv\Scripts\python.exe -m evaluation check-samples --tasks evaluation/B3-01.json evaluation/C1-01.json --seed-file data/SQL_Predicate_Family_Examples.sql
```

All SQL inputs are trusted local project fixtures. Databases are ephemeral and
the checker does not write to the task JSON files or the SQL seed file.

## Acceptance evidence and regression tests

| Case | Expected sample-check result |
| --- | --- |
| B3-01, 4 input rows | Both queries return `[[2], [4]]`; seed input rows match |
| C1-01, 10 input rows | Both return `[[2], [3], [4], [5], [6], [7], [10]]`; seed input rows match |
| Temporary C1 copy without the reference's `reading >= 40` branch | Original passes; reference fails with `missing=[{"row": [10], "count": 1}]` |
| Two input rows with different IDs and the same projected `x=7`; reference selects only one | Original passes; reference fails with `missing=[{"row": [7], "count": 1}]` |

Negative cases are temporary test fixtures, not benchmark additions. A regression
test passes when the checker detects the specified failure. The duplicate case
would fail to detect the error if result rows were compared as sets.

Tests also cover queries that are both wrong, unexpected duplicate counts, row
order, JSON key order, projection column order, full-row seed correspondence,
seed value drift, missing duplicate inputs, and CLI success/failure exit codes.
The C1 negative test reads and changes a temporary JSON copy and confirms that
the tracked source remains unchanged. No canonical JSON or SQL seed is modified.

Full benchmark acceptance remains future work: complete tasks and independent
reviews, production-verifier checks, strict size reduction, and the agreed family
split must still be implemented and validated before claiming 100-task acceptance.
