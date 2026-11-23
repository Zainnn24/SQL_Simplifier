# Task 2 · Build the LLM simplification and repair pipeline

**Start immediately with a dummy verifier.** The LLM lead writes a local test function, `dummy_verify(schema_sql, original_sql, candidate_sql)`, while the verifier lead builds the real checker. Use the E2-01 SQL and the two complete `CheckResult` examples in §2. Those examples are ready to use now.

**First working milestone.** Pass `dummy_verify` into the repair loop as its checker. Have the scripted model return the two E2 candidates in §3. When it proposes `end_day >= 10`, the dummy returns the saved `DIFFERENT` result so you can test that the counterexample reaches the second prompt. When it proposes `start_day >= 10`, the dummy returns the saved `EQUIVALENT` result (7 → 3 nodes) so you can test that the loop stops. Match the complete candidate SELECT to its fixture, and raise a test error for an unexpected input. Save both attempts as a development trace.

| You own | You receive | You hand off |
| --- | --- | --- |
| Model adapter, prompt, repair loop, CLI and attempt logs for RQ2 | The verifier’s `verify(schema_sql, original_sql, candidate_sql)` and the benchmark lead’s task records | A reproducible single-task and batch runner, development pilot, frozen run configuration and evaluation-readable JSONL |
| A1–A5, B1–B2 and D1–D2 | Nine existing SQL seeds | 36 reviewed tasks, including 27 new variants. The verifier lead reviews yours. You review the evaluation lead’s 32 tasks. |

CLI flags, file names and status strings below are proposed team interfaces. The four-call budget, first-attempt measurement and strict size rule come from the project.

## 1. Construct the first request

Open the SQL seed file in data folder and search for the headings `-- E2.`, `-- B3.` and `-- C1.`. `E2`, `B3` and `C1` are family labels in that file. The suffix `-01` names the first task in a family, which is the runnable SQL block already under that heading. Thus `E2-01` is the jobs example, `B3-01` is the parcels example and `C1-01` is the samples example. Each block includes its schema, inserts, original query and smaller reference and can run on its own in DuckDB.

1. Load the task by ID. In the initial scripted test, stub the input check to return `READY` for the known E2 schema and original query. At integration, call `validate_input(schema_sql, original_sql)` and continue on `READY` before spending a model call. Record any other preflight outcome with zero calls. Pass only `schema_sql` and `original_sql` into the prompt builder. Keep IDs, family labels, examples, reference queries, sample data, provenance and reasoning in offline records. Strip benchmark comments from the DDL and SQL the model sees.
2. Use one generic instruction across all families. Ask for **one full SELECT** so the verifier can check the unchanged table and ordered projection. Include allowed syntax and the size rule. Pin the output format before the test run.
3. Make a thin model adapter that accepts the rendered messages and settings and returns the raw text, model identifier, available token counts and elapsed time. Give the scripted adapter the same interface. Store credentials outside the logs.

**Draft first-attempt prompt**

```text
Rewrite only the WHERE predicate of the supplied DuckDB query. Return one complete SELECT
statement with the original table and projection. Preserve row selection for every row
allowed by the schema. Make the predicate strictly smaller. Count one node per operator,
column occurrence or literal. A signed integer is one literal. Parentheses add zero.
Use declared BIGINT NOT NULL columns, in-range integer literals, =, !=, <, <=, >, >=,
TRUE, FALSE, AND, OR, NOT and parentheses. Return SQL only.

Schema
{schema_sql}

Original query
{original_sql}
```

For the initial parser, trim whitespace and require one statement. If you decide to allow one enclosing Markdown code fence, strip only that outer wrapper and freeze the decision with the prompt. Preserve the raw response and the exact candidate submitted for verification. A multi-statement or malformed response consumes one attempt and receives a diagnostic.

## 2. Implement the loop

Use `attempt_no = 1…4`. Start a fresh conversation for every task and repetition. After each response, write the attempt, including its verifier outcome, **before** requesting another proposal. A syntactically valid candidate can still change the projected columns or fail equivalence.

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

### Handle the outcome of each attempt

| Outcome from checker or adapter | What the next step does |
| --- | --- |
| `EQUIVALENT` and strictly fewer nodes | Record `ACCEPTED`, return the candidate and end the run. Any verified smaller rewrite qualifies. |
| `EQUIVALENT` and equal or greater size | Give the two sizes, restate the strict target, and request a new candidate if a call remains. Count this as first-attempt equivalence if it occurred on attempt 1. |
| `DIFFERENT` | Give the proposed SQL, the replay-confirmed legal row, the two truth values and the two projected outputs. Ask for a revision against the original schema and query. |
| `INVALID_CANDIDATE` | Return the parse, binding, changed-projection or unsupported-syntax diagnostic. Keep the generic format and fragment reminder. |
| `INVALID_INPUT` or `UNSAT_SCHEMA` | End the run as a source-input/control outcome. Keep it distinct from a model-generated invalid candidate. |
| `INCONCLUSIVE` or `REPLAY_ERROR` | Proposed handling: stop this run, preserve the evidence and flag it for evaluation. A failed replay ends the run for investigation; use the same frozen policy for solver timeouts and UNKNOWN. |
| Call 4 finishes without acceptance on a retryable outcome | Record `BUDGET_EXHAUSTED` and return the original. Preserve the fourth outcome as well as the terminal reason. |
| Provider or infrastructure failure | Record `API_ERROR` and the available usage data. Predeclare the transport policy before test runs; count each dispatched generation request against the cap. Flag incomplete runs for reporting. |

The feedback message keeps the schema and original query available on every turn. Use only diagnostics for the candidate that was actually checked. A copied or repeated candidate still uses a call. The final output should include the selected SQL, whether it was accepted, the terminal reason and the attempt count, so a caller can distinguish an accepted rewrite from an original returned at exhaustion.

## 3. Build the E2 integration test

Use the existing `E2-01` jobs schema with `CHECK (start_day <= end_day)`. The original is `SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10`.

| Attempt | Scripted model returns | Expected trace |
| --- | --- | --- |
| 1 | `SELECT row_id FROM jobs WHERE end_day >= 10` | Verifier returns `DIFFERENT`, 7 → 3. A fixture witness is `row_id=2, start_day=9, end_day=10`; original false, candidate true, outputs `[]` and `[[2]]`. The next message includes all four observations and the row. |
| 2 | `SELECT row_id FROM jobs WHERE start_day >= 10` | Verifier returns `EQUIVALENT`, 7 → 3. Stop immediately, return the second SQL and preserve the failed first attempt. |

At integration, pass the real `verify` function into the same loop in place of `dummy_verify`. Keep the model responses scripted for this run. Assert witness **legality and disagreement** in the integration test because Z3 may choose a different row. Finally, run the same CLI with the live provider on development inputs. Save scripted traces separately from measured model traces.

Additional scripted checks: a copy followed by a smaller candidate, invalid SQL followed by a valid candidate, four repeated failures, a changed projection, an inconclusive check and a replay discrepancy. For exhaustion verify exactly four requests and the original output. For a replay discrepancy verify immediate termination. Inspect rendered prompts to confirm that seed comments and hidden answers never enter either request.

## 4. Make runs scoreable and reproducible

Proposed entry points are `python -m pipeline run --task E2-01 --repeat 1 --config dev.json` and `python -m pipeline batch --split test --repeats 3 --config frozen.json`. Support running one case before attempting the batch.

| Record | Minimum fields |
| --- | --- |
| Run identity | `run_id`, `task_id`, `split`, `repeat`, `config_id`; store the model identifier, sampling settings, prompt version, dependency versions and solver timeout with the configuration |
| Attempt | `attempt_no`, raw response, `candidate_sql`, verifier `status`, `original_nodes`, `candidate_nodes`, diagnostic or witness, tokens where available, elapsed time and the feedback actually sent |
| Run end | `terminal_reason`, `accepted`, `selected_sql`, `calls_used`, total elapsed time, and whether token accounting is complete |

Use a durable key such as `(run_id, attempt_no)` and one final record per run. Ensure the scorer reads a finished run once. When resuming after a crash, reconcile recorded requests before generating again; mark an unresolved dispatched request as incomplete. Preserve first attempts, failures and fallbacks. Keep signed 64-bit integer values exact when serializing witnesses and parsing logs.

**Handoff to evaluation:** supply the two-attempt E2 trace and a copy-on-first-attempt trace. For E2, their scorer should produce first-equivalent = 0, first-verified-smaller = 0, accepted-within-four = 1, recovered-after-confirmed-error = 1, calls = 2, nodes removed = 4. For a copied first answer, first-equivalent = 1 and first-verified-smaller = 0.

## 5. Form your 36 benchmark tasks

Keep each seed as variant 01 and author three more per family. Replace every symbolic `P`, `Q` or `R` below with an actual comparison. Each record needs a schema, original SELECT, strictly smaller hidden equivalent SELECT, sample rows with expected outputs, provenance and the specific error a boundary row exposes. The three directions per family are proposals to develop and review.

| Family | Variant 02 | Variant 03 | Variant 04 |
| --- | --- | --- | --- |
| A1 · repetition | Use a repeated disjunct | Repeat a compound condition | Repeat a conjunct in one branch beside a live alternative |
| A2 · absorption **(dev)** | Use the dual `P AND (P OR Q)` | Absorb a branch containing a compound P | Keep a separate live branch beside the absorption |
| A3 · common factor | Factor the common disjunct out of two clauses | Factor a shared condition across three alternatives | Use a compound common factor |
| A4 · nested negation | Use the dual with OR under the negation | Place the identity under an independent guard | Make one negated operand a compound comparison expression |
| A5 · consensus | Use the dual product-of-sums identity | Add an independent guard to the entire expression | Use a compound selector P and retain both selector cases |
| B1 · stronger bound | Use two upper bounds | Mix strict and inclusive bounds at the same endpoint | Simplify one bounded branch while preserving an alternative |
| B2 · fixed equality | Fix a negative value and remove a redundant lower bound | Add multiple bounds implied by the equality | Use the dual absorption case `x = k OR x <= m` with `k <= m` |
| D1 · incompatible equalities **(dev)** | Keep a live alternative to two incompatible equalities | Put the contradiction under NOT | Embed it in one branch of a larger guarded query |
| D2 · conflicting bounds | Use a strict and inclusive bound meeting at one endpoint | Keep a separate live interval | Negate the impossible conjunction inside a larger predicate |

Provide rows that can detect a plausible over-simplification. For A2 dual absorption, take `P = units >= 10` and `Q = discount_pct > 0`. Include a row with units 9 and discount 5: the original dual rejects it, but returning Q alone would accept it. Keep hidden references and family-specific reasoning outside of prompts, including during repair.

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

## 6. Sign-off

- [ ] From one CLI command, a teammate can run a development task, inspect the first candidate, reproduce the feedback and identify the returned SQL.
- [ ] All retry and terminal paths have deterministic scripted checks. The real verifier and live model have been piloted on development families A2, B3, C1, D1 and E2 before freezing.
- [ ] The evaluation lead can compute the expected E2 scores from the logs without an API call. The frozen configuration describes output parsing, resource limits and infrastructure-error handling.
- [ ] Nine families have four reviewed tasks each. Your review of the evaluation lead’s eight families is complete.
