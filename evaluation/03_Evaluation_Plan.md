# Task 3 · Build the benchmark and evaluate the verifier and LLM

**First task.** Check the sample rows and expected query outputs for the two completed JSON records, `B3-01` and `C1-01`, in §1. The independent verifier fixtures and the scorer for the E2 trace come later in Experiments A and B.

| You own | You receive | You hand off |
| --- | --- | --- |
| Benchmark format, review, family split, independent verifier evaluation (Experiment A), frozen LLM evaluation (Experiment B) | Task contributions from both leads, the direct checker, model attempt logs and final run records | A validated 100-task benchmark, separate controls and verifier fixtures, scoring scripts, results tables and replayable error examples |
| Families B3–B5 and C1–C5 | Eight existing SQL seeds | 32 tasks including 24 new variants, reviewed by the pipeline lead. You review the verifier lead’s 32 tasks and integrate all 100. |

Use the common JSON fields in §1 when exchanging benchmark tasks. The command names in later sections are suggested implementation choices. The counts, split and measurement goals follow the project documents.

## 1. Check the two prepared seed records

Open the SQL seed file in data folder and search for the headings `-- E2.`, `-- B3.` and `-- C1.`. `E2`, `B3` and `C1` are family labels in that file. The suffix `-01` names the first task in a family, which is the runnable SQL block already under that heading. Thus `E2-01` is the jobs example, `B3-01` is the parcels example and `C1-01` is the samples example. Each block includes its schema, inserts, original query and smaller reference and can run on its own in DuckDB.

Use one record per simplification task. Store the schema and original query as the model-facing payload; join the offline review fields by task ID. The runner may use the ID to locate a task. The prompt builder receives only the clean schema and original SQL. A seed’s original and reference are **one** task.

### B3-01 and C1-01 ready for review

The complete records are also supplied as `B3-01.json` and `C1-01.json` below and in the evaluation folder. For this first task:

1. Compare every `sample_rows` entry with the INSERT under the matching SQL heading, including the row ID.
2. Load those rows into a fresh DuckDB table created with `schema_sql`. Run `original_sql` and `reference_sql` separately. Compare each output with `expected_rows` as an unordered multiset, preserving repeated rows.

| Record | Input rows | Expected row IDs from each SELECT | Boundary to check |
| --- | ---: | --- | --- |
| `B3-01` | 4 | 2, 4 | Both parcels of weight 15 qualify. Weights 14 and 16 fall outside the filter. |
| `C1-01` | 10 | 2, 3, 4, 5, 6, 7, 10 | Reading 40 qualifies in the separate interval. Readings 26 and 39 fall in the gap. |

**B3-01 · Bounds pin one value**

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

**C1-01 · Merge overlapping intervals**

```json
{
  "task_id": "C1-01",
  "family": "C1",
  "split": "dev",
  "schema_sql": "CREATE TABLE samples (row_id BIGINT NOT NULL, reading BIGINT NOT NULL)",
  "original_sql": "SELECT row_id FROM samples WHERE (reading >= 10 AND reading <= 20) OR (reading >= 18 AND reading <= 25) OR reading >= 40",
  "reference_sql": "SELECT row_id FROM samples WHERE (reading >= 10 AND reading <= 25) OR reading >= 40",
  "sample_rows": [
    {"row_id": 1, "reading": 9},
    {"row_id": 2, "reading": 10},
    {"row_id": 3, "reading": 17},
    {"row_id": 4, "reading": 18},
    {"row_id": 5, "reading": 20},
    {"row_id": 6, "reading": 21},
    {"row_id": 7, "reading": 25},
    {"row_id": 8, "reading": 26},
    {"row_id": 9, "reading": 39},
    {"row_id": 10, "reading": 40}
  ],
  "expected_rows": [[2], [3], [4], [5], [6], [7], [10]],
  "derivation": "The inclusive intervals [10, 20] and [18, 25] overlap, so their union is [10, 25]. The separate interval starting at 40 also qualifies.",
  "source": "SQL_Predicate_Family_Examples.sql, C1. Adapted from Apache Calcite RexProgramTest.java, https://github.com/apache/calcite/blob/57ad4ffb1ed9b86d18fcfd7627fdf5c4227bc172/core/src/test/java/org/apache/calcite/rex/RexProgramTest.java#L1619-L1635",
  "error_exposed": "Dropping reading >= 40 loses row 10 (reading 40). Extending the merged interval through the gap incorrectly includes row 8 (26) or row 9 (39)."
}
```

### Later authoring example and family split

The following worked example is a proposed new B3 development variant. The original seed `B3-01` uses inclusive bounds with a positive endpoint. Here the same reasoning appears under NOT at a negative endpoint; the middle row catches a rewrite that mistakenly includes that value.

```json
{
  "task_id": "B3-02",
  "family": "B3",
  "split": "dev",
  "schema_sql": "CREATE TABLE readings (row_id BIGINT NOT NULL, x BIGINT NOT NULL)",
  "original_sql": "SELECT row_id FROM readings WHERE NOT (x >= -2 AND x <= -2)",
  "reference_sql": "SELECT row_id FROM readings WHERE x != -2",
  "sample_rows": [{"row_id": 1, "x": -3}, {"row_id": 2, "x": -2}, {"row_id": 3, "x": -1}],
  "expected_rows": [[1], [3]],
  "derivation": "Team variant of B3-01. The two inclusive bounds pin x to -2; negation excludes that point.",
  "source": "SQL_Predicate_Family_Examples.sql, B3",
  "error_exposed": "Returning TRUE incorrectly includes row 2."
}
```

For E tasks, require an additional offline record naming the CHECK to remove, the relaxed-schema witness and the two expected truth values. Compare query outputs as unordered multisets of projected rows, keeping duplicates. The verifier’s size rule gives the proposed B3-02 variant 8 → 3 nodes.

| Owner | Families | Seeds + new | Total | Development families |
| --- | --- | ---: | ---: | --- |
| Verifier | D3–D5, E1–E5 | 8 + 24 | 32 | E2 |
| LLM pipeline | A1–A5, B1–B2, D1–D2 | 9 + 27 | 36 | A2, D1 |
| Benchmark and evaluation | B3–B5, C1–C5 | 8 + 24 | 32 | B3, C1 |
| **All** | **25 families** | **25 + 75** | **100** | **5 whole families / 20 tasks** |

Assign variants 01–04 to the same family and split. Reserve the remaining **20 families / 80 tasks** for final testing. Keep already-simple inputs, unsupported inputs, inconsistent schemas and deliberately wrong verifier candidates in separate files. The main benchmark is designed to have a reviewed smaller equivalent answer for every task.

## 2. Form your eight families and review the full benchmark

Use the seed SQL as variant 01. The directions below are suggestions for variants 02–04. For every task, construct a legal sample that exposes a plausible wrong rewrite. Record the specific larger or smaller set of rows that mistake would select.

| Family | Variant 02 | Variant 03 | Variant 04 |
| --- | --- | --- | --- |
| B3 · bounds pin one value **(dev)** | Use the negated, negative-endpoint example above | Put the pinned interval beside a live range | Pin one column while retaining an independent comparison on another |
| B4 · exclude an endpoint | Exclude an upper endpoint | Turn an inclusive bound into a strict one inside a two-sided interval | Keep a live OR branch beside an endpoint exclusion |
| B5 · BIGINT type limit | Use the signed minimum lower bound | Use a strict comparison beyond which no legal value exists, e.g. `x > 9223372036854775807` | Put an always-true type bound under AND with a genuine restriction |
| C1 · merge overlapping intervals **(dev)** | Chain three overlapping intervals | Keep a separate interval across a one-integer gap | Merge intervals inside a branch controlled by a second column |
| C2 · consecutive points | Use consecutive negative integers | Preserve one isolated point beside a consecutive run | Put the run under NOT and check both endpoints |
| C3 · one missing point | Reverse the construction under NOT | Retain a second-column condition around the one-point gap | Place the one-point gap inside a bounded window |
| C4 · outer bound removes a branch | Use an upper bound to eliminate the right branch | Use three branches with two survivors | Use an outer bound that also clips the surviving interval |
| C5 · excluded point in a gap | Remove two exclusions inside one gap | Preserve a second exclusion inside a live range | Put the redundant exclusion inside one branch, with another independent branch |

**Review procedure for each submission**

1. Run the DDL in a fresh DuckDB database. Insert the legal samples; compare the original and hidden reference to the **author’s expected multiset**. Check endpoints, values just outside them and any surviving OR branch. In the existing C1 seed, reading 40 must still be returned; a candidate that merges the first two intervals and drops the separate tail fails on this row.
2. Ask a second person to explain why the reference is equivalent on every legal row and why the chosen variation still belongs to its family. After Experiment A, run the checker on each reference and enforce strict size reduction. Sample agreement alone cannot establish this property.
3. Record the upstream rule/test and what the author adapted, or the seed and derivation for a team variant. Flag likely duplicates using a normalized predicate structure, accounting for renamed columns, commuted operands and literal relationships. Inspect flagged tasks with their schema and boundary intent. Replace variants that merely rename identifiers or change constants without exercising a new case.
4. Apply the review rotation: you review the verifier lead’s families, the verifier lead reviews the pipeline lead’s families, and the pipeline lead reviews yours. Validate 25 unique family IDs, exactly four tasks per family, disjoint family sets across splits, unique task IDs, complete fixtures and 20/80 totals. When adapting an external test, preserve its provenance; a structurally duplicated example contributes only once to reported coverage.

## 3. Run Experiment A — can we trust the verifier?

Build expected answers **independently** of the SQL-to-Z3 translator. For known-equivalent pairs write a short logical justification; for unequal pairs supply a hand-checked legal distinguishing row. Keep a labeled verdict even if the solver finds a different witness. Include controls for invalid SQL, unsupported syntax, a changed projection and an inconsistent CHECK.

| Fixture to build | Independent answer | Defect it can expose |
| --- | --- | --- |
| E2 with the ordering CHECK, original vs. `start_day >= 10` | Equivalent because end ≥ start ≥ 10 | Constraint missing or mistranslated |
| Same pair with that CHECK removed | Unequal on start 10, end 9 | Stale constraint accidentally reused |
| `x >= 0` vs. `x > 0`; nested NOT; swapped column comparisons | Label each pair separately and evaluate the boundary row | Strict/inclusive confusion, precedence or swapped operands |
| `x <= 9223372036854775807` vs. TRUE; `x > -9223372036854775808` vs. TRUE | First equivalent; second unequal at the signed minimum | Bound omission, literal sign or precision error |
| `CHECK (x < 0)` and `CHECK (x >= 0)` together | Schema admits no row | Accidental acceptance through vacuous equivalence |

**Exhaustive differential check.** Start with one or two relevant columns and the domain `{-2,-1,0,1,2}`. Enumerate all assignments. Compare DuckDB and the encoder on schema legality; for each legal row compare each predicate’s truth value in DuckDB with the encoded value for that same fixed row. Then determine pair equivalence by asking whether any legal enumerated row differs. Compare with the checker using explicit domain bounds on the same columns. If there are no legal rows, the expected status is `UNSAT_SCHEMA`. Test full-width BIGINT endpoints in separate fixtures.

Record two wrong-verdict counts (unequal labeled equivalent; equivalent labeled unequal), inconclusive checks, schema/control outcomes and row-level translation mismatches. For the replay rate, use **all SAT disagreement models** as the denominator, including attempts whose replay failed. Preserve the schema, two queries and row for every discrepancy. Resolve verdict/replay discrepancies with the verifier lead and rerun affected tests before starting Experiment B.

## 4. Run Experiment B — how well does the model simplify?

1. Pilot on the 20 development tasks. First score the scripted E2 trace: `DIFFERENT` at attempt 1, then `EQUIVALENT` and 7 → 3 at attempt 2. Expected values are 0 first-equivalent, 0 first-smaller, 1 final success, 1 recovery, 2 calls and 4 nodes removed. Check a copy-on-first-attempt trace separately. Store scripted and verifier-fixture runs outside measured model results.
2. Freeze the benchmark manifest, prompt, model ID, sampling settings, output parser, verifier/software versions, solver timeout, four-call cap and failure-handling policy. Keep a configuration ID on every result. Only development tasks inform adjustments.
3. Run the frozen pipeline three times per held-out task (80 × 3 = 240 scheduled runs). Reset model context each time. Use records keyed by task, repetition and attempt. Reconcile expected IDs and terminal records before computing totals. Keep service failures and replay discrepancies visible; record missing runs and their reason.
4. Build the following table **for each repetition over its 80 scheduled tasks**. Report counts with denominators. If pooling across 240, label it as repeated runs of the same 80 tasks. Break down by category/family and use real replayed model errors for the qualitative analysis.

| Measure | Calculation |
| --- | --- |
| First-attempt equivalence | First outcome `EQUIVALENT` / 80. A copy counts. |
| First-attempt verified simplification | First outcome `EQUIVALENT` with fewer nodes / 80. |
| Verified simplification within four calls | Runs ending `ACCEPTED` by call 4 / 80. Returning the original after exhaustion earns zero. |
| Recovery after a confirmed wrong first answer | First-attempt `DIFFERENT` runs that later succeed / all first-attempt `DIFFERENT` runs. Show numerator and denominator; report N/A for a zero denominator. |
| Amount of simplification | For accepted runs, report original minus accepted nodes and percentage reduction relative to original. State the accepted-run count; the hidden reference supplies one valid target. |
| Outcomes and resource use | Tabulate confirmed disagreements, invalid candidates, inconclusives, replay errors, infrastructure issues and missing data. Label counts by attempt or by run. Report calls, elapsed time and available token usage across runs, including failures. |

Do not count deliberate Experiment A errors as LLM mistakes. When describing recovery, show the original, first candidate, actual replayed row and accepted revision when available. These traces describe what happened during retries; the project has one pipeline and does not estimate the causal effect of a feedback strategy.

### Make the results reproducible

| Suggested entry point | Input | Output to inspect |
| --- | --- | --- |
| `python -m evaluation validate-benchmark --manifest benchmark.jsonl` | All authored records, split and review metadata | Per-task fixture/reference failures, counts by owner and split, duplicate flags |
| `python -m evaluation check-verifier --fixtures verifier_cases.jsonl` | Independent labels and enumeration domains, plus the real checker | Verdict confusion counts, row-level mismatches, SAT models and confirmed replays |
| `python -m evaluation score --manifest benchmark.jsonl --logs results/` | Frozen test IDs, attempt logs and terminal records | Per-repeat results, missing/duplicate runs, family breakdowns and traceable error examples |

## 5. Sign-off

- [ ] One validation command checks the schema of all records, provenance, independent review, sample outputs, reference sizes and the frozen split.
- [ ] Experiment A produces its verdict table, bounded-domain comparison and replay counts from saved fixtures. Every discrepancy is traceable.
- [ ] The scoring script reproduces the E2 expected measurements and builds the three held-out result tables from saved logs without another model call.
- [ ] The final report includes the benchmark manifest, frozen configuration, repeat-level denominators, outcome breakdowns, structural reductions, resource use and illustrative replayed errors. Include any unresolved or missing measurements with their scope.
