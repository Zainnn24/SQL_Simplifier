# LLM lead — deliverables

Your main deliverable is the model-driven simplification loop and its reproducible logs. Start with a scripted model and fixture verifier results; a live provider and a completed verifier are not prerequisites for the first handoff.

| You receive | You deliver | Recipient |
| --- | --- | --- |
| Verifier's `validate_input`, `verify`, ValidationResult and CheckResult | Model adapter, generic prompt and four-call repair loop | Team; evaluation for experimentation |
| Task records/manifest and split from evaluation | Single-task/batch CLI, configuration, attempt logs and run summaries | Evaluation lead |
| Nine seeds and shared benchmark format | 36 tasks for A1–A5/B1–B2/D1–D2; review of evaluation's 32 tasks | Evaluation for integration and review feedback |

The [team contract](../README.md#4-shared-benchmark-and-experiment-decisions) is the common definition. The verifier lead implements SQL validation, AST counting, equivalence checking and DuckDB replay. You call the two functions below and decide how the model run proceeds. Evaluation implements the row-evaluation tool and scorer.

**First handoff:** script E2-01 to return the wrong candidate and then the correct smaller candidate. Use fixture preflight/check results with the exact real signatures, save both attempts and the run summary, and give the trace to evaluation. Replace the fixtures with real verifier calls when available. E2 is the jobs block in [the seed file](../data/SQL_Predicate_Family_Examples.sql).

## D1. Model adapter, prompt and candidate extraction

**Receive:** schema_sql and original_sql from a benchmark task, the shared SQL scope, model settings and verifier diagnostics for later feedback.

**Implement:** an adapter returning raw model text, model identity, available input/output token counts and elapsed time. Give the scripted adapter the same interface. Keep credentials outside configuration artifacts and logs. Start a fresh conversation for each task/repetition.

Only clean schema and original SQL enter the initial prompt with generic instructions. Keep IDs, family labels, sample rows, references, provenance and derivations offline. Strip benchmark comments from the DDL and SELECT. Ask for one complete SELECT with the original table and projection.

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

Use the shared `sql-or-single-fence-v1` extraction policy: trim whitespace and optionally remove one outer SQL code fence. Preserve raw text and exact candidate SQL. Let `verify` validate statement count, SQL syntax and the supported fragment. If no candidate can be extracted, record pipeline OUTPUT_ERROR and diagnostic; it still consumes one model call. Do not invent a verifier result for a response the verifier did not check.

**Hand off:** the scripted/live adapter, generic prompt and versioned response parser as part of the runner delivered in D3. Evaluation receives the frozen artifacts through the run configuration.

**Acceptance:** a scripted adapter produces both E2 responses; a live adapter can run a development task when configured. Prompt inspection confirms no hidden task information enters either the initial request or repair messages. Raw responses remain available even on extraction failure.

## D2. Repair loop consuming the verifier APIs

**Receive:** both functions and their result types from the verifier lead. Import them from `verifier`:

```python
def validate_input(
    schema_sql: str,
    original_sql: str,
    *,
    timeout_ms: int,
) -> ValidationResult:
    ...


def verify(
    schema_sql: str,
    original_sql: str,
    candidate_sql: str,
    *,
    timeout_ms: int,
) -> CheckResult:
    ...
```

These are the functions you consume, not functions you implement. See [their semantics](../README.md#verifier-function-semantics), [ValidationResult](../README.md#validationresult) and [CheckResult](../README.md#checkresult). Pass `config["solver_timeout_ms"]` as timeout_ms on each call. A solver check's timeout is distinct from the model request timeout and generation budget.

**Implement:** call validate_input once before generation. Continue only on READY; otherwise end with zero calls and preserve the full preflight result. For each of up to four dispatched requests, extract a candidate, call verify and record the attempt before asking again. Keep the original schema/query available throughout repairs.

| Received outcome | Pipeline action |
| --- | --- |
| EQUIVALENT with strictly fewer nodes | ACCEPTED; return candidate and stop |
| EQUIVALENT with equal/greater size | Give both sizes and retry if budget remains; a copy still counts for first-attempt equivalence |
| DIFFERENT | Send the actual candidate, confirmed legal row, both truth values and both outputs; request a revision |
| INVALID_CANDIDATE | Send the verifier diagnostic with generic syntax/format guidance; retry if possible |
| INVALID_INPUT / UNSAT_SCHEMA | End as a source-input outcome, distinct from a bad model proposal |
| INCONCLUSIVE / REPLAY_ERROR | Stop, preserve evidence and report separately; do not claim equivalence or an LLM semantic error |
| OUTPUT_ERROR | Send the extraction diagnostic and retry if possible |
| Fourth retryable outcome without acceptance | BUDGET_EXHAUSTED; return the original SQL |
| Provider failure | API_ERROR; preserve available usage and stop under the shared no-automatic-retry policy |

For development, implement `dummy_verify` with the **same parameters, including keyword-only timeout_ms**, as verify. Match the schema, original and complete candidate to saved E2 fixtures; raise a test error for unexpected inputs. The preflight stub likewise matches validate_input and returns the documented READY object for E2. These test doubles supply canned answers only. Replace both by the real verifier functions for integration.

**Hand off:** the repair loop and deterministic integration tests to the team; deliver their traces to evaluation through D3. The shared [E2 request/results](../README.md#e2-request-and-results) and [JSONL trace](../README.md#e2-scripted-jsonl-trace) are the authoritative examples.

**Acceptance:** E2 first returns `SELECT row_id FROM jobs WHERE end_day >= 10`, receives DIFFERENT, then returns `SELECT row_id FROM jobs WHERE start_day >= 10`, receives EQUIVALENT (7 → 3), and stops after two calls. With the real checker assert witness legality and disagreement, not a particular solver row. Test a copied first answer, invalid SQL then success, four failures, changed projection, inconclusive check, replay discrepancy and API/output errors. Exhaustion uses exactly four requests; replay failure stops immediately.

## D3. Single/batch runner, configuration and JSONL logs

**Receive:** the task manifest from evaluation, D1/D2 components, real verifier functions and the shared [RunConfig](../README.md#runconfig) and [JSONL record formats](../README.md#jsonl-record-formats).

**Implement:** entry points using the existing folder name:

```text
python -m LLM_pipeline run --manifest benchmark.jsonl --task E2-01 --repeat 1 --config dev.json --output results/dev/
python -m LLM_pipeline batch --manifest benchmark.jsonl --split test --repeats 3 --config frozen.json --output results/test/
```

The commands are targets to implement. The batch runner selects task/repeat combinations; evaluation uses it to execute the official frozen run. Keep every run's context separate. Record immutable configuration identity, model/sampling settings, prompt/parser versions, dependency/code versions, benchmark version, solver timeout, model timeout and failure policy.

Write one attempt record per dispatched request and one run_end record per run, including non-READY preflight with zero calls. Nest the unmodified CheckResult under check_result. API_ERROR/OUTPUT_ERROR/INCOMPLETE belong to the pipeline and have no CheckResult. The feedback field records the message actually included with that attempt, so attempt 1 has null and attempt 2 contains the first result's feedback. Keep signed integer values exact. Missing token counts remain null and make total accounting incomplete.

Journal dispatches before requests, save completed attempts before retrying, and reconcile uncertain dispatches after crashes. Preserve first attempts and final failure reasons. An unresolved dispatched request becomes INCOMPLETE and consumes a call; automatic transport retries must not exceed the cap. Use the durable `(run_id, attempt_no)` key and exactly one terminal record per run.

**Hand off:** runner, config/prompt/parser artifacts, the scripted E2 trace, a copy-on-first-attempt trace and live development pilot logs to evaluation. Collaborate on freezing the experiment, then let evaluation run/scoring use the same runner rather than building another execution loop.

**Acceptance:** evaluation scores the E2 trace without any model call: first-equivalent 0, first-smaller 0, final success 1, recovery 1/1, two calls and four nodes removed. A copied first answer gives first-equivalent 1 and first-smaller 0. A teammate can run one case, inspect its first candidate, reconstruct feedback and identify accepted SQL versus fallback. Supply the runnable E2 demonstration for the final project demo.

## D4. Your 36 benchmark tasks and cross-review

**Receive:** A1–A5, B1–B2 and D1–D2 seeds, the [task/review formats](../README.md#44-benchmark-review-and-verifier-fixture-formats) and [split/review rotation](../README.md#46-benchmark-split-review-and-integration-milestones).

**Implement:** keep each seed as variant 01 and author three meaningful variants per family. Replace P, Q and R with actual comparisons. Each record needs schema, original SELECT, a strictly smaller equivalent reference, legal sample rows and expected outputs, provenance and the error its boundary rows expose.

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

For A2 dual absorption, take P = `units >= 10` and Q = `discount_pct > 0`. Include units 9, discount 5: the original dual rejects it, while returning Q alone accepts it. Keep references and family-specific reasoning outside prompts during every attempt. Only A2 and D1 are development families for your contribution.

**Hand off:** 36 records to evaluation for integration and to verifier for review. Review evaluation's 32 tasks and return corrections/approval to that lead and the manifest owner. Use the shared [benchmark task records](../README.md#benchmark-task-records) and [review metadata](../README.md#review-metadata) for full JSON.

**Acceptance:** nine families × four reviewed tasks, strict size reduction and correct family split. The verifier lead has reviewed yours and your review of evaluation's families is complete.
