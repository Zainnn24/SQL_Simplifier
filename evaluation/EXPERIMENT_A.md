# Experiment A: independent evidence and production integration

Stage 1, round 4 completes the evaluation-side preparation. Production
Experiment A has **not run** because the verifier implementation is unavailable.
No second SQL-to-Z3 translator or runtime substitute is provided.

## Dependency snapshot

The repository was fetched and checked again before committing this work:

| Reference | Observed commit | Implementation available |
| --- | --- | --- |
| Martin starting revision | `9832f7b78fd6654e36d319059dedad182458dd6f` | Existing evaluation tools and 32 tasks |
| origin/David | `c2030c1784ec0517add6fe0536ac8d0d40fec06f` | Plans only |
| origin/main | `c2030c1784ec0517add6fe0536ac8d0d40fec06f` | Plans only |

The local `verifier` directory imports as a namespace package, but does not export
callable `validate_input`, `verify`, or `encode_query`, or the required
`InputError(ValueError)` class. `production.load_production()` reports all four
missing interfaces. A later delivered implementation must actually be installed
in the checkout being evaluated; a branch name or plan file is not sufficient.

Tested dependency versions, pinned in `evaluation/requirements.txt`:

| Dependency | Actual version |
| --- | --- |
| Python | 3.13.7 |
| DuckDB | 2.0.1 |
| SQLGlot | 30.35.0 |
| z3-solver | 5.4.1.0 |

## Files and ownership

- `verifier_cases.jsonl`: 30 independently labeled cases, separate from the
  personal 32-task collection and the planned 100-task model benchmark.
- `duckdb_oracle.py`: independent supported-SQL validation, actual table inserts,
  original WHERE-text observations, projection results, Cartesian enumeration,
  and exact-domain schema construction. It imports no Z3 encoder.
- `row_evaluator.py`: the public `evaluate_encoded_row` and `EncodedRowResult`.
  It consumes the real encoder's live Z3 expressions and never translates SQL.
- `production.py`: strict real-module loading and explicit missing-interface
  diagnostics, including structured `missing` names.
- `verifier_check.py`: fixture validation, offline evidence, production verdict
  comparisons, row-level discrepancies, independent witness replay and reports.
- `cli.py`: adds `check-verifier` while preserving `check-samples`.
- Four new test modules cover fixture metadata, the DuckDB oracle, row evaluation
  and integration bookkeeping. Test doubles are confined to tests.

The 32 existing task records, their reviews, SQL seeds and author-check fixtures
remain unchanged. All 32 reviews are still PENDING. No evaluated model API was
called, and test-family results were not used for prompt tuning.

## Reproduce the actual runs

Run from the repository root in PowerShell, reusing the existing isolated venv:

```powershell
.\.venv\Scripts\python.exe -m pip install -r evaluation/requirements.txt
.\.venv\Scripts\python.exe -m evaluation check-verifier --fixtures evaluation/verifier_cases.jsonl --fixtures-only --timeout-ms 5000 --output evaluation/results/experiment_a_offline.json
.\.venv\Scripts\python.exe -m evaluation check-verifier --fixtures evaluation/verifier_cases.jsonl --timeout-ms 5000 --output evaluation/results/experiment_a_production.json
.\.venv\Scripts\python.exe -m unittest discover -s evaluation/tests -v
.\.venv\Scripts\python.exe -m evaluation check-samples
.\.venv\Scripts\python.exe -m evaluation.author_checks
```

The offline command returns **0**. The formal command currently returns **2**
and explicitly lists the four missing production exports. That expected gate
does not disable any other evaluation command and does not trigger a substitute.

Saved reports are `results/experiment_a_offline.json` and
`results/experiment_a_production.json`. They include the fixture SHA-256,
dependency versions, timeout configuration and exit code. Production metrics
are JSON null (N/A), with `run_status="NOT_RUN"`; they are not zero-error claims.
Successful finite-row observations can be regenerated from the recorded domains.
Reports retain case-level counts, distinguishing rows, projection multisets and
counterexample replay details. Production discrepancies additionally retain SQL,
schema, fixed row and expected/actual results.

## Actual offline evidence

| Independent label | Cases |
| --- | ---: |
| EQUIVALENT | 12 |
| DIFFERENT | 10 |
| UNSAT_SCHEMA | 1 |
| INVALID_INPUT | 2 |
| INVALID_CANDIDATE | 5 |
| Total | 30 |

All **30/30** fixture checks passed. All **10/10** hand-derived legal
counterexamples replayed with their recorded truth values and projected outputs.
The seven invalid-input/candidate controls have null enumeration and are checked
against the independent SQL-scope contract, not treated as equivalent queries.

The 23 finite-domain cases enumerate **182** complete assignments: **157** legal
rows, **25** CHECK-rejected rows, and **12** distinguishing rows. Bounded verdicts
are 13 EQUIVALENT, nine DIFFERENT and one UNSAT_SCHEMA. A two-column grid with
each column in {-2,-1,0,1,2} exhausts all 25 pairs, not only the five diagonal pairs.
Enumeration and bounded-schema construction share a 10,000-assignment limit.

`bounded-agreement-only` compares `x <= 2` with TRUE. On the recorded domain
{-2,-1,0,1,2}, both predicates hold on all five rows, so the bounded conclusion
is EQUIVALENT. The full-range label remains DIFFERENT: x=3 is legal, the original
is false, and the candidate is true. Neither label overwrites the other.

E2 results are independently checked:

| Case | Independent answer | Observed domain / counterexample |
| --- | --- | --- |
| Ordering CHECK, retain start >= 10 | EQUIVALENT: end >= start >= 10 | Nine assignments, six legal, no distinguishing row |
| Same pair without that CHECK | DIFFERENT | start=10/end=9 is legal; original false, candidate true |
| Ordering CHECK, wrongly retain end >= 10 | DIFFERENT | start=9/end=10 is legal; original false, candidate true |

BIGINT minimum, maximum and their adjacent values are exact integers throughout
JSON, parameter binding, DuckDB, and Z3. SQL literals remain in range. Separate
fixed-row tests actually attempt inserts of MIN-1 and MAX+1; both produce
REJECTED_TYPE with `schema_holds=false` and no DuckDB predicate observation.
For the supported query `x >= 0`, MIN/MIN+1 are legal and false, MAX-1/MAX are
legal and true. Larger out-of-range Python integers are also tested separately.

`contradictory-checks` has CHECK(x < 0) and CHECK(x >= 0): zero of three domain
rows is legal, yielding UNSAT_SCHEMA. `false-query-supported` has a legal schema
with five legal domain rows but a predicate that always rejects; its pair is
EQUIVALENT. An empty query result is not an empty schema.

Ordered multi-column projection controls are included. `repeated-projection`
enumerates row_id={1,2} with x=7; both query outputs aggregate to `[[7],[7]]`.
Comparison uses unordered multisets without losing multiplicity or column order.

## Row evaluator and independent comparison

The exported signature is:

```python
from evaluation import evaluate_encoded_row

result = evaluate_encoded_row(schema_sql, query_sql, row, timeout_ms=5000)
```

The function loads only production `encode_query` and `InputError`. It checks that
the complete integer row exactly matches the encoder's column mapping, excluding
bool/null, and uses that mapping's Z3 context. The solver receives only equalities
fixing each column to its supplied value. It does not assert schema_constraint or
predicate, does not run satisfiability preflight and does not use model completion.
It then evaluates schema and predicate separately.

A CHECK-violating or out-of-range row can consequently return EVALUATED with
`schema_holds=false` and a concrete Boolean `predicate_truth`. Contradictory CHECKs
behave the same way. Malformed row/source inputs are INVALID_INPUT; UNKNOWN is
INCONCLUSIVE; missing production code, symbolic observations and unexpected
failures are EVALUATION_ERROR. Non-EVALUATED results have two null truth fields.

There were **zero successful production encoder calls**. An actual call with the
current plan-only verifier returns EVALUATION_ERROR, null truth fields and a
ProductionUnavailable diagnostic. The 21 row-evaluator/loader unit tests use
explicitly patched hand-built Z3 expressions or controlled error/solver doubles.
These test evaluation logic, including custom contexts and CHECK violations;
they are not translator-validation measurements. Scripted integration tests are
likewise excluded from the saved production report. There is no CLI stub mode.

The DuckDB side executes the original DDL and bound INSERT. Known CHECK rejection,
type rejection, SQL execution error and infrastructure error remain distinct.
Only legal rows have directly observed predicate truth. The Boolean SELECT uses
the original WHERE text, and the full original SELECT supplies projection rows;
neither is regenerated from encoder output.

## Production comparisons ready for David's implementation

When the actual APIs are available, the formal command performs:

1. `validate_input` and full-range `verify` against the independent full-range
   labels, checking complete shared result contracts and preserving failures.
2. Row evaluator observations for every enumerated row and both expressions.
   Compare schema legality for all rows, and predicate truth only on legal rows.
3. Bounded `verify` using the original schema plus an equality-disjunction CHECK
   for each domain and an equality CHECK for every fixed column, including row_id.
   Compare with the independently derived bounded verdict, not the full-range label.
4. Independent replay of every DIFFERENT witness. Another legal distinguishing
   row is accepted; it need not equal the hand-written witness. Truth values and
   ordered projected multisets must match actual DuckDB observations. Failed
   replays retain both observations, including infrastructure diagnostics.

Metrics separately count false equivalence and false difference, with full-range
and bounded verdict breakdowns, validation failures, INCONCLUSIVE, row mismatches,
row errors and infrastructure errors. Check-level replay rate is confirmed replays
divided by **all DIFFERENT plus REPLAY_ERROR outcomes**, including both full and
bounded checks. A REPLAY_ERROR remains in that SAT-model denominator even when no
confirmed witness is available; a zero denominator gives N/A. A scripted regression
with one REPLAY_ERROR and one confirmed witness correctly yields 1/2. This is a
bookkeeping test, not a measured production replay rate.

## Regression results and remaining handoff

The complete evaluation suite passed **110/110** tests: 40 existing regressions,
24 oracle tests, 21 row-evaluator/loader tests, nine fixture-record tests and
16 driver tests. Original sample checks passed 6/6. The unchanged personal packet
passed 24/24 seed checks, 64/64 query checks, all 18 new-variant negative cases
(26 mutations), and 32/32 author node reductions.

David can consume the independent JSONL labels and hand-written witnesses now.
He still needs to supply the real `validate_input`, `verify`, `encode_query`,
`InputError`, the specified result/encoding types and translation/regression
evidence. Then run the existing formal command, investigate every disagreement
or replay discrepancy and rerun affected checks before claiming Experiment A
complete or starting Experiment B. Alvin's 32 task reviews remain outstanding;
this work neither approves them nor modifies any feedback.
