# B3 and C1 development review packet

This packet contains eight development tasks for independent review by Alvin
(the LLM lead). All eight records in `benchmark_reviews.jsonl` are **PENDING**:
`author_role="evaluation"`, `reviewer_role="llm"`, null `review_notes`, and null
`check_removal_test`. Author checks do not constitute Alvin's approval.

The six new records extend the existing B3-01 and C1-01 seeds. The original seed
JSON files and SQL seed file are unchanged. All task records use `split="dev"`.
References, samples, derivations and reviews are offline evaluation data. Only
clean schema and original SQL may enter a future model prompt.

## Author-derived sample expectations and size counts

Expected row IDs were derived from the stated intervals and guards before running
DuckDB. Each query is independently checked against the recorded expectations.

| Task | Input rows | Expected row IDs from both queries | WHERE nodes: original to reference | Boundary intent |
| --- | ---: | --- | --- | --- |
| B3-01 | 4 | 2, 4 | 7 to 3 | Keep both parcels at 15; exclude 14 and 16 |
| B3-02 | 3 | 1, 3 | 8 to 3 | NOT of the singleton -2; test -3, -2 and -1 |
| B3-03 | 8 | 2, 5, 6, 7 | 15 to 11 | Preserve both the singleton -4 and the separate closed range [2,5] |
| B3-04 | 6 | 3, 4 | 11 to 7 | At x=-3, y=9 and y=0 fail; y=10 and y=11 pass |
| C1-01 | 10 | 2, 3, 4, 5, 6, 7, 10 | 19 to 11 | Merge the overlap and retain the separate branch at 40 |
| C1-02 | 10 | 2, 3, 4, 5, 6, 7, 8, 9 | 23 to 7 | The third linked interval extends coverage beyond 7 to 10 |
| C1-03 | 10 | 2, 3, 4, 5, 7, 8, 9 | 23 to 15 | Retain [-4,3] and [5,7]; exclude exactly 4 between them |
| C1-04 | 11 | 2, 3, 5, 6, 9, 10 | 23 to 15 | At reading=14, enabled=1 passes and enabled=0 fails; tail at 40 stays independent |

These are **author calculations under README section 4.2**, not production
verifier results. A comparison has three nodes, a closed interval has seven,
two such intervals joined by OR have fifteen, and three have twenty-three.
NOT adds one node. Each additional comparison joined by AND/OR adds four.
Signed literals count once; parentheses and SELECT wrappers count zero.
Every reference is strictly smaller under this rule. Formal production-verifier
confirmation remains pending until Experiment A has passed.

The B3-02 record matches the complete README draft, including its provenance.
Other variants are team derivations from the corresponding B3/C1 seed, not new
external benchmark imports. Their structural changes exercise negation, live
branches, independent columns, a three-interval chain, a one-integer gap, and a
guarded merge rather than merely renaming columns or changing constants.

## Deliberately wrong references in temporary copies

These mutations are defined in `tests/test_development_families.py`. They run on
temporary JSON copies; the original query and author expectations stay fixed.
Each original query must pass and each mutated reference must report the listed
multiset difference without an execution error. Every listed row occurs once.

| Task | Wrong WHERE condition | Expected detected difference |
| --- | --- | --- |
| B3-02 | `TRUE` | Unexpected [2] |
| B3-03 | `x >= 2 AND x <= 5` | Missing [2] |
| B3-03 | `x = -4` | Missing [5], [6], [7] |
| B3-04 | `x = -3` | Unexpected [2], [6] |
| C1-02 | `x >= 0 AND x <= 7` | Missing [8], [9] |
| C1-03 | `x >= -4 AND x <= 7` | Unexpected [6], the single-integer gap |
| C1-03 | `x >= -4 AND x <= 3` | Missing [7], [8], [9] |
| C1-04 | `(reading >= 10 AND reading <= 20) OR reading >= 40` | Unexpected [4], [11], both with a false guard |

## Reproduction from the repository root

Reuse the first-stage `.venv` (tested with Python 3.13.7 and DuckDB 2.0.1).
If recreating it, follow `evaluation/README.md` and `evaluation/requirements.txt`.
Run these PowerShell commands:

```powershell
.\.venv\Scripts\python.exe -m evaluation check-samples
.\.venv\Scripts\python.exe -m evaluation check-samples --tasks evaluation/B3-01.json evaluation/B3-02.json evaluation/B3-03.json evaluation/B3-04.json evaluation/C1-01.json evaluation/C1-02.json evaluation/C1-03.json evaluation/C1-04.json
.\.venv\Scripts\python.exe -m unittest evaluation.tests.test_development_families -v
.\.venv\Scripts\python.exe -m unittest discover -s evaluation/tests -v
```

The first command checks the unchanged seeds: two tasks, six checks. The second
explicitly lists all eight tasks: sixteen query checks, with no `--seed-file`.
Variants have their own samples and are not compared with seed INSERT rows.
The development tests check metadata, expected IDs, the exact B3-02 draft, review
assignments, and eight wrong-reference mutations across the six new tasks.
The full suite also reruns the first-stage checker regressions.

Author acceptance run with the versions above: seed checks **6/6 passed**;
eight-task query checks **16/16 passed**; wrong-reference mutations **8/8
detected across all six new tasks**; full regression suite **27/27 passed**.
Each mutation was also run through the CLI on a temporary copy: the original
passed, the reference failed with the listed row counts, and the CLI returned 1
as expected. There were no unexpected failures. All eight node calculations
were cross-checked with lexical counts under the stated signed-literal rule.

## Remaining acceptance gates

Finite sample agreement and author reasoning do not prove the production
translator correct or certify the full 100-task benchmark. Alvin must supply
independent explanations and record his actual review. After Experiment A passes,
the production verifier must confirm equivalence and strict node reduction for
all eight references. No model calls or teammate implementations are included
in this packet.
