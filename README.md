# SQL predicate simplification — team work plan

We will test whether an LLM can shorten a DuckDB `WHERE` predicate while preserving the rows selected under explicit schema constraints. Each role owns one part of the prototype and a set of benchmark families.

## 1. SQL-to-Z3 translation and equivalence checking

**Owner: verifier lead.** Receive the shared SQL scope, seed schemas, and independent fixtures and discrepancy reports from evaluation. Deliver `validate_input()` and `verify()` to the LLM and evaluation leads, and `encode_query()` to evaluation. Also deliver a direct-check CLI, the translation argument and regression tests. Evaluation owns `evaluate_encoded_row()` and its DuckDB comparison driver.

[Verifier deliverables](verifier/01_SQL_Verifier_Plan.md) describe implementation and acceptance checks. Start with E2-01: return results for a wrong candidate, a smaller equivalent candidate and a copy of the original. The agreed interfaces and examples are in section 4.

**Benchmark ownership:** D3–D5 and E1–E5, 32 tasks including 8 seeds and 24 new variants. For each E task, record a counterexample after removing the relevant CHECK.

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

## 2. LLM simplification pipeline

**Owner: LLM lead.** Receive `validate_input()` and `verify()` from the verifier lead and benchmark records from evaluation. Deliver the model adapter, generic prompt, four-call repair loop, single-task/batch CLI, configuration and JSONL logs to evaluation. The verifier supplies SQL validation, AST counts, equivalence decisions and confirmed counterexamples; the pipeline decides when to accept, retry or stop.

[LLM deliverables](LLM_pipeline/02_LLM_Pipeline_Plan.md) explain how to start with a scripted model, a preflight stub and `dummy_verify` using the exact shared signatures. Produce the E2 two-attempt trace before the real verifier is ready, then replace the stubs with its functions. Evaluation uses the delivered batch runner for the official experiment.

**Benchmark ownership:** A1–A5, B1–B2 and D1–D2, 36 tasks including 9 seeds and 27 new variants. Replace P, Q and R below with concrete comparisons.

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

## 3. Benchmark and evaluation

**Owner: evaluation lead.** Receive all three benchmark contributions, the verifier functions and encoder, and the LLM runner/configuration/logs. Deliver the benchmark validator and reviewed 100-task manifest; independent fixtures; `evaluate_encoded_row()` and the DuckDB differential driver; Experiment A results; the scorer, Experiment B results and reproduction materials.

[Evaluation deliverables](evaluation/03_Evaluation_Plan.md) start with the supplied [B3-01](evaluation/B3-01.json) and [C1-01](evaluation/C1-01.json) records. Build independent tests while the verifier is being implemented and score the scripted E2 trace while the live model adapter is being built.

**Benchmark ownership:** B3–B5 and C1–C5, 32 tasks including 8 seeds and 24 new variants. Choose boundary rows that catch plausible wrong rewrites, including surviving OR branches.

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

Candidate external examples remain available from [SQLSolver's Spark tests](https://github.com/SJTU-IPADS/SQLSolver/blob/main/sqlsolver_data/db_rule_instances/spark_tests) and [SQLGlot's simplify tests](https://github.com/tobymao/sqlglot/blob/089891f2521780e2c5b243005017af9db62f25a6/tests/fixtures/optimizer/simplify.sql). Preserve provenance and remove structural duplicates. SQLSolver is an optional source of cases; translator validation uses the team's encoder and independent DuckDB execution.

## Where to find the three example SQL queries

Open [the SQL seed file](data/SQL_Predicate_Family_Examples.sql) and search for the headings `-- E2.`, `-- B3.` and `-- C1.`. `E2`, `B3` and `C1` are family labels in that file. The suffix `-01` names the first task in a family, which is the runnable SQL block already under that heading. Thus `E2-01` is the jobs example, `B3-01` is the parcels example and `C1-01` is the samples example. Each block includes its schema, inserts, original query and smaller reference and can run on its own in DuckDB.

## Initial benchmark design

The planned benchmark contains **5 categories × 5 families per category × 4 tasks per family = 100 tasks**. A **category** names the kind of reasoning a filter requires. A **family** names a specific reason a rewrite works. A **task** gives the model one schema and one original query to simplify. For example, B covers comparisons on a single column, B3 covers two bounds that pin the column to one value, and one B3 task asks the model to shorten `x >= 6 AND x <= 6`. The hidden reference is `x = 6`.

| Category | What the tasks ask |
| --- | --- |
| A. Boolean structure | Can a repeated condition, a shared condition or a negation be simplified? |
| B. Single-column comparisons and bounds | Which comparisons still restrict a single integer column once its other bounds are known? |
| C. Unions and gaps in integer ranges | Which integer values are included when ranges overlap, meet or leave a gap? |
| D. Contradictions and impossible branches | Can any legal row satisfy all the conditions in this branch? |
| E. Simplification under schema constraints | Which conditions does an explicit row-level `CHECK` already guarantee? |

The owner of each family keeps its existing seed as task 1 and adds **three new input queries**, each with its own hidden reference. The plan tables illustrate each rule. The SQL seed file contains one original query and one smaller reference per family. That pair counts as one task. Across all families, 25 SQL seed pairs exist and 75 additional variants are planned. Two seeds also have standalone JSON records; the remaining records still need packaging and review. We reserve one whole family per category for development (20 tasks) and the other four for testing (80 tasks). This lets us evaluate performance on new families within each category. A candidate succeeds when it selects the same rows for every legal input and its predicate has fewer syntax-tree nodes. The hidden reference is one way to meet those criteria.

## 4. Shared benchmark and experiment decisions

This section is the common contract to implement. The role plans repeat the signatures they need and link here for full data definitions. These are planned interfaces and artifacts; this documentation does not claim they are already implemented.

### 4.1 Ownership and handoffs

| Producer | Recipient | Handoff |
| --- | --- | --- |
| Verifier | LLM and evaluation | `validate_input`, `verify`, `ValidationResult`, `CheckResult`, confirmed E2 results |
| Verifier | Evaluation | `encode_query`, `EncodedQuery`, `InputError`, translation argument and regression evidence |
| Evaluation | Verifier | Independent labeled pairs, DuckDB reference observations, reproducible discrepancy reports |
| Evaluation | Team | `evaluate_encoded_row`, `EncodedRowResult`, enumeration driver and Experiment A results |
| Each role | Evaluation | Assigned benchmark tasks, provenance, boundary samples and cross-review |
| Evaluation | LLM | Validated task records and family split; only schema and original SQL enter prompts |
| LLM | Evaluation | Single/batch runner, frozen configuration, attempt logs and run-end records |
| Evaluation | Team | Benchmark validation, scorer, Experiment B tables and reproduction/report materials |

The verifier lead owns the two verifier result types and the encoding interface. Evaluation owns benchmark/fixture/review formats and `EncodedRowResult`. The LLM lead writes configuration and logs in the format below; evaluation reads them. Changes to a shared contract must update this section and both producer and consumer plans together.

### 4.2 SQL scope and function signatures

Use one table with signed `BIGINT NOT NULL` columns, a fixed ordered list of projected columns, and an explicit WHERE predicate. Use `WHERE TRUE` when no filter is needed. Integer terms are columns or in-range signed literals. Predicates and row-local CHECKs use `=`, `!=`, `<`, `<=`, `>`, `>=`, TRUE, FALSE, AND, OR, NOT and parentheses. Reject arithmetic, casts, nullable columns, joins, subqueries, aggregates, extra query clauses and unsupported constraints. The candidate keeps the original table and ordered projection.

Count one node per operator, column occurrence or literal before algebraic simplification. A signed integer counts once; parentheses, identifiers inside a column node, SELECT wrappers and CHECK expressions add nothing. Thus `x >= -2` has 3 nodes, `NOT (x >= -2)` has 4 and a repeated conjunction has 7.

The verifier lead implements and exports these functions from `verifier`:

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

#### Verifier function semantics

`schema_sql` is one CREATE TABLE; each query argument is one complete SELECT. `validate_input` checks the schema and original query, counts the original predicate and checks schema satisfiability before the pipeline spends a model call. `verify` independently performs the same necessary validation and checks one candidate; callers need not invoke preflight first. Both share internal parsing, validation and encoding code.

#### Encoding validation and InputError

`encode_query` validates and translates one query without asking whether the schema is satisfiable. It therefore also encodes a syntactically supported contradictory CHECK. Unsupported SQL raises verifier-owned `InputError(ValueError)` with an actionable message (`str(error)`). The public verifier functions convert expected input errors to their result statuses. SQL parsing, AST counting and replay helpers remain the verifier lead's implementation choices.

#### EncodedQuery

`EncodedQuery` is a Python TypedDict containing live Z3 objects, not JSON:

| Field | Python type | Meaning |
| --- | --- | --- |
| `columns` | `dict[str, z3.ArithRef]` | Every declared column mapped to its symbolic Z3 integer |
| `schema_constraint` | `z3.BoolRef` | All declared CHECKs and signed BIGINT bounds |
| `predicate` | `z3.BoolRef` | The query's WHERE expression |
| `predicate_nodes` | `int` | Size under the shared rule |

The expressions in one result use exactly its column symbols and Z3 context. Production `verify` uses this same encoding path. When building a pair formula, align the original and candidate to one shared column mapping/context; fresh unrelated variables would make the equivalence question wrong.

#### Row evaluation interface

The evaluation lead implements and exports this function from `evaluation`:

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

It calls `encode_query`, fixes every column to the supplied integer and evaluates the returned schema and predicate expressions. It consumes the production translator; it does not implement a second SQL-to-Z3 translator. The row must have exactly the declared columns and integer values (not booleans or nulls). Out-of-range integers may be supplied as negative schema-legality tests and should make `schema_holds` false. A supported but contradictory schema can likewise be evaluated; do not reject it through a satisfiability preflight.

**Timeout contract.** `timeout_ms` is a positive integer taken from run/test configuration and applied to each Z3 solver check. It is not a whole-function deadline: schema and disagreement checks can each use that allowance. Freeze the chosen value before test runs. UNKNOWN or timeout remains inconclusive. The encoder performs no solver check and needs no timeout parameter. Model request timeout and the four-call generation cap are separate settings.

### 4.3 Result formats and counterexamples

Serializable results are ordinary dictionaries described by TypedDict. Every listed field is required, even when its value is null. Keep integer values exact, including signed 64-bit endpoints.

#### ValidationResult

| Field | JSON type | Meaning |
| --- | --- | --- |
| `status` | string | READY, INVALID_INPUT, UNSAT_SCHEMA or INCONCLUSIVE |
| `original_nodes` | integer or null | Original size when available; required non-null on READY |
| `diagnostic` | string or null | Null on READY; explanation on other statuses |

READY means supported input and a satisfiable schema. An always-false original is still valid. INVALID_INPUT identifies malformed/unsupported source SQL; UNSAT_SCHEMA means no legal row; INCONCLUSIVE means satisfiability could not be decided.

```json
{"status": "READY", "original_nodes": 7, "diagnostic": null}
```

#### CheckResult

| Field | JSON type | Meaning |
| --- | --- | --- |
| `status` | string | One of the statuses below |
| `original_nodes` | integer or null | Original predicate size when available |
| `candidate_nodes` | integer or null | Submitted candidate size when available |
| `diagnostic` | string or null | Null for EQUIVALENT/DIFFERENT; explanation otherwise |
| `witness` | object or null | Confirmed candidate counterexample only for DIFFERENT |

| Status | Required interpretation |
| --- | --- |
| EQUIVALENT | Schema is satisfiable and `C AND XOR(P,Q)` is UNSAT; both counts are integers |
| DIFFERENT | A SAT disagreement row was legally inserted and confirmed by DuckDB; both counts and witness are present |
| INVALID_INPUT | Source schema/original is malformed or unsupported |
| INVALID_CANDIDATE | Candidate failed parsing, binding, fragment or fixed-query validation |
| UNSAT_SCHEMA | Source schema admits no legal row |
| INCONCLUSIVE | A solver check returned UNKNOWN or timed out |
| REPLAY_ERROR | A SAT disagreement model failed insertion or its predicted observations were not confirmed; preserve details in diagnostic |

The pipeline accepts only EQUIVALENT with `candidate_nodes < original_nodes`. An unchanged original can be EQUIVALENT without being a successful simplification.

A **candidate counterexample** is a row legal under the original schema on which original and candidate select differently. `witness` has these required fields: `row` (all columns as integers), `original_truth` and `candidate_truth` (different booleans), and `original_rows` and `candidate_rows` (arrays of projected rows). Each projected row is an array of integers in SELECT order. Compare outputs as unordered multisets, retaining duplicates. The witness has no status of its own and is null for every status except DIFFERENT.

#### E2 request and results

For E2-01, the direct-check request includes the timeout so it can be passed as `verify(**request)`. The 5000 ms value here is an illustrative development setting, not a measured/frozen experiment choice.

```json
{
  "schema_sql": "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL, CHECK (start_day <= end_day))",
  "original_sql": "SELECT row_id FROM jobs WHERE start_day >= 10 AND end_day >= 10",
  "candidate_sql": "SELECT row_id FROM jobs WHERE end_day >= 10",
  "timeout_ms": 5000
}
```
One confirmed result is:

```json
{
  "status": "DIFFERENT",
  "original_nodes": 7,
  "candidate_nodes": 3,
  "diagnostic": null,
  "witness": {
    "row": {
      "row_id": 2,
      "start_day": 9,
      "end_day": 10
    },
    "original_truth": false,
    "candidate_truth": true,
    "original_rows": [],
    "candidate_rows": [
      [
        2
      ]
    ]
  }
}
```
For candidate `SELECT row_id FROM jobs WHERE start_day >= 10`, return:

```json
{
  "status": "EQUIVALENT",
  "original_nodes": 7,
  "candidate_nodes": 3,
  "diagnostic": null,
  "witness": null
}
```

Z3 may choose another legal distinguishing row. A copied original returns EQUIVALENT with counts 7 and 7.

#### EncodedRowResult

**Owner: evaluation lead.**

| Field | JSON type | Meaning |
| --- | --- | --- |
| `status` | string | EVALUATED, INVALID_INPUT, INCONCLUSIVE or EVALUATION_ERROR |
| `schema_holds` | boolean or null | Whether this particular row satisfies the encoded bounds and CHECKs |
| `predicate_truth` | boolean or null | Whether the encoded WHERE is true on this row |
| `diagnostic` | string or null | Null on EVALUATED; explanation otherwise |

On EVALUATED both truth fields are booleans, even when `schema_holds` is false. On other statuses both are null. INVALID_INPUT covers unsupported SQL or malformed row mappings; INCONCLUSIVE covers solver UNKNOWN/timeout; EVALUATION_ERROR covers unexpected failures or a result that remains symbolic despite complete row bindings. These are evaluation-tool outcomes, not candidate verdicts.

```json
{"status": "EVALUATED", "schema_holds": true, "predicate_truth": false, "diagnostic": null}
```

This example applies to the E2 original on row `(row_id=2, start_day=9, end_day=10)`. The false value means this row fails the predicate; it is not an equivalence verdict. Evaluation compares these observations with independent DuckDB execution.

### 4.4 Benchmark, review and verifier-fixture formats

#### Benchmark task records

Evaluation owns the loader/validator and integrates the manifest. Each author supplies one record per task; an original/reference pair is one task. All fields in the base task below are required and non-null. IDs/family/split and SQL/reasoning fields are strings; split is dev or test. `sample_rows` is a nonempty array of complete integer row objects; `expected_rows` is an array of projected integer rows and may be empty. The reference must be reviewed, equivalent and strictly smaller. Only clean `schema_sql` and `original_sql` enter the model prompt; remove comments and keep family labels, references, samples and reasoning offline.

The canonical base example matches the supplied [B3-01.json](evaluation/B3-01.json):

```json
{
  "task_id": "B3-01",
  "family": "B3",
  "split": "dev",
  "schema_sql": "CREATE TABLE parcels (row_id BIGINT NOT NULL, weight_kg BIGINT NOT NULL)",
  "original_sql": "SELECT row_id FROM parcels WHERE weight_kg >= 15 AND weight_kg <= 15",
  "reference_sql": "SELECT row_id FROM parcels WHERE weight_kg = 15",
  "sample_rows": [
    {
      "row_id": 1,
      "weight_kg": 14
    },
    {
      "row_id": 2,
      "weight_kg": 15
    },
    {
      "row_id": 3,
      "weight_kg": 16
    },
    {
      "row_id": 4,
      "weight_kg": 15
    }
  ],
  "expected_rows": [
    [
      2
    ],
    [
      4
    ]
  ],
  "derivation": "The two inclusive bounds pin weight_kg to 15. Both parcels with that weight qualify.",
  "source": "SQL_Predicate_Family_Examples.sql, B3. Adapted from Apache Calcite RexProgramTest.java, https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1078-L1079",
  "error_exposed": "Keeping only weight_kg >= 15 includes row 3 (weight 16). Keeping only weight_kg <= 15 includes row 1 (weight 14)."
}
```

[C1-01.json](evaluation/C1-01.json) uses the same format. Keep the existing SQL seed as variant 01.

#### B3-02 authoring example

The following drafted B3-02 example exercises the same rule under NOT at a negative endpoint (8 → 3 nodes); it still requires review:

```json
{
  "task_id": "B3-02",
  "family": "B3",
  "split": "dev",
  "schema_sql": "CREATE TABLE readings (row_id BIGINT NOT NULL, x BIGINT NOT NULL)",
  "original_sql": "SELECT row_id FROM readings WHERE NOT (x >= -2 AND x <= -2)",
  "reference_sql": "SELECT row_id FROM readings WHERE x != -2",
  "sample_rows": [
    {
      "row_id": 1,
      "x": -3
    },
    {
      "row_id": 2,
      "x": -2
    },
    {
      "row_id": 3,
      "x": -1
    }
  ],
  "expected_rows": [
    [
      1
    ],
    [
      3
    ]
  ],
  "derivation": "Team variant of B3-01. The two inclusive bounds pin x to -2; negation excludes that point.",
  "source": "SQL_Predicate_Family_Examples.sql, B3",
  "error_exposed": "Returning TRUE incorrectly includes row 2."
}
```

#### Review metadata

Store one separate `benchmark_reviews.jsonl` record per task, joined by `task_id`. Required fields are `task_id`, `author_role`, `reviewer_role` (strings; roles are verifier/llm/evaluation), `review_status` (PENDING or APPROVED), `review_notes` (string or null), and `check_removal_test` (object or null). Require a different reviewer and nonempty approval notes before freezing; E-family approval also requires a verified check-removal test. Existing seed JSON files remain valid base records and are not assumed reviewed.

```json
{"task_id": "B3-01", "author_role": "evaluation", "reviewer_role": "llm", "review_status": "PENDING", "review_notes": null, "check_removal_test": null}
```

A **counterexample after removing the CHECK** proves that a reference depends on that constraint. It must satisfy all remaining constraints and distinguish original from reference. It is an offline test, not a legal candidate counterexample under the original schema. An E2 `check_removal_test` has the following complete shape; every field is required:

```json
{
  "removed_check_sql": "CHECK (start_day <= end_day)",
  "schema_without_check_sql": "CREATE TABLE jobs (row_id BIGINT NOT NULL, start_day BIGINT NOT NULL, end_day BIGINT NOT NULL)",
  "row": {
    "row_id": 1,
    "start_day": 10,
    "end_day": 9
  },
  "original_truth": false,
  "reference_truth": true,
  "original_rows": [],
  "reference_rows": [
    [
      1
    ]
  ]
}
```

Name the exact CHECK occurrence to remove (use named constraints when identical clauses would be ambiguous), and verify that the supplied schema differs only by that removal. `row` has all declared integer columns; truth fields are booleans and output fields are projected-row arrays. Require different truth values and outputs. For non-E tasks use null unless a constraint-dependence test is relevant.

#### Independent verifier cases

Evaluation supplies `verifier_cases.jsonl`. Each record requires `case_id`, `schema_sql`, `original_sql`, `candidate_sql`, `expected_status`, `justification`, `counterexample` and `enumeration`. The first six are strings; expected_status is an independently labeled CheckResult status, normally EQUIVALENT, DIFFERENT or an input/control status. `counterexample` is a witness-shaped object for DIFFERENT, otherwise null; it need not match the solver's chosen row. `enumeration` is null or an object with `domains` (column → nonempty list of integer values) and `fixed_values` (column → integer). The two maps are disjoint and together cover every declared column. Use null enumeration for malformed/unsupported SQL controls. For equivalent pairs require a logical justification; for unequal pairs independently check the distinguishing row.

```json
{
  "case_id": "boundary-01",
  "schema_sql": "CREATE TABLE t (row_id BIGINT NOT NULL, x BIGINT NOT NULL)",
  "original_sql": "SELECT row_id FROM t WHERE x >= 0",
  "candidate_sql": "SELECT row_id FROM t WHERE x > 0",
  "expected_status": "DIFFERENT",
  "justification": "At x = 0 the inclusive predicate selects the row and the strict predicate rejects it.",
  "counterexample": {
    "row": {
      "row_id": 1,
      "x": 0
    },
    "original_truth": true,
    "candidate_truth": false,
    "original_rows": [
      [
        1
      ]
    ],
    "candidate_rows": []
  },
  "enumeration": {
    "domains": {
      "x": [
        -2,
        -1,
        0,
        1,
        2
      ]
    },
    "fixed_values": {
      "row_id": 1
    }
  }
}
```

`expected_status` labels the unrestricted pair under the recorded schema. Derive a separate bounded-domain verdict from enumeration and compare that with `verify` under the same explicit domain constraints; a pair that differs outside the small domain may agree inside it. Keep already-simple inputs, unsupported SQL, inconsistent schemas and deliberate wrong candidates outside the 100 simplification tasks.

### 4.5 Run configuration and JSONL logs

The LLM lead owns the single/batch runner and log writer. Evaluation owns the scorer and executes the frozen experiment with that runner. CLI module names are `python -m verifier`, `python -m LLM_pipeline` and `python -m evaluation`, matching the repository folders once their entry points are implemented.

#### RunConfig

Required fields are shown below. IDs, versions and policy names are strings; `sampling` is an object of provider-supported parameters; `dependency_versions` maps package names to exact versions; timeouts are positive integers; `max_model_calls` is 4. `run_kind` is scripted or model. Store an immutable config file by config_id, its prompt/parser artifacts and the referenced benchmark snapshot. Credentials are excluded. The scripted example has illustrative timeouts and explicit version placeholders; replace every placeholder with actual values before any frozen live run.

```json
{
  "config_id": "e2-scripted-v1",
  "run_kind": "scripted",
  "model_id": "scripted-e2",
  "sampling": {},
  "prompt_version": "generic-v1",
  "output_parser": "sql-or-single-fence-v1",
  "solver_timeout_ms": 5000,
  "model_timeout_s": 60,
  "max_model_calls": 4,
  "transport_policy": "no_automatic_retry",
  "benchmark_version": "e2-seed-v1",
  "code_revision": "RECORD_COMMIT",
  "dependency_versions": {
    "python": "RECORD_VERSION",
    "sqlglot": "RECORD_VERSION",
    "z3-solver": "RECORD_VERSION",
    "duckdb": "RECORD_VERSION"
  }
}
```

The parser trims whitespace and optionally removes exactly one enclosing Markdown SQL fence; preserve raw text. Leave SQL syntax/statement-count/fragment validation to `verify`. An unextractable response is a pipeline OUTPUT_ERROR. Each dispatched generation request consumes a call, including invalid outputs and failed requests. Disable automatic provider/SDK retries for this policy; do not silently spend extra calls. A transport failure ends the run as API_ERROR.

#### JSONL record formats

**Common log identity.** Every record has `record_type`, `run_id`, `task_id`, `split`, `repeat`, `config_id`. The first, second, third, fourth and sixth are strings; repeat is a positive integer. Records join to the frozen task and configuration. `record_type` is attempt or run_end. All fields below are required; only fields explicitly marked nullable may be null.

| Attempt field | Type and rule |
| --- | --- |
| `attempt_no` | Integer 1–4, unique within run_id; counts dispatched requests |
| `outcome` | CHECKED, OUTPUT_ERROR, API_ERROR or INCOMPLETE |
| `raw_response` | String or null if no response was obtained |
| `candidate_sql` | Exact submitted SQL string or null if none could be extracted |
| `check_result` | Full CheckResult for CHECKED; null otherwise |
| `diagnostic` | Null for CHECKED (use check_result.diagnostic); explanation for other outcomes |
| `feedback` | String actually sent with this request about the preceding attempt; null on attempt 1 |
| `input_tokens`, `output_tokens` | Nonnegative integers or null when unavailable; scripted examples use zero |
| `elapsed_ms` | Nonnegative number for this attempt including generation and verification |

Record a completed attempt before issuing the next request. `feedback` describes what this attempt received, so the log does not claim that a planned next message was already sent. Preserve raw responses, failures and copies. Journal dispatches before sending requests; after a crash reconcile them before resuming, and record an unresolved dispatched request as INCOMPLETE rather than generating again blindly.

| Run-end field | Type and rule |
| --- | --- |
| `preflight_result` | Full ValidationResult, including non-READY outcomes with zero calls |
| `terminal_reason` | ACCEPTED, BUDGET_EXHAUSTED, INVALID_INPUT, UNSAT_SCHEMA, INCONCLUSIVE, REPLAY_ERROR, API_ERROR or INCOMPLETE |
| `accepted` | Boolean; true only for ACCEPTED |
| `selected_sql` | Accepted SQL for ACCEPTED; original SQL for BUDGET_EXHAUSTED; null for other terminal reasons |
| `calls_used` | Integer 0–4; includes every dispatched request and equals the attempt-record count after reconciliation |
| `input_tokens`, `output_tokens` | Sum of known counts, or null if no count is available; zero for zero-call runs |
| `token_accounting_complete` | Boolean; true only when usage is known for every dispatched request |
| `elapsed_ms` | Nonnegative total run duration including preflight |
| `diagnostic` | String or null; preserve terminal failures and incomplete-run explanation |

There is exactly one final run_end record per run_id. Non-READY preflight ends with its status, no attempts and zero calls. Stop on solver INCONCLUSIVE or REPLAY_ERROR. Retry DIFFERENT, INVALID_CANDIDATE, OUTPUT_ERROR or an equivalent candidate that did not shrink while calls remain; after the fourth such outcome return the original with BUDGET_EXHAUSTED. A repeated candidate still consumes a call. API_ERROR is a pipeline outcome, never a verifier status.

#### E2 scripted JSONL trace

The [E2 request and results](#e2-request-and-results) and [RunConfig example](#runconfig) identify this as scripted development data. Times below are illustrative. The JSONL has one JSON object per line: two attempts, then one run_end.

```jsonl
{"record_type": "attempt", "run_id": "e2-scripted-r1", "task_id": "E2-01", "split": "dev", "repeat": 1, "config_id": "e2-scripted-v1", "attempt_no": 1, "outcome": "CHECKED", "raw_response": "SELECT row_id FROM jobs WHERE end_day >= 10", "candidate_sql": "SELECT row_id FROM jobs WHERE end_day >= 10", "check_result": {"status": "DIFFERENT", "original_nodes": 7, "candidate_nodes": 3, "diagnostic": null, "witness": {"row": {"row_id": 2, "start_day": 9, "end_day": 10}, "original_truth": false, "candidate_truth": true, "original_rows": [], "candidate_rows": [[2]]}}, "diagnostic": null, "feedback": null, "input_tokens": 0, "output_tokens": 0, "elapsed_ms": 50}
{"record_type": "attempt", "run_id": "e2-scripted-r1", "task_id": "E2-01", "split": "dev", "repeat": 1, "config_id": "e2-scripted-v1", "attempt_no": 2, "outcome": "CHECKED", "raw_response": "SELECT row_id FROM jobs WHERE start_day >= 10", "candidate_sql": "SELECT row_id FROM jobs WHERE start_day >= 10", "check_result": {"status": "EQUIVALENT", "original_nodes": 7, "candidate_nodes": 3, "diagnostic": null, "witness": null}, "diagnostic": null, "feedback": "Your candidate SELECT row_id FROM jobs WHERE end_day >= 10 changes the result. Legal row: row_id=2, start_day=9, end_day=10. Original truth=false, candidate truth=true; original rows=[], candidate rows=[[2]]. Revise against the original schema and query, preserving the allowed syntax and reducing predicate size.", "input_tokens": 0, "output_tokens": 0, "elapsed_ms": 10}
{"record_type": "run_end", "run_id": "e2-scripted-r1", "task_id": "E2-01", "split": "dev", "repeat": 1, "config_id": "e2-scripted-v1", "preflight_result": {"status": "READY", "original_nodes": 7, "diagnostic": null}, "terminal_reason": "ACCEPTED", "accepted": true, "selected_sql": "SELECT row_id FROM jobs WHERE start_day >= 10", "calls_used": 2, "input_tokens": 0, "output_tokens": 0, "token_accounting_complete": true, "elapsed_ms": 70, "diagnostic": null}
```

The scorer should obtain first-equivalent = 0, first-smaller = 0, final success = 1, recovery numerator/denominator = 1/1, calls = 2 and nodes removed = 4. Keep scripted and Experiment A traces outside measured model results. Also deliver a copy-on-first-attempt trace: first-equivalent = 1 and first-smaller = 0.

### 4.6 Benchmark split, review and integration milestones

| Role | Families | Existing seeds | New tasks | Total | Development families |
| --- | ---: | ---: | ---: | ---: | --- |
| SQL-to-Z3 and equivalence checking | 8 | 8 | 24 | 32 | E2 |
| LLM pipeline | 9 | 9 | 27 | 36 | A2, D1 |
| Benchmark and evaluation | 8 | 8 | 24 | 32 | B3, C1 |
| **Total** | **25** | **25** | **75** | **100** | **5 families, 20 tasks** |

- **Variation within each family.** Extend each seed with three tasks that exercise the same underlying rule in meaningfully different settings. For C1, try a chain of three overlapping intervals, a separate interval with a one-integer gap, and an interval merge inside a branch alongside a condition on another column. Record what error each variant would expose.
- **Review and split.** The evaluation lead reviews the checker owner’s tasks, the checker owner reviews the pipeline owner’s tasks and the pipeline owner reviews the evaluation lead’s tasks. Keep each family together. Use A2, B3, C1, D1 and E2 for development (20 tasks), and reserve the other 20 families for testing (80 tasks). Tune the prompt on development tasks and freeze it before running the held-out set. The grouping and split draw on [Dr.Spider](https://github.com/awslabs/diagnostic-robustness-text-to-sql), [CLEVR](https://svl.stanford.edu/assets/publications/pdfs/johnson2017cvpr.pdf) and [Finegan-Dollak et al.](https://aclanthology.org/P18-1033/).
- **Scoring.** Run each held-out task three times. Report first-attempt equivalence, first-attempt verified simplification and verified simplification within four calls. Also report the AST reduction among verified simplifications to distinguish a partial simplification from a more complete one. Any smaller equivalent candidate earns credit, including one that improves on the hidden reference.


Keep provenance, independent review, legal samples and a strictly smaller reference for all 100 tasks. Validate exactly 25 families, four tasks per family, unique IDs and disjoint family sets across the 20 development and 80 test tasks. Flag structural duplicates rather than counting renamed copies as new coverage. Compare projected results as multisets, including duplicate projected rows and multi-column projection controls.

| Milestone | Concrete handoff and acceptance |
| --- | --- |
| E2 development integration | Verifier supplies READY, DIFFERENT, smaller EQUIVALENT and copied-original results; LLM produces the two-attempt log; evaluation reproduces the stated scores without a model call |
| Translator validation | Verifier supplies encode_query; evaluation supplies evaluate_encoded_row, small-domain enumeration and DuckDB comparisons; discrepancies return to verifier with reproducing inputs |
| Experiment A complete | Independent pair labels, schema controls, bounded-domain decisions and BIGINT endpoint tests agree; investigate any verdict or replay discrepancy before Experiment B |
| Development pilot and freeze | Use only the 20 development tasks to adjust the model/prompt/parser/settings. Freeze benchmark, code/dependencies, configuration, timeout and failure policy |
| Experiment B | Evaluation invokes the LLM runner for 80 held-out tasks × 3 repetitions = 240 scheduled runs, each with a fresh model context and at most four calls |
| Reproduction and demo | Evaluation assembles tables and report materials; verifier supplies the translation argument; LLM supplies the runnable E2 demo. Scores reproduce from saved logs without new model calls |

Report each repetition over its 80 scheduled tasks: first equivalence, first verified simplification, success within four calls, recovery among first-attempt DIFFERENT runs, accepted AST reductions, and calls/tokens/time including failures. Show denominators, missing runs, invalid outputs, inconclusives and replay discrepancies. Any smaller equivalent answer earns credit; the reference is not an exact-match target. Recovery describes the retry process, not the causal effect of feedback.
