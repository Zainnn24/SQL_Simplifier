# Task 1 · Build the SQL equivalence verifier

**First working milestone:** given a table definition and two queries, run one local command and get either an equivalence verdict or a row on which DuckDB returns different results. A teammate must be able to use this checker without an LLM or an API key.

| You own | You receive | You hand off |
| --- | --- | --- |
| SQL validation, AST size, SQL-to-Z3 translation, witness replay and the translation argument for RQ1 | Supported fragment from the proposal and independently labeled tests from the evaluation lead | A Python function, a direct-check CLI, example result objects and verifier regression tests |
| Families D3–D5 and E1–E5 | Eight existing SQL seeds | 32 reviewed tasks, including 24 new variants. The evaluation lead reviews yours. You review the pipeline lead’s 36 tasks. |

The function names, CLI and status strings below are proposed interfaces for the team to adopt together. The project proposal defines the SQL fragment and acceptance rule.

## 1. Get one real case through the interface

Open the SQL seed file in the data folder and search for the headings `-- E2.`, `-- B3.` and `-- C1.`. `E2`, `B3` and `C1` are family labels in that file. The suffix `-01` names the first task in a family, which is the runnable SQL block already under that heading. Thus `E2-01` is the jobs example, `B3-01` is the parcels example and `C1-01` is the samples example. Each block includes its schema, inserts, original query and smaller reference and can run on its own in DuckDB.

Start with development seed `E2-01`. Its schema is `jobs(row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL, CHECK (start_day <= end_day))`. The original is `SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10`. For each row below, substitute the candidate predicate into that same query.

| Candidate / schema | Expected result | What the pipeline should see |
| --- | --- | --- |
| `start_day >= 10`, original schema | `EQUIVALENT`, 7 → 3 nodes | Accept the simplification. |
| `end_day >= 10`, original schema | `DIFFERENT`, 7 → 3 | One possible legal row is `(row_id=2, start_day=9, end_day=10)`. Truth values are false/true and projected outputs are `[]` / `[[2]]`. Other legal distinguishing rows are valid. |
| `start_day >= 10`, remove the ordering check | `DIFFERENT`, 7 → 3 | A row with start 10 and end 9 now distinguishes the queries. Keep this as a separate verifier test. |
| Original predicate copied unchanged | `EQUIVALENT`, 7 → 7 | Report equivalence and supply sizes so the caller can request a smaller candidate. |

**First handoff.** The LLM lead can immediately use the E2 examples below in their own `dummy_verify` function. Build the real checker in parallel. When it handles E2-01, give the pipeline lead its callable `verify` and real results for the wrong candidate, the smaller equivalent candidate and a copy of the original. They can then rerun the same scripted test against your checker.

### Main verifier function inputs and outputs

`verify(schema_sql: str, original_sql: str, candidate_sql: str) -> CheckResult` checks one candidate per call. The first two arguments come from the benchmark task. The third is the full SELECT proposed on this attempt. A direct checker test can supply a hand-written candidate in the same argument.

**Input.** For the first attempt on `E2-01`, the following JSON object holds the three arguments. A Python caller can pass the decoded object as `verify(**request)`.

```json
{
  "schema_sql": "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL, CHECK (start_day <= end_day))",
  "original_sql": "SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10",
  "candidate_sql": "SELECT row_id FROM jobs WHERE end_day >= 10"
}
```

`schema_sql` supplies one table definition, including its declared checks. `original_sql` supplies the query to simplify. `candidate_sql` must keep the original table and ordered projection and may change the WHERE predicate. The hidden reference and sample rows stay in the benchmark record.

**Output.** Every `CheckResult` has these five top-level fields. The examples show their JSON representation.

| Field | JSON type | Meaning |
| --- | --- | --- |
| `status` | string | One of `EQUIVALENT`, `DIFFERENT`, `INVALID_CANDIDATE`, `INVALID_INPUT`, `UNSAT_SCHEMA`, `INCONCLUSIVE` or `REPLAY_ERROR`. |
| `original_nodes` | integer or null | Number of AST nodes in the original WHERE predicate. Use null if the count is unavailable. |
| `candidate_nodes` | integer or null | The same count for the candidate. |
| `diagnostic` | string or null | Explanation of a validation, schema, solver or replay issue. Use null when there is no diagnostic. |
| `witness` | object or null | A DuckDB-confirmed counterexample for `DIFFERENT`. Use null for the other statuses. |

For the request above, DuckDB confirms the following counterexample.

```json
{
  "status": "DIFFERENT",
  "original_nodes": 7,
  "candidate_nodes": 3,
  "diagnostic": null,
  "witness": {
    "row": {"row_id": 2, "start_day": 9, "end_day": 10},
    "original_truth": false,
    "candidate_truth": true,
    "original_rows": [],
    "candidate_rows": [[2]]
  }
}
```

`witness.row` maps **every declared column** to its integer value for one legal row. The two `*_truth` fields are Boolean results of evaluating the WHERE predicates on that row. The two `*_rows` fields are the full SELECT outputs after inserting just that row into DuckDB. The outer array holds result rows. Each inner array holds the projected columns in SELECT order. Here `[]` means zero rows and `[[2]]` means one row containing the selected `row_id` of 2. The solver may find a different legal counterexample.

For the second attempt, keep the same schema and original SQL and set `candidate_sql` to `SELECT row_id FROM jobs WHERE start_day >= 10`. The verifier returns the following result.

```json
{
  "status": "EQUIVALENT",
  "original_nodes": 7,
  "candidate_nodes": 3,
  "diagnostic": null,
  "witness": null
}
```

The pipeline accepts a candidate when `status == "EQUIVALENT"` **and** `candidate_nodes < original_nodes`. A copy of the original also returns `EQUIVALENT`, but its node counts are 7 and 7, so the pipeline can request another attempt if budget remains.

For a direct check, the proposed CLI is `python -m verifier --case case.json`. The file takes the same three input fields shown above.

## 2. Implement the checker in this order

| Step | Implementation | Completion check |
| --- | --- | --- |
| 1. Parse and validate | Use SQLGlot with the DuckDB dialect. Read exactly one table definition and one SELECT per query. Resolve column names, check types, inspect every clause and permit only the agreed fragment. Validate the original and schema independently of the candidate. Compare the table and the ordered projection of the two queries. | Changing the projection, adding `LIMIT`, adding a second statement, using an unknown column or introducing `IS NULL` produces a diagnostic identifying the offending input. |
| 2. Build the predicate representation and size function | Represent integer terms (declared column or signed integer literal) separately from predicates (Boolean literal, comparison, AND, OR, NOT). Treat a minus attached to an integer literal as part of that literal. Count one per operator, literal or column occurrence. Ignore parentheses, whitespace, the SELECT wrapper and CHECK expressions. Count the submitted tree before any algebraic simplification. | `x >= -2` has 3 nodes, `NOT (x >= -2)` has 4, and `x >= -2 AND x >= -2` has 7. Reject an out-of-range literal. |
| 3. Encode the schema | Allocate one Z3 integer per declared column. Reuse the same symbols for the original, candidate and checks. Add signed BIGINT bounds from −9223372036854775808 through 9223372036854775807 and all declared row-local checks. Call the conjunction `C`. | A contradictory pair of checks returns `UNSAT_SCHEMA`. A satisfiable schema with an always-false original remains a valid task. |
| 4. Encode the predicates | Map each of the six comparisons to its integer relation. Translate TRUE, FALSE, AND, OR and NOT recursively. Parentheses only affect grouping. Keep SQLGlot’s parsed precedence. Reject unsupported nodes wherever they occur, including inside a CHECK. | Show equalities between columns, negative constants, nested NOTs and `a OR (b AND c)` on hand-computed rows. Here `a,b,c` stand for comparisons. |
| 5. Decide equivalence | First check satisfiability of `C`. For a legal schema, ask Z3 for `C AND XOR(P,Q)`. An UNSAT result on this second query gives `EQUIVALENT`. A SAT result supplies a model to replay. Give both solver calls the configured timeout. | At a BIGINT endpoint, `x <= 9223372036854775807` and `TRUE` agree. The same test exposes a missing type bound. An UNKNOWN result stays inconclusive. |
| 6. Replay each SAT model | Materialize all column values as exact integers. In an empty in-memory DuckDB database, create the original schema, insert the single row with bound parameters, evaluate both predicate truth values, then execute both full SELECTs. Check these observations against the Z3 model. | Emit `DIFFERENT` only after a legal insert and confirmed disagreement in the expected direction. Preserve both projected outputs. Failed inserts or mismatches produce `REPLAY_ERROR` with the failing input. |

The supported projection is the original fixed list of columns in order. Filters and CHECK expressions may contain `=`, `!=`, `<`, `<=`, `>`, `>=`, `AND`, `OR`, `NOT`, integer literals, columns, parentheses and Boolean constants in their appropriate type positions. Reject nullable columns, arithmetic, casts, joins, subqueries, aggregates and extra query clauses. Validate every declared constraint so an unsupported one cannot silently disappear from `C`.

Expose the schema and original-query checks from steps 1–3 as `validate_input(schema_sql, original_sql)` for the pipeline’s preflight. It returns `READY`, `INVALID_INPUT`, `UNSAT_SCHEMA` or `INCONCLUSIVE`. The direct verifier uses that same validation internally so both entry points enforce the same fragment.

### Result contract

| Status | Meaning for the caller |
| --- | --- |
| `EQUIVALENT` | A legal schema exists and the disagreement formula is UNSAT. The caller accepts only if `candidate_nodes < original_nodes`. |
| `DIFFERENT` | DuckDB confirmed the witness. The caller may send that row, truth values and outputs to the model. |
| `INVALID_CANDIDATE` | The proposed SQL failed parsing, binding, fragment or fixed-query checks. Return an actionable diagnostic. |
| `INVALID_INPUT` / `UNSAT_SCHEMA` | The source task is unsupported/malformed, or its definition allows no legal rows. Return which condition failed. |
| `INCONCLUSIVE` / `REPLAY_ERROR` | The solver could not decide, or independent execution failed to confirm its witness. Mark the run for separate accounting. |

## 3. Deliver evidence for Experiment A

- Write the translation argument alongside the implementation. For an arbitrary legal row, column lookup and literals preserve integer values, each comparison preserves its truth value, and induction over AND/OR/NOT preserves predicate truth. Non-null columns give two-valued comparisons. Row-wise agreement with the same projection preserves every selected occurrence, including multiplicities.
- Ask the evaluation lead for independently labeled equivalent/unequal pairs and an exhaustive bounded-domain driver. Your implementation supplies the encoder and checker; theirs obtains expected truths from DuckDB. Compare both individual predicate values on each legal row and the final pair verdict under identical bounds. Test at least one legal row in every equivalence fixture.
- Retain a small regression for each diagnosed issue. For example, a flipped boundary operator should fail a test on the endpoint; an omitted CHECK should fail the E2 paired test. Pass discrepancies to evaluation with schema, SQL, configuration, expected result and actual result.

## 4. Form your 32 benchmark tasks

Keep each seed as variant 01. For variants 02–04, use the directions below as starting points. Each new record needs concrete DDL, original SELECT, a strictly smaller hidden equivalent SELECT, legal sample rows, expected projected rows, what error the sample exposes, and a source or derivation. For each E task, add a **separate relaxed-schema witness** that satisfies all remaining constraints and on which the original and reference disagree. Keep this row out of the original-schema fixtures.

| Family | Variant 02 | Variant 03 | Variant 04 |
| --- | --- | --- | --- |
| D3 · empty integer interval | Put the empty open interval inside an OR with a live branch | Use adjacent negative endpoints inside a negation | Put the impossible branch beside two live alternatives and preserve both |
| D4 · equal columns conflict | Transfer equality through a third column before comparing conflicting values | Put the contradiction under NOT | Put an independent flag around a disjunction containing the contradiction |
| D5 · strict ordering cycle | Reverse the inequalities and move the strict edge | Add a fourth column to the cycle | Place the cycle beside a live branch that permits equality |
| E1 · guaranteed bound | Remove a CHECK-implied upper bound | Remove one guaranteed conjunct and retain two independent ones | Use a guaranteed predicate inside a negation with a surviving condition |
| E2 · order transfers a bound **(dev)** | Transfer an upper bound in the opposite direction | Use a declared strict ordering and distinguish the endpoint | Chain two explicit ordering checks and specify which check the witness removes |
| E3 · equal columns | Combine equal-column comparisons at a negative endpoint | Substitute across an equality chain | Factor a repeated test expressed through two equal columns |
| E4 · finite domain | Select all except one of four permitted values | Within a finite domain, use disjoint intervals whose complement is one point | Put the finite-domain complement inside a branch controlled by another column |
| E5 · conditional check | Reverse the sign of the amount rule | Use a threshold premise and an inclusive endpoint | Simplify the implied condition in one branch and preserve a separate live branch |

Give the evaluation lead IDs, family, provenance and the completed fixtures. They assign the family split from the team manifest. Only E2 is in development for this role. For D3–D5, include a row accepted by any surviving branch. When the entire predicate is impossible, record the empty expected output explicitly.

### Shared JSON template for writing a benchmark task

Copy this completed `B3-01` record when writing a task for your own family. Replace the values with your task’s SQL, data, expected projected rows and derivation. Only `schema_sql` and `original_sql` enter the model prompt alongside the generic instructions. Keep the reference and all review fields offline. The second completed seed, `C1-01`, appears in the evaluation plan.

```json
{
  "task_id": "B3-01",
  "family": "B3",
  "split": "dev",
  "schema_sql": "CREATE TABLE parcels (row_id BIGINT NOT NULL, weight_kg BIGINT NOT NULL)",
  "original_sql": "SELECT row_id FROM parcels WHERE weight_kg >= 15 AND weight_kg <= 15",
  "reference_sql": "SELECT row_id FROM parcels WHERE weight_kg = 15",
  "sample_rows": [
    {"row_id": 1, "weight_kg": 14},
    {"row_id": 2, "weight_kg": 15},
    {"row_id": 3, "weight_kg": 16},
    {"row_id": 4, "weight_kg": 15}
  ],
  "expected_rows": [[2], [4]],
  "derivation": "The two inclusive bounds pin weight_kg to 15. Both parcels with that weight qualify.",
  "source": "SQL_Predicate_Family_Examples.sql, B3. Adapted from Apache Calcite RexProgramTest.java, https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1078-L1079",
  "error_exposed": "Keeping only weight_kg >= 15 includes row 3 (weight 16). Keeping only weight_kg <= 15 includes row 1 (weight 14)."
}
```

## 5. Sign-off

- [ ] A teammate can run the checker directly on a submitted candidate, including an unsupported candidate.
- [ ] The E2 table above passes with the real checker. The pipeline consumes its result objects without custom interpretation.
- [ ] The independent verifier tests, bounded-domain comparisons, BIGINT endpoint cases and replay results are available to the evaluation lead. Remaining discrepancies are identified before scoring model outputs.
- [ ] All eight families have four reviewed tasks each, with a relaxed-schema witness for every E task. The pipeline lead has received your review of their tasks.
