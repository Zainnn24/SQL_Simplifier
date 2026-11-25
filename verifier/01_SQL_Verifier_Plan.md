# Verifier lead — deliverables

Your main deliverable is a reusable SQL equivalence checker. A teammate can submit two queries without an LLM or an API key and obtain a verdict or a DuckDB-confirmed counterexample.

| You receive | You deliver | Recipient |
| --- | --- | --- |
| Shared SQL scope and E2 seed | `validate_input`, `verify`, typed result dictionaries and direct-check CLI | LLM and evaluation leads |
| Independent fixtures and discrepancy reports from evaluation | Production `encode_query` interface, translation argument and regression evidence | Evaluation lead |
| Eight seeds, family assignments and shared task format | 32 tasks for D3–D5/E1–E5; review of the LLM lead's 36 tasks | Evaluation lead for integration; LLM lead for review feedback |

The [team contract](../README.md#4-shared-benchmark-and-experiment-decisions) defines signatures and complete JSON formats. You implement `validate_input`, `verify` and `encode_query`. **Evaluation implements `evaluate_encoded_row`, small-domain enumeration and independent DuckDB comparisons.** Share the encoder used by your checker so evaluation can exercise the production translation.

The four sections below specify what you receive, implement, hand off and demonstrate. E2-01 is the jobs block under `-- E2.` in [the seed file](../data/SQL_Predicate_Family_Examples.sql); use it for the first integration milestone.

## D1. Input validation API

**Receive:** the [supported fragment, size rule and signature](../README.md#42-sql-scope-and-function-signatures), source schemas and original SELECTs from benchmark tasks.

**Implement and export from `verifier`:**

```python
def validate_input(
    schema_sql: str,
    original_sql: str,
    *,
    timeout_ms: int,
) -> ValidationResult:
    ...
```

Parse exactly one CREATE TABLE and one SELECT using SQLGlot's DuckDB dialect. Check declared columns, BIGINT NOT NULL types, every constraint and query clause, an explicit WHERE and the supported ordered column projection. Reject unsupported operations wherever they occur, including inside CHECKs. Build the predicate size and the encoded schema with all signed BIGINT bounds. Ask whether the schema admits a row.

Return READY, INVALID_INPUT, UNSAT_SCHEMA or INCONCLUSIVE using [ValidationResult](../README.md#validationresult). Every result includes status, original_nodes and diagnostic. READY has an integer count and null diagnostic. Keep an always-false original valid when the schema is satisfiable. Apply `timeout_ms` to the schema solver check; do not treat UNKNOWN as UNSAT.

**Hand off:** the callable and result type to LLM for preflight and to evaluation for source/control validation. `verify` reuses these internal checks and remains safe to call without preflight. The LLM lead should not implement SQL validation or schema satisfiability themselves.

**Acceptance:** E2-01 returns READY with 7 nodes; unsupported input returns an actionable diagnostic; contradictory CHECKs return UNSAT_SCHEMA. Include an inconclusive-path test. The pipeline can stop on any non-READY result with zero model calls.

## D2. Equivalence API and direct-check CLI

**Receive:** schema/original SQL from a task, a candidate SELECT from a caller, the configured timeout, and independent labeled pairs from evaluation.

**Implement and export from `verifier`:**

```python
def verify(
    schema_sql: str,
    original_sql: str,
    candidate_sql: str,
    *,
    timeout_ms: int,
) -> CheckResult:
    ...
```

Use [CheckResult and its status rules](../README.md#checkresult). This function checks one candidate per call. It determines equivalence and returns counts; the LLM pipeline applies the strict-size acceptance rule. Original and candidate must share the table, ordered projection, Z3 column symbols and context.

| Step | Implementation | Completion check |
| --- | --- | --- |
| 1. Parse and validate | Reuse D1 validation with the DuckDB dialect. Read exactly one table definition and one SELECT per query. Resolve column names, check types, inspect every clause and permit only the agreed fragment. Validate the original and schema independently of the candidate. Compare the table and the ordered projection of the two queries. | Changing the projection, adding `LIMIT`, adding a second statement, using an unknown column or introducing `IS NULL` produces a diagnostic identifying the offending input. |
| 2. Build the predicate representation and size function | Represent integer terms (declared column or signed integer literal) separately from predicates (Boolean literal, comparison, AND, OR, NOT). Treat a minus attached to an integer literal as part of that literal. Count one per operator, literal or column occurrence. Ignore parentheses, whitespace, the SELECT wrapper and CHECK expressions. Count the submitted tree before any algebraic simplification. | `x >= -2` has 3 nodes, `NOT (x >= -2)` has 4, and `x >= -2 AND x >= -2` has 7. Reject an out-of-range literal. |
| 3. Encode the schema | Use the shared encoding path exposed by D3; allocate one Z3 integer per declared column. Reuse the same symbols for the original, candidate and checks. Add signed BIGINT bounds from −9223372036854775808 through 9223372036854775807 and all declared row-local checks. Call the conjunction `C`. | A contradictory pair of checks returns `UNSAT_SCHEMA`. A satisfiable schema with an always-false original remains a valid task. |
| 4. Encode the predicates | Map each of the six comparisons to its integer relation. Translate TRUE, FALSE, AND, OR and NOT recursively. Parentheses only affect grouping. Keep SQLGlot’s parsed precedence. Reject unsupported nodes wherever they occur, including inside a CHECK. | Show equalities between columns, negative constants, nested NOTs and `a OR (b AND c)` on hand-computed rows. Here `a,b,c` stand for comparisons. |
| 5. Decide equivalence | First check satisfiability of `C`. For a legal schema, ask Z3 for `C AND XOR(P,Q)`. An UNSAT result on this second query gives `EQUIVALENT`. A SAT result supplies a model to replay. Give both solver calls the configured timeout. | At a BIGINT endpoint, `x <= 9223372036854775807` and `TRUE` agree. The same test exposes a missing type bound. An UNKNOWN result stays inconclusive. |
| 6. Replay each SAT model | Materialize all column values as exact integers. In an empty in-memory DuckDB database, create the original schema, insert the single row with bound parameters, evaluate both predicate truth values, then execute both full SELECTs. Check these observations against the Z3 model. | Emit `DIFFERENT` only after a legal insert and confirmed disagreement in the expected direction. Preserve both projected outputs. Failed inserts or mismatches produce `REPLAY_ERROR` with the failing input. |

Reuse the shared production encoding path delivered in D3. A signed negative literal counts once: `x >= -2` has 3 nodes, NOT adds one, and repeating the comparison with AND gives 7. Count the submitted structure before algebraic simplification.

For replay, populate every declared column, including columns not used by a predicate. Insert bound integer parameters into a fresh in-memory DuckDB table with the original schema. Compare the two predicate truth values and full projected outputs against the model. Emit DIFFERENT only after legal insertion and confirmed disagreement; otherwise return REPLAY_ERROR with reproducing details. `witness` has no separate status and is null outside DIFFERENT. A solver's distinguishing row need not match a fixture row exactly.

**Direct-check CLI:** implement `python -m verifier --case case.json`. The case contains schema_sql, original_sql, candidate_sql and timeout_ms as shown in the [E2 request](../README.md#e2-request-and-results). The CLI reads JSON, calls the same `verify(**request)` and prints its JSON result. This is the proposal's direct candidate-checking path without a model call. The LLM pipeline imports the function directly rather than launching this CLI.

**Hand off:** `verify`, CheckResult and the CLI to both leads. Supply the real E2 results so LLM can replace its dummy verifier without changing the repair loop. Full request/result JSON is maintained in the [E2 request and results](../README.md#e2-request-and-results).

**Acceptance:** use original `SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10` and the jobs schema with `CHECK (start_day <= end_day)`.

| Candidate / schema | Expected result | What the pipeline should see |
| --- | --- | --- |
| `start_day >= 10`, original schema | `EQUIVALENT`, 7 → 3 nodes | Accept the simplification. |
| `end_day >= 10`, original schema | `DIFFERENT`, 7 → 3 | One possible legal row is `(row_id=2, start_day=9, end_day=10)`. Truth values are false/true and projected outputs are `[]` / `[[2]]`. Other legal distinguishing rows are valid. |
| `start_day >= 10`, remove the ordering check | `DIFFERENT`, 7 → 3 | A row with start 10 and end 9 now distinguishes the queries. Keep this as a separate verifier test. |
| Original predicate copied unchanged | `EQUIVALENT`, 7 → 7 | Report equivalence and supply sizes so the caller can request a smaller candidate. |

Also check changed projection, LIMIT, a second statement, an undeclared column, unsupported syntax, BIGINT endpoints and replay failure. A copied original is equivalent but must not be presented as a successful simplification.

## D3. Shared encoder, correctness argument and regression evidence

**Receive:** evaluation's independent pair labels, fixed-row tests and discrepancy reports.

**Implement and export from `verifier`:**

```python
def encode_query(schema_sql: str, query_sql: str) -> EncodedQuery:
    ...
```

Expose the same translator used in D1/D2. The [EncodedQuery contract](../README.md#encodedquery) supplies the column-to-Z3-symbol mapping, schema_constraint, predicate and predicate_nodes. These are Python objects, not a JSON payload. All expressions in a result share the returned integer symbols and context. Validate syntax/types and raise the shared InputError for unsupported input; do not solve schema satisfiability here, so supported contradictory schemas remain available for evaluation.

**Hand off:** this encoder, its result type and InputError to evaluation. Evaluation writes `evaluate_encoded_row()`, fixes concrete row values, calls Z3 to calculate the returned expressions, independently executes SQL in DuckDB and compares observations. You supply the production translation; evaluation supplies the testing tool and reference answers. Both sides preserve the exact SQL, row and configuration when reporting a mismatch.

Write the translation argument alongside implementation: column lookup and signed literals preserve integer values; comparisons preserve truth; induction over AND/OR/NOT preserves predicate truth. Non-null columns give two-valued comparisons. Include the BIGINT bounds and CHECK conjunction. Row-wise agreement with the same ordered projection preserves selected occurrences, including duplicates. Small-domain tests provide implementation evidence; they do not prove all full-width inputs.

Retain a regression for each diagnosed issue. Fix a flipped comparison on its boundary; catch omitted bounds at BIGINT endpoints and omitted CHECKs with the E2 paired tests. Return corrected results and regression evidence to evaluation.

**Acceptance:** evaluation can import encode_query and run its row evaluator without copying your translator or accessing unstable private helpers. Its independent verdict checks, row comparisons and replay tests pass before Experiment B. A discrepancy remains visible until resolved, including its scope and reproducing input.

## D4. Your 32 benchmark tasks and cross-review

**Receive:** D3–D5 and E1–E5 seeds, the [shared task/review formats](../README.md#44-benchmark-review-and-verifier-fixture-formats), and the [family split](../README.md#46-benchmark-split-review-and-integration-milestones).

**Implement:** keep each seed as variant 01 and author three meaningful variants per family. Each needs concrete DDL, original SELECT, a reviewed smaller reference, legal samples and expected projected rows, provenance and an explanation of what mistake the samples expose. For every E task, add the offline **counterexample after removing the CHECK** test: specify the removed CHECK, schema without it, a row satisfying all remaining constraints and both truth values/outputs. Keep that row out of the original-schema sample data and model feedback.

| Family | Variant 02 | Variant 03 | Variant 04 |
| --- | --- | --- | --- |
| D3 · empty integer interval | Put the empty open interval inside an OR with a live branch | Use adjacent negative endpoints inside a negation | Put the impossible branch beside two live alternatives and preserve both |
| D4 · equal columns conflict | Transfer equality through a third column before comparing conflicting values | Put the contradiction under NOT | Put an independent flag around a disjunction containing the contradiction |
| D5 · strict ordering cycle | Reverse the inequalities and move the strict edge | Add a fourth column to the cycle | Place the cycle beside a live branch that permits equality |
| E1 · guaranteed bound | Remove a CHECK-implied upper bound | Remove one guaranteed conjunct and retain two independent ones | Use a guaranteed predicate inside a negation with a surviving condition |
| E2 · order transfers a bound **(dev)** | Transfer an upper bound in the opposite direction | Use a declared strict ordering and distinguish the endpoint | Chain two explicit ordering checks and specify the CHECK removed for the counterexample test |
| E3 · equal columns | Combine equal-column comparisons at a negative endpoint | Substitute across an equality chain | Factor a repeated test expressed through two equal columns |
| E4 · finite domain | Select all except one of four permitted values | Within a finite domain, use disjoint intervals whose complement is one point | Put the finite-domain complement inside a branch controlled by another column |
| E5 · conditional check | Reverse the sign of the amount rule | Use a threshold premise and an inclusive endpoint | Simplify the implied condition in one branch and preserve a separate live branch |

For D3–D5, include a row accepted by each surviving branch; explicitly record an empty expected result when the whole predicate is impossible. Only E2 is a development family for this role.

**Hand off:** 32 task records and review metadata to evaluation. Evaluation reviews your families. Review the LLM lead's 36 tasks and return corrections/approval to that lead and evaluation. Full JSON examples are in the shared [benchmark task records](../README.md#benchmark-task-records) and [review metadata](../README.md#review-metadata).

**Acceptance:** eight families × four reviewed tasks, unique IDs and the correct split; every reference is equivalent and strictly smaller; every E task has a valid counterexample after removing its specified CHECK. Your cross-review of the LLM contribution is complete.
