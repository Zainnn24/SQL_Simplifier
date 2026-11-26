# D1 benchmark receipt and formal acceptance

The shared entry point accepts contributions from all three owners:

```text
python -m evaluation validate-benchmark --manifest benchmark.jsonl
```

The default mode is **draft**. The default review file is
`benchmark_reviews.jsonl` beside the supplied manifest; `--reviews` overrides it.
The original repository SQL seed file is checked by default. `--timeout-ms`
defaults to 5000 for production calls. Both JSONL files use the shared README 4.4
record formats; task references, samples, reasoning and reviews stay offline.

Exit 0 means a valid draft receipt, or FORMAL_PASSED only when explicit formal
mode satisfies every gate. Exit 1 means malformed records or failed data checks.
Exit 2 means FORMAL_BLOCKED: valid received data with outstanding formal
requirements. A draft success never claims that 100 tasks or equivalence have
been accepted. The console and JSON report distinguish these states.

## Reproduce the actual partial receipt

From the repository root using the existing isolated environment:

```powershell
.\.venv\Scripts\python.exe -m evaluation validate-benchmark --manifest evaluation/manifests/martin_draft.jsonl --reviews evaluation/benchmark_reviews.jsonl --mode draft --output evaluation/results/benchmark_draft.json
.\.venv\Scripts\python.exe -m evaluation validate-benchmark --manifest evaluation/manifests/martin_draft.jsonl --reviews evaluation/benchmark_reviews.jsonl --mode formal --experiment-a-report evaluation/results/experiment_a_production.json --output evaluation/results/benchmark_formal.json
```

The second command actually returns **2**, not a formal success. The manifest
packages the existing 32 task JSON records exactly, in sorted task_id order.
The receipt covers Martin's contribution to the 100-task team benchmark.

| Actual receipt | Result |
| --- | --- |
| Unique tasks | 32 of 100 |
| Complete received families | B3, B4, B5, C1, C2, C3, C4, C5; four tasks each |
| Split | dev 8, test 24 |
| Missing contributions | LLM 36 tasks; verifier 32 tasks |
| Review records | 8 APPROVED (B3 and C1), 24 PENDING |
| Query checks against expected multisets | 64/64 |
| Seed input checks | 8/8 |
| Seed DDL/original/reference fidelity triples | 8/8 |
| Structural duplicate groups | 0 |
| Draft | DRAFT, exit 0 |
| Formal | FORMAL_BLOCKED, exit 2 |

Missing families are A1-A5, B1-B2, D1-D5 and E1-E5. The formal report lists all
68 absent IDs, the 24 pending reviews, the four missing production interfaces
and the missing successful production Experiment A evidence separately.

## Validation gates

Task IDs are family-01 through family-04 across A1-E5. IDs, families, required
fields, supported SQL, integer samples and projections must agree. Every task
uses a fresh DuckDB sample database. Original and reference each compare with
the author's expected unordered multiset, retaining duplicate counts and
projection order. Variant 01 additionally retains the seed DDL, both queries
and the full multiset of input rows.

The checker reuses the existing sample checker, seed reader and author
structural screening. CHECK-bearing predicates additionally retain joint schema
constraint context when screening renaming, commuted comparisons/Boolean terms
and literal-rank/adjacency relationships. Duplicate diagnostics name all flagged
IDs and the heuristic, for independent review. This screen is not an equivalence
proof or a substitute for peer assessment of meaningful family variation.
More than six used columns produce an explicit incomplete-screen warning in
draft mode and a formal blocker; they never silently pass a name-sensitive
fallback. None of the current 32 tasks reaches that resource limit.

Formal acceptance requires exactly 25 families, four tasks each, and the entire
A2/B3/C1/D1/E2 families in dev (20 tasks), with the remaining 80 in test.
Ownership and review assignments follow the team agreement:

| Author role | Families | Required reviewer role |
| --- | --- | --- |
| llm | A1-A5, B1-B2, D1-D2 | verifier |
| evaluation | B3-B5, C1-C5 | llm |
| verifier | D3-D5, E1-E5 | evaluation |

Reviews are joined by unique task_id. PENDING may have null or textual feedback;
APPROVED requires nonempty notes from the assigned different role. Formal mode
requires one approved review per task and rejects unmatched or duplicated
review records. A validator checks the recorded decision's format and assignment;
the team remains responsible for the authenticity of the human review.

## E-family CHECK dependence

CHECK-removal evidence must name exactly one existing CHECK occurrence. Named
constraints disambiguate repeated expressions. The supplied reduced schema must
equal the original DDL with only that CHECK removed: all other constraints,
columns and types remain. DuckDB must reject the witness under the original
schema's CHECK, accept it under the reduced schema, and observe the declared
different truth values and projected multisets directly.

Temporary tests replay the existing E2-01 seed with row_id=1, start_day=10,
end_day=9: after removing the ordering CHECK, the original returns [] and the
reference returns [[1]]. This seed is only a control, not Martin's 33rd task.
Another temporary E2 control keeps CHECK(row_id >= 0) and a two-column projection.
A witness with row_id=-1 is rejected because it violates that remaining CHECK;
silently dropping the second CHECK or swapping projected columns is also caught.

## Production and Experiment A evidence

Formal mode requires the actual `verifier` exports `validate_input`, `verify`,
`encode_query` and `InputError`, plus a successful current production Experiment A
report. An offline report, NOT_RUN report, test-double report or old report
without provenance does not qualify. Evidence is checked against current fixture
bytes, dependency versions, real source fingerprints, coverage and discrepancy
records. A self-reported passed flag or summary alone is insufficient.

`check-verifier` now records `verifier_provenance` and `test_standin`. The
fingerprint includes the actual bytes of verifier Python sources and public
export locations. If source or fixture bytes or dependencies change, regenerate
Experiment A evidence in that environment before formal benchmark acceptance.
Injected APIs must be explicitly marked test-only and can exercise branches,
including EQUIVALENT without node shrinkage; they can never yield FORMAL_PASSED.

For each reference, a validated real API must return READY and EQUIVALENT with
positive counts, matching original-node counts and a strictly smaller candidate.
The benchmark report records versions, timeout, production details and exact
input hashes. Reports never invoke the evaluated model.

Generate new production evidence once David delivers the implementation:

```powershell
.\.venv\Scripts\python.exe -m evaluation check-verifier --fixtures evaluation/verifier_cases.jsonl --timeout-ms 5000 --output evaluation/results/experiment_a_production.json
.\.venv\Scripts\python.exe -m evaluation validate-benchmark --manifest benchmark.jsonl --reviews benchmark_reviews.jsonl --mode formal --experiment-a-report evaluation/results/experiment_a_production.json --timeout-ms 5000 --output results/benchmark_acceptance.json
```

The saved production Experiment A report records NOT_RUN (exit 2). Alvin's
scripted pipeline handoff at `88b87d0c0ceb9754ea3bd7f532eff87ce2ab9cb8`
uses the E2-01 verifier stub. Both regenerated traces passed the scorer with
zero issues. His eight independent approvals are in `benchmark_reviews.jsonl`.

## Reproduction and regression

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s evaluation/tests -v
.\.venv\Scripts\python.exe -m evaluation.author_checks
.\.venv\Scripts\python.exe -m evaluation check-samples
.\.venv\Scripts\python.exe -m evaluation check-verifier --fixtures evaluation/verifier_cases.jsonl --fixtures-only --output evaluation/results/experiment_a_offline.json
```

The two scored scripted batches and exact-byte hash checks are documented in
`CLOSEOUT_A.md`. Scoped LF attributes also cover the new draft manifest, real
review file, SQL seed and D1 reports, without changing the seed or review records
in Git. Hashes always describe actual input bytes, not normalized surrogate data.
The review update preserves all 32 task files and the SQL seeds. Both saved
receipt reports hash the updated review file.

The final complete evaluation suite passed **202/202** tests. Author checks still
pass 24/24 seed checks, 64/64 query checks, 26/26 wrong-reference captures across
18 variants and 32/32 size reductions. The original sample command passes 6/6.
Experiment A offline retains 30 fixtures, 10 independent replays, 182 finite
assignments (157 legal rows) and eight separate BIGINT probes.

At the initial receipt, a fresh checkout of the staged Git index passed all
three hash regression tests against 15 saved input hashes. Both scoring commands
were repeated twice there; their JSON and text outputs were byte-identical to
each other and to the saved reports. Fresh draft/formal benchmark commands
returned 0/2 respectively and reproduced their saved JSON reports byte-for-byte.
