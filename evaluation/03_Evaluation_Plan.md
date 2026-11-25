# Evaluation lead — deliverables

Your deliverables make the benchmark, verifier checks and model results reproducible. You own **`evaluate_encoded_row()`**, the small-domain enumeration driver and independent DuckDB comparisons. The verifier lead supplies the production encoder through `encode_query()`; you do not need to implement SQL-to-Z3 translation.

| You receive | You deliver | Recipient |
| --- | --- | --- |
| Task contributions from all three roles, SQL seeds and shared record formats | Your 32 tasks, benchmark validator, reviewed 100-task manifest and family split | LLM lead and team |
| Verifier's `validate_input`, `verify`, `encode_query`, types and InputError | Independent fixtures, row evaluator, DuckDB comparisons, Experiment A results and discrepancy reports | Verifier lead and team |
| LLM single/batch runner, frozen configuration and JSONL logs | Scorer, official Experiment B results and reproduction/report materials | Team |

Use the [team contract](../README.md#4-shared-benchmark-and-experiment-decisions) for full schemas and interface definitions. Start by checking the supplied B3-01/C1-01 records and building independent fixtures. Build the scorer against the scripted E2 log while the verifier and live model adapter are in progress.

## D1. Validated benchmark, your 32 tasks and cross-review

**Receive:** [B3-01.json](B3-01.json), [C1-01.json](C1-01.json), all 25 [SQL seeds](../data/SQL_Predicate_Family_Examples.sql), contributions from both leads, and the shared [task/review formats](../README.md#44-benchmark-review-and-verifier-fixture-formats).

**Implement:** a loader and benchmark validation command:

```text
python -m evaluation validate-benchmark --manifest benchmark.jsonl
```

The command is a target to implement. First compare every sample row with its seed INSERT, create a fresh DuckDB table and run original/reference separately. Compare both outputs to the author's expected unordered multiset, retaining duplicate rows.

| First record | Input rows | Expected row IDs from both SELECTs | Boundary check |
| --- | ---: | --- | --- |
| B3-01 | 4 | 2, 4 | Both weight-15 parcels qualify; weights 14 and 16 do not |
| C1-01 | 10 | 2, 3, 4, 5, 6, 7, 10 | Reading 40 belongs to the separate interval; 26 and 39 remain excluded |

Use these as base-record examples; completed metadata and independent review remain necessary. Package the other seeds, including E2-01 for integration. Keep reference solutions, samples, reasoning and review metadata offline; the prompt builder receives only cleaned schema and original SQL.

Author your B3–B5 and C1–C5 families: retain each seed as variant 01 and add three meaningful variants. The [shared B3-02 example](../README.md#b3-02-authoring-example) uses `NOT (x >= -2 AND x <= -2)` → `x != -2`, 8 → 3 nodes, and remains a draft to review.

| Family | Variant 02 | Variant 03 | Variant 04 |
| --- | --- | --- | --- |
| B3 · bounds pin one value **(dev)** | Use B3-02 from the shared examples: NOT at a negative endpoint | Put the pinned interval beside a live range | Pin one column while retaining an independent comparison on another |
| B4 · exclude an endpoint | Exclude an upper endpoint | Turn an inclusive bound into a strict one inside a two-sided interval | Keep a live OR branch beside an endpoint exclusion |
| B5 · BIGINT type limit | Use the signed minimum lower bound | Use a strict comparison beyond which no legal value exists, e.g. `x > 9223372036854775807` | Put an always-true type bound under AND with a genuine restriction |
| C1 · merge overlapping intervals **(dev)** | Chain three overlapping intervals | Keep a separate interval across a one-integer gap | Merge intervals inside a branch controlled by a second column |
| C2 · consecutive points | Use consecutive negative integers | Preserve one isolated point beside a consecutive run | Put the run under NOT and check both endpoints |
| C3 · one missing point | Reverse the construction under NOT | Retain a second-column condition around the one-point gap | Place the one-point gap inside a bounded window |
| C4 · outer bound removes a branch | Use an upper bound to eliminate the right branch | Use three branches with two survivors | Use an outer bound that also clips the surviving interval |
| C5 · excluded point in a gap | Remove two exclusions inside one gap | Preserve a second exclusion inside a live range | Put the redundant exclusion inside one branch, with another independent branch |

For every contributed task, use this review procedure:

1. Run DDL and legal samples in DuckDB. Compare each SELECT to the author's expected multiset. Check exact endpoints, adjacent values and every surviving OR branch. Include duplicate projected rows and multi-column projection controls in the verifier tests.
2. Obtain another person's explanation of equivalence on every legal row and family membership. Once Experiment A is validated, use `verify(..., timeout_ms=...)` on each reference and require strict node reduction. Sample agreement alone does not establish equivalence.
3. Record source/test adaptation or derivation and the concrete error each variant exposes. Flag normalized structural duplicates, including renaming, commuted operands and literal relationships; inspect flags with schema and boundary intent. Replace cosmetic variants.
4. Collect separate benchmark_reviews.jsonl records. You review verifier's tasks, verifier reviews LLM's tasks, and LLM reviews yours. An E-family record also needs the counterexample after removing its specified CHECK: confirm the schema changes only by that removal, the row satisfies every remaining constraint and original/reference outputs differ. This row is not a legal original-schema sample or model feedback.
5. Validate 25 unique families, exactly four tasks each, unique task IDs, complete approved reviews and the [20/80 family split](../README.md#46-benchmark-split-review-and-integration-milestones). Use A2, B3, C1, D1 and E2 for development; freeze the remaining 20 families for testing. Keep already-simple inputs, unsupported queries, contradictory schemas and deliberately wrong candidates in separate control files.

**Hand off:** your 32 records to the LLM lead for review; reviewed integrated manifest, review metadata and family split to the LLM runner; review corrections to the verifier lead. Original/reference pairs count once, and external structural duplicates contribute only once to coverage.

**Acceptance:** B3-01/C1-01 match their fixtures; all 100 task records and reviews pass one validation command; each reference is equivalent and smaller; family totals and split are exact. Samples, references and review fields never enter model prompts.

## D2. Row evaluator, independent fixtures and Experiment A

**Receive:** the following functions from the verifier lead, together with [ValidationResult](../README.md#validationresult), [CheckResult](../README.md#checkresult), [EncodedQuery](../README.md#encodedquery) and [InputError](../README.md#encoding-validation-and-inputerror):

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


def encode_query(schema_sql: str, query_sql: str) -> EncodedQuery:
    ...
```

`encode_query` returns the column-to-Z3-integer mapping, schema_constraint, predicate and predicate_nodes from the same translation used by production verify. All expressions share that result's symbols and context. These live Python objects are not JSON. Encoding validates syntax/types but does not reject a supported contradictory schema by checking satisfiability.

**Implement and export from `evaluation`:**

```python
def evaluate_encoded_row(
    schema_sql: str,
    query_sql: str,
    row: dict[str, int],
    *,
    timeout_ms: int,
) -> EncodedRowResult:
    ...
```

This is your deliverable. It supplies the tested side of the comparison; independently executed DuckDB supplies reference observations. Use [EncodedRowResult](../README.md#encodedrowresult), including the distinct EVALUATED/INVALID_INPUT/INCONCLUSIVE/EVALUATION_ERROR statuses.

### Suggested implementation of the row evaluator

1. Call `encode_query`. Check that the row maps exactly every declared column to an integer, excluding booleans/nulls. Map InputError or malformed row input to INVALID_INPUT. Preserve out-of-range integer row values for schema-legality tests; SQL literals still follow the supported range rule.
2. Construct a Z3 solver in the returned expressions' context. Add only bindings of the form `column_symbol == supplied_value` for all columns, and set its check timeout from timeout_ms.
3. Do **not** assert schema_constraint or predicate when obtaining the model. A CHECK-violating row or false predicate must still be evaluable; asserting them would turn such a test into UNSAT and hide the values being tested.
4. After SAT, use `model.eval()` separately on schema_constraint and predicate. Convert concrete Z3 true/false to Python booleans. Complete row bindings should leave neither expression symbolic; a symbolic result or unexpected failure is EVALUATION_ERROR. Do not silently fill missing columns with model-completion defaults.
5. Return EVALUATED with both booleans and null diagnostic. A row violating CHECKs/bounds yields schema_holds=false, not a source-input error. UNKNOWN/timeout gives INCONCLUSIVE; error results have null truth fields and a diagnostic. A supported contradictory schema evaluates false on every row.

Z3 is calculating the expressions supplied by the translator here. It does not decide whether that translation matches the SQL. Your comparison driver makes that decision using independent execution:

| Check | Z3 side | Independent DuckDB side |
| --- | --- | --- |
| Row legality | Encoded schema_constraint on the fixed row | Insert the same row into a table with the original DDL; recognize actual CHECK/type violations separately from infrastructure errors |
| Predicate truth on a legal row | Encoded predicate on that row | Evaluate the original SQL WHERE expression as a Boolean SELECT over the inserted row, without filtering the row away |
| Pair equivalence over a finite domain | `verify` with explicit restrictions to that same domain | Enumerate all legal rows and check whether any original/candidate truth values differ |

DuckDB must execute the input SQL expressions, not expressions regenerated from the Z3 encoding. You may parse SQL to isolate its WHERE text; do not reuse translator outputs as expected answers. Compare schema legality for every row and predicate truth on legal rows. For an illegal row, the encoded predicate can still be calculated, but the failed table insert does not provide a DuckDB predicate observation.

A fixed-row predicate result of false means that row does not satisfy that predicate. It is different from the SAT/UNSAT answer to a two-query disagreement formula. For example, SQL `x >= 10` on x=10 is true in DuckDB. If the translator mistakenly encodes `x > 10`, Z3 correctly calculates false for the wrong expression; the comparison exposes the translation bug.

### Small-domain exhaustive testing suggestion

`evaluate_encoded_row` evaluates **one row per call**. Your driver repeatedly calls it across many rows and expressions. Start with one or two relevant columns and enumerate the Cartesian product of `{-2,-1,0,1,2}`:

| Enumerated columns | Assignments | Other columns |
| --- | ---: | --- |
| x | 5 | Supply explicit fixed values for every remaining column |
| x, y | 25 | For example, fix row_id=1 when it is irrelevant to predicates/CHECKs |
| k columns | 5^k | Grow deliberately; report which domains were actually exhausted |

For x and y this includes (-2,-2), (-2,-1), through (2,2), not just pairs with x=y. Each supplied row still contains all declared columns. Keep the domains/fixed values in the [fixture enumeration object](../README.md#independent-verifier-cases). Use cases with constants near this domain to exercise both truth outcomes. Also test around actual thresholds: a predicate `x >= 10` needs 9, 10 and 11; testing only -2 through 2 would miss its boundary.

Cover all six comparisons, column-to-column comparisons, negative literals, TRUE/FALSE, AND/OR/NOT, nesting/precedence and multiple CHECKs. Include rows satisfying and violating the checks. Test full-width BIGINT minimum/maximum and adjacent values separately; out-of-range row values check that type bounds are present.

For each valid pair fixture, enumerate DuckDB legality and both predicate truth values. If no legal assignment exists, the bounded expected result is UNSAT_SCHEMA; if any legal assignment distinguishes the predicates, it is DIFFERENT; otherwise it is EQUIVALENT. Compare with verify after adding explicit row-local CHECKs that describe exactly the enumerated domains **and all fixed values**. A domain can be expressed as `x = -2 OR x = -1 OR x = 0 OR x = 1 OR x = 2`; add equality CHECKs for fixed columns. Keep the original schema unchanged in the unrestricted test. Do not compare a bounded result to an unrestricted fixture label: disagreement might exist only outside the enumerated domain.

This tests translator behavior across the enumerated inputs, not all BIGINT values. Pair it with the verifier lead's structural translation argument and independent full-range boundary/constraint fixtures.

### Independent fixtures and verdict/replay checks

Create `verifier_cases.jsonl` using the shared format, with logical explanations for equivalent pairs and hand-checked legal distinguishing rows for unequal pairs. Prepare these expected answers independently of the production translator. Include unsupported SQL, changed projection and inconsistent-schema controls.

| Fixture to build | Independent answer | Defect it can expose |
| --- | --- | --- |
| E2 with the ordering CHECK, original vs. `start_day >= 10` | Equivalent because end ≥ start ≥ 10 | Constraint missing or mistranslated |
| Same pair with that CHECK removed | Unequal on start 10, end 9 | Stale constraint accidentally reused |
| `x >= 0` vs. `x > 0`; nested NOT; swapped column comparisons | Label each pair separately and evaluate the boundary row | Strict/inclusive confusion, precedence or swapped operands |
| `x <= 9223372036854775807` vs. TRUE; `x > -9223372036854775808` vs. TRUE | First equivalent; second unequal at the signed minimum | Bound omission, literal sign or precision error |
| `CHECK (x < 0)` and `CHECK (x >= 0)` together | Schema admits no row | Accidental acceptance through vacuous equivalence |

Implement `python -m evaluation check-verifier --fixtures verifier_cases.jsonl --timeout-ms 5000`; the timeout value is a configurable development example. Compare two directions of wrong verdicts (unequal labeled equivalent; equivalent labeled unequal), inconclusives, controls and row-level translation mismatches. Use **all SAT disagreement models**, including REPLAY_ERROR cases, as the denominator for confirmed replay rate. Preserve schema, SQL, row, expected/actual results and configuration for each discrepancy; unexpected failures must not be scored as successful checks.

**Hand off:** the row evaluator, enumeration/DuckDB driver, independent fixtures and Experiment A tables to the team; reproducing discrepancy reports to verifier. The verifier lead supplies fixes and regressions. Tests can be developed before the real encoder is available, but actual translator validation must use that production encoder.

**Acceptance:** legal/illegal fixed-row cases, finite-domain pair decisions, independent full-range labels and BIGINT endpoint tests agree. Every equivalence fixture has at least one legal row, except explicit UNSAT_SCHEMA controls. Any verdict or replay discrepancy is investigated and affected tests rerun before Experiment B. Record inconclusive outcomes separately.

## D3. Log scorer and official Experiment B results

**Receive:** the LLM lead's single/batch runner, prompt/parser/configuration, scripted E2 and copied-first-answer traces, and actual model logs; D1's manifest and validated verifier from Experiment A.

**Implement:** a scorer for [the shared JSONL format](../README.md#jsonl-record-formats). Join records to the frozen manifest/configuration, distinguish pipeline outcomes from nested CheckResult statuses, reconcile attempt numbers/calls and require exactly one terminal record per run. Do not discard missing, duplicate, failed or incomplete runs silently.

```text
python -m evaluation score --manifest benchmark.jsonl --logs results/
```

First score the scripted E2 trace: first-equivalent=0, first-smaller=0, final success=1, recovery=1/1, calls=2 and nodes removed=4. A copied first answer counts for first equivalence but not first simplification. Keep scripted runs and deliberate verifier errors outside measured model data.

Pilot on the 20 development tasks. With the LLM lead, freeze the model, sampling settings, prompt, response parser, benchmark version, software/code versions, solver timeout, request timeout, four-call cap and failure-handling policy. Only development tasks inform changes. Evaluation executes the official run using the delivered LLM batch runner; do not implement a second model loop:

```text
python -m LLM_pipeline batch --manifest benchmark.jsonl --split test --repeats 3 --config frozen.json --output results/test/
```

Schedule 80 held-out tasks × 3 repetitions = 240 runs with fresh model contexts. Reconcile scheduled IDs and terminal records before scoring. Report the following **for each repetition over its 80 scheduled tasks**; pooled results must be labeled repeated runs of the same tasks.

| Measure | Calculation |
| --- | --- |
| First-attempt equivalence | First outcome `EQUIVALENT` / 80. A copy counts. |
| First-attempt verified simplification | First outcome `EQUIVALENT` with fewer nodes / 80. |
| Verified simplification within four calls | Runs ending `ACCEPTED` by call 4 / 80. Returning the original after exhaustion earns zero. |
| Recovery after a confirmed wrong first answer | First-attempt `DIFFERENT` runs that later succeed / all first-attempt `DIFFERENT` runs. Show numerator and denominator; report N/A for a zero denominator. |
| Amount of simplification | For accepted runs, report original minus accepted nodes and percentage reduction relative to original. State the accepted-run count; the hidden reference supplies one valid target. |
| Outcomes and resource use | Tabulate confirmed disagreements, invalid candidates, inconclusives, replay errors, infrastructure issues and missing data. Label counts by attempt or by run. Report calls, elapsed time and available token usage across runs, including failures. |

Attempt-level verifier outcomes are in check_result.status for CHECKED attempts. OUTPUT_ERROR is a pipeline extraction failure and is reported with invalid-output counts but is not a fabricated verifier verdict; API_ERROR and INCOMPLETE are separate infrastructure outcomes. For first-attempt metrics, a run with no first check earns no verified success and remains visible in the scheduled denominator. Recovery is restricted to confirmed first-attempt DIFFERENT cases; show numerator and denominator and use N/A when the denominator is zero.

**Hand off:** reproducible scores, repeat/category/family tables, resource totals and selected actual model-error traces to the team. Use original query, candidate, replayed row and accepted revision where available to explain errors. Recovery describes the full retry process and does not establish a causal effect of feedback.

**Acceptance:** scores reproduce the scripted expectations and all three test repetitions from saved logs without another model call. All scheduled runs are accounted for, and statuses, repeated attempts, fallbacks, missing usage and missing records remain visible.

## D4. Reproduction package and report materials

**Receive:** D1–D3 artifacts, verifier's translation argument/regression evidence and the LLM lead's runnable E2 demonstration.

**Implement:** assemble the final benchmark/reviews, independent fixtures, frozen configuration and prompt/parser artifacts, raw logs, scoring outputs and commands needed to reproduce them. Reference exact code and dependency versions. Separate scripted development traces from measured model results. Document remaining inconclusive or missing measurements with their scope.

**Hand off:** the reproduction package, Experiment A/B tables and error-analysis material to the team for the report; coordinate the demo with the LLM lead and include verifier's translation explanation. Each role supplies its own implementation details and evidence.

**Acceptance:** another teammate can validate the benchmark, rerun Experiment A and regenerate reported model scores from saved logs using the documented commands. The report includes repeat-level denominators, outcome breakdowns, structural reductions, resource use and illustrative replayed errors. The E2 demo exercises verifier, pipeline and scorer together.
