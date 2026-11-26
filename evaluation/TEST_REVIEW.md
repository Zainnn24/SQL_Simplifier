# Martin's 32-task author packet: remaining test families

This packet indexes all 24 test tasks added in stage 1, round 3. Together with
B3-01 through B3-04 and C1-01 through C1-04, Martin now contributes 32 tasks:
eight families, four tasks per family, eight development tasks and 24 test tasks.
The accepted starting revision was e25c0e2f2856f1157feeec1a51d5f9b2c0da85ae.

All checks here are offline author checks. No evaluated model was called; these
test tasks, reference answers and sample results were not used to tune a prompt.
Independent approval and production-verifier confirmation remain outstanding.

## Index of the 24 test tasks

All records have `split="test"`. The linked JSON files retain the complete base
record format. References, samples, expected outputs and reasoning remain offline.
Node counts below are author calculations under README 4.2, not verifier results.
Every projected ID listed below occurs once; an empty output is written as none.

| Task | Samples | Original to reference nodes | Expected projected IDs |
| --- | ---: | --- | --- |
| [B4-01](B4-01.json) | 3 | 7 to 3 | 3 |
| [B4-02](B4-02.json) | 5 | 7 to 3 | 1, 2 |
| [B4-03](B4-03.json) | 6 | 11 to 7 | 3, 4, 5 |
| [B4-04](B4-04.json) | 6 | 11 to 7 | 3, 4, 5, 6 |
| [B5-01](B5-01.json) | 5 | 3 to 1 | 1, 2, 3, 4, 5 |
| [B5-02](B5-02.json) | 5 | 3 to 1 | 1, 2, 3, 4, 5 |
| [B5-03](B5-03.json) | 5 | 3 to 1 | none |
| [B5-04](B5-04.json) | 6 | 7 to 3 | 2, 3, 5, 6 |
| [C2-01](C2-01.json) | 5 | 11 to 7 | 2, 3, 4 |
| [C2-02](C2-02.json) | 6 | 15 to 7 | 2, 3, 4, 5 |
| [C2-03](C2-03.json) | 8 | 15 to 11 | 2, 3, 4, 7 |
| [C2-04](C2-04.json) | 5 | 12 to 8 | 1, 5 |
| [C3-01](C3-01.json) | 3 | 7 to 3 | 1, 3 |
| [C3-02](C3-02.json) | 3 | 8 to 3 | 2 |
| [C3-03](C3-03.json) | 6 | 11 to 7 | 1, 3 |
| [C3-04](C3-04.json) | 7 | 15 to 11 | 2, 3, 5, 6 |
| [C4-01](C4-01.json) | 6 | 11 to 3 | 5, 6 |
| [C4-02](C4-02.json) | 9 | 15 to 7 | 2, 3, 4 |
| [C4-03](C4-03.json) | 12 | 23 to 15 | 4, 5, 6, 9, 10, 11 |
| [C4-04](C4-04.json) | 10 | 15 to 7 | 7, 8, 9 |
| [C5-01](C5-01.json) | 10 | 19 to 15 | 2, 3, 5, 6, 10 |
| [C5-02](C5-02.json) | 10 | 23 to 15 | 2, 3, 8, 9 |
| [C5-03](C5-03.json) | 15 | 27 to 23 | 2, 3, 5, 6, 10, 11, 13, 14 |
| [C5-04](C5-04.json) | 10 | 15 to 11 | 1, 2, 5, 7, 8, 9, 10 |

The six `01` records preserve the existing SQL seeds. CREATE TABLE, original SQL
and reference SQL match their seed blocks token for token after ignoring comments,
case and whitespace. Sample rows match all named columns and duplicate counts.
C3-01 deliberately preserves the provenance distinction: SQLGlot supplies the
comparison input and retains both comparisons in its expected expression; the
shorter `stock_delta != 0` reference is the team's derivation.

## Existing development tasks: author node counts

The eight existing JSON records and SQL seed file are unchanged.

| Task | Original to reference nodes | Production verifier |
| --- | --- | --- |
| [B3-01](B3-01.json) | 7 to 3 | PENDING_EXPERIMENT_A |
| [B3-02](B3-02.json) | 8 to 3 | PENDING_EXPERIMENT_A |
| [B3-03](B3-03.json) | 15 to 11 | PENDING_EXPERIMENT_A |
| [B3-04](B3-04.json) | 11 to 7 | PENDING_EXPERIMENT_A |
| [C1-01](C1-01.json) | 19 to 11 | PENDING_EXPERIMENT_A |
| [C1-02](C1-02.json) | 23 to 7 | PENDING_EXPERIMENT_A |
| [C1-03](C1-03.json) | 23 to 15 | PENDING_EXPERIMENT_A |
| [C1-04](C1-04.json) | 23 to 15 | PENDING_EXPERIMENT_A |

The same production status applies to all 24 test tasks. Author node counts for
those tasks are stored separately from that status in the three JSON fixtures
under `tests/fixtures/`. A signed integer is one literal, parentheses add no nodes,
and each operator or column occurrence adds one. All 32 references strictly shrink.
No solver verdict is fabricated or inferred from sample agreement.

## Structural coverage and duplicate screening

| Family | Structural changes beyond its seed |
| --- | --- |
| B4 | Upper-endpoint exclusion, a bounded interval retaining its other endpoint, and an independent second-column OR branch |
| B5 | BIGINT minimum lower bound, an impossible strict comparison at the maximum, and a type bound combined with a real second-column restriction |
| C2 | Four consecutive negative points instead of the seed's three, a run plus an isolated point, and a run under NOT |
| C3 | A missing-point union under NOT, an independent second-column guard, and a finite window around the gap |
| C4 | An outer upper bound, three branches with two survivors, and simultaneous branch removal and clipping of the survivor |
| C5 | Two redundant gap exclusions, effective exclusions scoped to both live branches, and a local redundant exclusion beside an independent second-column branch |

The author screening heuristic uses DuckDB's parsed original predicates. It
normalizes column names, commuted AND/OR operands, comparison operand swaps and
literal ranks, while retaining integer adjacency and BIGINT endpoint roles.
No normalized duplicate group was found among the 32 originals. Retaining literal
relationships matters: a bound in a gap differs from a bound that clips a live
interval; MIN/MAX bounds are not ordinary constants. This screening is not a proof
that all possible structural duplicates have been excluded. Alvin still reviews
family membership, boundary intent and substantive coverage independently.

## Representative negative checks for the 18 new variants

Every row below was run with an unchanged original query and expectations, and a
wrong reference in a temporary JSON copy. The original passed, the wrong reference
failed by the listed multiset difference, and no task file changed. Each difference
below has count 1. Fixtures retain all 26 tested mutations, including additional
branch, window and scope errors; this table shows the first mutation per variant.

| Task | Wrong WHERE condition | Actual missing rows | Actual unexpected rows |
| --- | --- | --- | --- |
| B4-02 | `x <= -2` | none | [3] |
| B4-03 | `x > -2` | none | [6] |
| B4-04 | `x > 0` | [4], [5] | none |
| B5-02 | `x > -9223372036854775808` | [1] | none |
| B5-03 | `x >= 9223372036854775807` | none | [5] |
| B5-04 | `x <= 9223372036854775807` | none | [1], [4] |
| C2-02 | `x > -5 AND x < -2` | [2], [5] | none |
| C2-03 | `x >= 2 AND x <= 9` | none | [5], [6] |
| C2-04 | `NOT (x > -2 AND x < 0)` | none | [2], [4] |
| C3-02 | `x != 4` | [2] | [1], [3] |
| C3-03 | `x != 0` | none | [4], [5] |
| C3-04 | `x != 0` | none | [1], [7] |
| C4-02 | `(x >= -5 AND x <= -1) OR x >= 4` | none | [8], [9] |
| C4-03 | `x >= 6 AND x <= 9` | [4], [5], [6] | none |
| C4-04 | `x >= 1 AND x <= 10` | none | [5], [6] |
| C5-02 | `x >= 0 AND x <= 10` | none | [4], [5], [6], [7] |
| C5-03 | `(x >= -4 AND x <= 0) OR ((x >= 6 AND x <= 10) AND x != 8)` | none | [4] |
| C5-04 | `((x <= -2 OR x >= 4) OR enabled = 1) AND x != 1` | [5] | none |

## Reviews and review-feedback regressions

`benchmark_reviews.jsonl` contains one record per assigned task. The previous eight
records were preserved byte for byte, and 24 new `PENDING` records were appended:
`author_role="evaluation"`, `reviewer_role="llm"`, null notes and null
`check_removal_test`. At this author checkpoint there are 32 PENDING and zero
APPROVED records. No author or helper agent has approved a task for Alvin.

The development regression now accepts PENDING notes that are either text or null.
Temporary review data with a requested change passes. Temporary APPROVED records
with null, empty or whitespace-only notes are rejected, while a nonempty approval
note passes. Actual review records are not altered by these tests. Existing real
feedback or approvals must be retained when the packet is updated.

## Reproduction and observed results

Reuse the repository's `.venv`: Python 3.13.7, DuckDB 2.0.1, with the dependency
pinned in `evaluation/requirements.txt`. Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m evaluation.author_checks
.\.venv\Scripts\python.exe -m unittest discover -s evaluation/tests -v
```

The author command reuses `check-samples` with explicit Python argument lists:
eight `01` paths plus `--seed-file`, then all 32 paths with no seed argument.
It does not depend on shell wildcard expansion. It also checks seed SQL fidelity,
metadata, author sizes, duplicate flags and every stored wrong-reference mutation.
Expected negative mismatches are labeled as captured errors, not unexpected test
failures. Any unexpected mismatch raises an error and returns a nonzero exit code.

Observed author acceptance results:

- Structure: 32 unique task IDs; eight families of four; dev=8, test=24.
- Reviews: exactly one record per task, with 32 PENDING records.
- Seed samples: 24/24 checks passed across eight seeds.
- Seed SQL fidelity: 8/8 triples matched, including all six newly packaged seeds.
- Full collection samples: 64/64 query checks passed.
- New-variant negatives: 18/18 variants covered; 26/26 wrong references detected
  with the exact expected missing/unexpected row counts.
- Author size counts: 32/32 references strictly smaller.
- Full evaluation regression suite: 40/40 tests passed, zero failures.
- BIGINT literals and sample values: exact signed 64-bit integers, including both
  extreme endpoints and adjacent values; no float conversion is used.

## Remaining handoffs

Alvin approved the eight B3 and C1 development tasks. The 24 test tasks in
this packet still need his independent review.
David must deliver the production verifier/encoder and its validation evidence.
After Experiment A passes, all 32 references still need formal equivalence and
strict-size confirmation using that production implementation. This packet does
not certify the team's complete 100-task benchmark or authorize prompt tuning on
the held-out test families.
