# SQL predicate simplification — team work plan

We will test whether an LLM can shorten a DuckDB `WHERE` predicate while preserving the rows selected under explicit schema constraints. Each role owns one part of the prototype and a set of benchmark families.

## 1. SQL-to-Z3 translation and equivalence checking

**Deliverable.** A checker that takes the schema, original predicate and candidate predicate. It parses the supported SQL with SQLGlot, counts predicate AST nodes and asks Z3 whether a legal row can distinguish the two predicates. For a disagreement, it replays the row in DuckDB and returns the row, both predicate truth values and both query outputs. The pipeline consumes this result.

**Benchmark ownership.** Eight families, 32 tasks including 8 seeds, so 24 new tasks. D3–D5 cover contradictions involving integer values or multiple columns. E1–E5 cover facts guaranteed by the schema. For each E task, keep a witness row that distinguishes the original and reference when the relevant `CHECK` is removed.

| Category | Family | One valid simplification | Starting reference |
| --- | --- | --- | --- |
| D. Contradictions and impossible branches | D3. An open interval contains no integer | `x > 4 AND x < 5` → `FALSE` | [PostgreSQL discrete ranges](https://www.postgresql.org/docs/17/rangetypes.html#RANGETYPES-DISCRETE) |
| D. Contradictions and impossible branches | D4. Equal columns have conflicting values | `(x = y AND x = 2 AND y = 3) OR w > 0` → `w > 0` | [Calcite filter tests](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1111-L1113) and [Brass & Goldberg, Error 8](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |
| D. Contradictions and impossible branches | D5. An ordering cycle contains a strict inequality | `(x <= y AND y < z AND z <= x) OR w > 0` → `w > 0` | [Jarke & Koch, transitivity](https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf#page=13) and [Brass & Goldberg, inconsistent subconditions](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |
| E. Simplification under schema constraints | E1. Schema guarantees a bound | `x >= 0 AND y > 2` → `y > 2` under `CHECK (x >= 0)` | [Jarke, §2](https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf#page=6) · [Brass & Goldberg, Error 8](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |
| E. Simplification under schema constraints | E2. Column ordering transfers a bound | `lo >= 10 AND hi >= 10` → `lo >= 10` under `CHECK (lo <= hi)` | [Jarke, §2](https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf#page=6) · [Brass & Goldberg, Error 8](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |
| E. Simplification under schema constraints | E3. Equal columns can be substituted | `a < 5 OR b = 5` → `a <= 5` under `CHECK (a = b)` | [Jarke, §2](https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf#page=6) · [Brass & Goldberg, Error 8](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |
| E. Simplification under schema constraints | E4. A finite domain has a shorter complement | `s = 1 OR s = 7` → `s != 3` under `CHECK (s = 1 OR s = 3 OR s = 7)` | [Jarke, §2](https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf#page=6) · [Brass & Goldberg, Error 8](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |
| E. Simplification under schema constraints | E5. A conditional rule implies a filter condition | `kind != 1 AND amount < 0` → `amount < 0` under `CHECK (kind != 1 OR amount >= 0)` | [Jarke, §2](https://archive.nyu.edu/bitstream/2451/14565/1/IS-84-51.pdf#page=6) · [Brass & Goldberg, Error 8](https://dbs.informatik.uni-halle.de/sqllint/qsic04.pdf#page=3) |

**First handoff.** Use the schema and original query in the E2 SQL block. Give the LLM lead sample checker results for three candidates: `start_day >= 10` is equivalent and smaller, `end_day >= 10` differs on the legal row with start 9 and end 10, and copying the original is equivalent at the same size. Then implement the check and replay.

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

## 2. LLM simplification pipeline

**Deliverable.** A command-line prototype that gives the model the schema, the original query and a generic instruction to simplify its filter. Log the first attempt before feedback. After a confirmed disagreement, provide the counterexample and its DuckDB results. Allow up to four model calls per run and accept a candidate only when the checker confirms equivalence and a strictly smaller predicate AST.

**Benchmark ownership.** Nine families, 36 tasks including 9 seeds, so 27 new tasks. A1–A5 cover Boolean structure, B1–B2 cover single-column comparisons and D1–D2 cover contradictions. In the examples below, replace `P`, `Q` and `R` with concrete SQL comparisons.

| Category | Family | One valid simplification | Starting reference |
| --- | --- | --- | --- |
| A. Boolean structure | A1. Repeated condition | `P AND P` → `P` | [Jarke & Koch, Table 2](https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf#page=13) |
| A. Boolean structure | A2. Absorption | `P OR (P AND Q)` → `P` | [SQLGlot absorption tests](https://github.com/tobymao/sqlglot/blob/089891f2521780e2c5b243005017af9db62f25a6/tests/fixtures/optimizer/simplify.sql#L291-L295) |
| A. Boolean structure | A3. Common factor | `(P AND Q) OR (P AND R)` → `P AND (Q OR R)` | [Jarke & Koch, distributivity](https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf#page=13) |
| A. Boolean structure | A4. Negation of two negated conditions | `NOT ((NOT P) AND (NOT Q))` → `P OR Q` | [Jarke & Koch, De Morgan and double negation](https://www.csd.uoc.gr/~hy460/pdf/computersurveys84.pdf#page=13) |
| A. Boolean structure | A5. Branch covered by two other branches | `(P AND Q) OR ((NOT P) AND R) OR (Q AND R)` → `(P AND Q) OR ((NOT P) AND R)` | [Consensus theorem](https://cc.ee.ntu.edu.tw/~jhjiang/instruction/courses/fall13-ld/unit03.pdf#page=7) |
| B. Single-column comparisons and bounds | B1. Stronger bound implies a weaker one | `x >= 8 AND x >= 3` → `x >= 8` | [Calcite filter tests](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1133-L1137) |
| B. Single-column comparisons and bounds | B2. Equality settles another comparison | `x = 6 AND x < 10` → `x = 6` | [SQLGlot, equality and bounds](https://github.com/tobymao/sqlglot/blob/089891f2521780e2c5b243005017af9db62f25a6/tests/fixtures/optimizer/simplify.sql#L1088-L1092) |
| D. Contradictions and impossible branches | D1. Incompatible equalities | `x = 2 AND x = 5` → `FALSE` | [Calcite filter tests](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1098-L1101) |
| D. Contradictions and impossible branches | D2. Conflicting bounds | `x >= 9 AND x < 4` → `FALSE` | [Calcite filter tests](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1151-L1155) |

**Start immediately with a dummy verifier.** The LLM lead can build the repair loop while the verifier lead implements SQL-to-Z3 and DuckDB replay. Write `dummy_verify(schema_sql, original_sql, candidate_sql)` using the two E2-01 results already in §1. Run the scripted wrong and corrected candidates, log both attempts and give the trace to the evaluation lead. When the real `verify` is ready, swap it into the same loop and rerun E2-01. Use A2 and D1 for additional development tests.

## 3. Benchmark and evaluation

**Deliverable.** A reviewed 100-task benchmark, a development/test split by family and an evaluation of the frozen pipeline. Check the verifier against independently labeled query pairs and exhaustive DuckDB results on small integer domains. Coordinate task formatting, provenance and structural deduplication across the three contributions.

**Benchmark ownership.** Eight families, 32 tasks including 8 seeds, so 24 new tasks. B3–B5 cover integer endpoints and type limits. C1–C5 cover unions and gaps. Choose boundary rows that reveal a plausible incorrect rewrite. For C1, for example, test the first value in a separate interval so a rewrite that drops the entire interval is caught.

| Category | Family | One valid simplification | Starting reference |
| --- | --- | --- | --- |
| B. Single-column comparisons and bounds | B3. Bounds pin one value | `x >= 6 AND x <= 6` → `x = 6` | [Calcite filter tests](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1078-L1079) |
| B. Single-column comparisons and bounds | B4. Excluding an endpoint makes a bound strict | `x >= 6 AND x != 6` → `x > 6` | [PostgreSQL range bounds](https://www.postgresql.org/docs/17/rangetypes.html#RANGETYPES-INCLUSIVITY) |
| B. Single-column comparisons and bounds | B5. The integer type guarantees a bound | `x <= 9223372036854775807` → `TRUE` | [MySQL constant folding at type boundaries](https://dev.mysql.com/doc/refman/8.4/en/constant-folding-optimization.html) |
| C. Unions and gaps in integer ranges | C1. Merge overlapping intervals | `(x >= 2 AND x <= 6) OR (x >= 5 AND x <= 9)` → `x >= 2 AND x <= 9` | [Calcite, `testSimplifyRange3`](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1619-L1635) |
| C. Unions and gaps in integer ranges | C2. Consecutive points form an interval | `x = 3 OR x = 4 OR x = 5` → `x >= 3 AND x <= 5` | [PostgreSQL discrete ranges](https://www.postgresql.org/docs/17/rangetypes.html#RANGETYPES-DISCRETE) |
| C. Unions and gaps in integer ranges | C3. Two ranges leave one point out | `x < 4 OR x > 4` → `x != 4` | [SQLGlot comparison input; team-derived rewrite](https://github.com/tobymao/sqlglot/blob/089891f2521780e2c5b243005017af9db62f25a6/tests/fixtures/optimizer/simplify.sql#L968-L969) |
| C. Unions and gaps in integer ranges | C4. An outer bound eliminates one branch | `(x <= 3 OR x >= 8) AND x >= 5` → `x >= 8` | [PostgreSQL, range intersection](https://www.postgresql.org/docs/17/functions-range.html#RANGE-OPERATORS-TABLE) |
| C. Unions and gaps in integer ranges | C5. An excluded point already lies in the gap | `(x <= 3 OR x >= 8) AND x != 5` → `x <= 3 OR x >= 8` | [Calcite, `testSimplifyRange`](https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1589-L1605) |

**First task.** The completed `B3-01.json` and `C1-01.json` records are supplied. Check their sample rows against the seed INSERTs, run both SELECTs and confirm their outputs match `expected_rows`. This completes the initial handoff.

**Later benchmark work.** Review candidate examples from [SQLSolver’s Spark tests](https://github.com/SJTU-IPADS/SQLSolver/blob/main/sqlsolver_data/db_rule_instances/spark_tests) and [SQLGlot’s simplify tests](https://github.com/tobymao/sqlglot/blob/089891f2521780e2c5b243005017af9db62f25a6/tests/fixtures/optimizer/simplify.sql). An adapted case can fill a family slot when its provenance is recorded. Remove structural duplicates before reporting a separate external set.

## Where to find the three example SQL queries
Open the SQL seed file inside data folder and search for the headings `-- E2.`, `-- B3.` and `-- C1.`. `E2`, `B3` and `C1` are family labels in that file. The suffix `-01` names the first task in a family, which is the runnable SQL block already under that heading. Thus `E2-01` is the jobs example, `B3-01` is the parcels example and `C1-01` is the samples example. Each block includes its schema, inserts, original query and smaller reference and can run on its own in DuckDB.

## Initial benchmark design

The planned benchmark contains **5 categories × 5 families per category × 4 tasks per family = 100 tasks**. A **category** names the kind of reasoning a filter requires. A **family** names a specific reason a rewrite works. A **task** gives the model one schema and one original query to simplify. For example, B covers comparisons on a single column, B3 covers two bounds that pin the column to one value, and one B3 task asks the model to shorten `x >= 6 AND x <= 6`. The hidden reference is `x = 6`.

| Category | What the tasks ask |
| --- | --- |
| A. Boolean structure | Can a repeated condition, a shared condition or a negation be simplified? |
| B. Single-column comparisons and bounds | Which comparisons still restrict a single integer column once its other bounds are known? |
| C. Unions and gaps in integer ranges | Which integer values are included when ranges overlap, meet or leave a gap? |
| D. Contradictions and impossible branches | Can any legal row satisfy all the conditions in this branch? |
| E. Simplification under schema constraints | Which conditions does an explicit row-level `CHECK` already guarantee? |

The owner of each family keeps its existing seed as task 1 and adds **three new input queries**, each with its own hidden reference. The plan tables illustrate each rule. The sql file inside data folder contains one original query and one smaller reference per family. That pair counts as one task. Across all families, 25 tasks are ready and 75 remain to be written. We reserve one whole family per category for development (20 tasks) and the other four for testing (80 tasks). This lets us evaluate performance on new families within each category. A candidate succeeds when it selects the same rows for every legal input and its predicate has fewer syntax-tree nodes. The hidden reference is one way to meet those criteria.

## 4. Shared benchmark and experiment decisions

| Role | Families | Existing seeds | New tasks | Total | Proposed development families |
| --- | ---: | ---: | ---: | ---: | --- |
| SQL-to-Z3 and equivalence checking | 8 | 8 | 24 | 32 | E2 |
| LLM pipeline | 9 | 9 | 27 | 36 | A2, D1 |
| Benchmark and evaluation | 8 | 8 | 24 | 32 | B3, C1 |
| **Total** | **25** | **25** | **75** | **100** | **5 families, 20 tasks** |

- **Common scope and task format.** Use one table, an unchanged projection and `BIGINT NOT NULL` columns. Predicates and row-local constraints may use integer literals, comparisons, `TRUE`, `FALSE`, `AND`, `OR` and `NOT`. The copyable JSON template is at the end of this section. Each task contains an ID, schema, original query, reviewed smaller reference, sample rows with expected results and a source or derivation. Keep the reference, samples, family label and reasoning out of the model prompt.
- **Variation within each family.** Extend each seed with three tasks that exercise the same underlying rule in meaningfully different settings. For C1, try a chain of three overlapping intervals, a separate interval with a one-integer gap, and an interval merge inside a branch alongside a condition on another column. Record what error each variant would expose.
- **Review and split.** The evaluation lead reviews the checker owner’s tasks, the checker owner reviews the pipeline owner’s tasks and the pipeline owner reviews the evaluation lead’s tasks. Keep each family together. Use A2, B3, C1, D1 and E2 for development (20 tasks), and reserve the other 20 families for testing (80 tasks). Tune the prompt on development tasks and freeze it before running the held-out set. The grouping and split draw on [Dr.Spider](https://github.com/awslabs/diagnostic-robustness-text-to-sql), [CLEVR](https://svl.stanford.edu/assets/publications/pdfs/johnson2017cvpr.pdf) and [Finegan-Dollak et al.](https://aclanthology.org/P18-1033/).
- **Shared integration case.** Run E2 through the three modules. The checker supplies a verdict or a replayed disagreement, the pipeline records each attempt, and evaluation reads the log to compute the result. This exercises the interface before the remaining 75 tasks are ready.
- **Scoring.** Run each held-out task three times. Report first-attempt equivalence, first-attempt verified simplification and verified simplification within four calls. Also report the AST reduction among verified simplifications to distinguish a partial simplification from a more complete one. Any smaller equivalent candidate earns credit, including one that improves on the hidden reference.

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
