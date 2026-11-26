# Offline Experiment B log scorer

`python -m evaluation score` recomputes results from saved task, configuration
and JSONL log files. It does not call a model, run a pipeline, execute SQL or
reverify candidates. Experiment A's production-verifier validation is a separate
prerequisite for publishing model results.

This round starts at `ed313c42fc2de305c5ef52c785a7666dead0ca6b`. Its A changes are
separately committed as `cf5253f65f939d070fd748841ca280752adac18c`; see
`ROUND5_A.md` for the BIGINT probes and known node-count calibration.

## Saved-input commands

From the repository root, reuse the pinned `.venv`:

```powershell
.\.venv\Scripts\python.exe -m evaluation score --manifest evaluation/scoring_examples/README_E2/manifest.jsonl --logs evaluation/scoring_examples/README_E2/logs --config evaluation/scoring_examples/README_E2/config.json --split dev --repeats 1 --output evaluation/scoring_examples/README_E2/result.json --summary evaluation/scoring_examples/README_E2/summary.txt
.\.venv\Scripts\python.exe -m evaluation score --manifest evaluation/scoring_examples/EVALUATION_COPY/manifest.jsonl --logs evaluation/scoring_examples/EVALUATION_COPY/logs --config evaluation/scoring_examples/EVALUATION_COPY/config.json --split dev --repeats 1 --output evaluation/scoring_examples/EVALUATION_COPY/result.json --summary evaluation/scoring_examples/EVALUATION_COPY/summary.txt
.\.venv\Scripts\python.exe -m evaluation score --help
```

`--config` is required and repeatable. Each explicit configuration receives its
own schedule and results. `--split` selects the manifest population; `--repeats`
sets the planned repetition numbers, including repetitions with no received logs.
Defaults are the test split and repetitions 1, 2 and 3. For future frozen model
logs, use the population guard below; this scores files and does not launch 240
model runs:

```powershell
.\.venv\Scripts\python.exe -m evaluation score --manifest benchmark.jsonl --logs results/test --config frozen.json --split test --repeats 1 2 3 --expected-tasks-per-repeat 80 --output results/test_scores.json --summary results/test_scores.txt
```

The schedule is selected manifest tasks x explicit configs x planned repeats.
It is never inferred from the number of received runs. A missing task stays in
the denominator. A wrong manifest population is an integrity error; fix it before
using the results. Repeated observations of the same tasks are not independent
new benchmark coverage.

## Inputs and identity reconciliation

Manifest records follow README 4.4. Configuration records follow RunConfig in
README 4.5. The log directory is read recursively for `*.jsonl`; a single JSONL
file is also accepted. Every identity must agree on run_id, task_id, split,
repeat and config_id. Each scheduled slot has one run, each run has one terminal
record, and dispatched attempts have consecutive unique numbers 1 through 4.

The scorer detects missing slots, conflicting run identities, unknown tasks,
unscheduled repeats, wrong split/configuration, duplicate runs/attempts/terminals,
missing terminals, malformed fields and invalid JSON. Issues retain the source
file and line where available, plus the affected run/task/config/repeat. Duplicate
or invalid manifest/config definitions invalidate their associated runs instead
of silently choosing a definition for credit. Numeric booleans, nonfinite times
and contradictory witness payloads are rejected.

`calls_used` must match dispatched attempt records. A non-READY preflight stops
with its status, zero calls and no attempts. ACCEPTED needs the last checked
EQUIVALENT candidate, a strict node reduction, accepted=true and exactly matching
selected SQL. A byte-identical original copy cannot claim a different node count,
even if its run_end is missing. Checked source counts must agree with READY.
Budget exhaustion requires four calls and the exact original SQL fallback.
Mandatory accept/stop outcomes cannot be followed by another request.

An intact API_ERROR, INCOMPLETE, solver INCONCLUSIVE, REPLAY_ERROR or zero-call
preflight failure is a visible failed run, not missing data. Missing or damaged
records make `integrity.complete=false`; the CLI returns 1 and its summary says
INCOMPLETE. Such summaries are diagnostics, not complete formal experiment
results. Invalid runs never earn final simplification credit. Independently
usable attempt-1 observations can remain visible in an incomplete batch; an
ambiguous task/config/identity or contradictory source-size claim gets no such
credit. A malformed input file does not disappear from the integrity report.

The scorer checks stored witness field consistency without pretending to replay
the row. DIFFERENT needs distinct projected multisets and one selected output
for a true predicate versus none for a false predicate. Actual witness legality
and SQL equivalence remain the production verifier/Experiment A's responsibility.

## Metrics and resources

Repetition, family and category results stay separate by configuration and
`run_kind`. Category is the leading family letter. Scripted data is never pooled
with model measurements.

- First equivalence uses only valid CHECKED attempt_no=1 with EQUIVALENT status.
- First simplification additionally needs fewer predicate nodes. OUTPUT_ERROR
  followed by a correct second response earns zero first-attempt credit.
- Final simplification needs a fully reconciled ACCEPTED run by the fourth call.
  Exhaustion and returning the original earn zero.
- Recovery is later final success among runs whose first attempt was confirmed
  DIFFERENT. The numerator and denominator are stored; zero denominator is null
  in JSON and N/A in text. It describes the whole retry process, not a causal
  effect of feedback.
- Reductions count only accepted runs. Each run retains original and accepted
  sizes, removed nodes and percentage relative to its original. Groups report
  accepted count, total/mean removed nodes and mean per-run percentage. No
  accepted runs gives zero total removal and N/A means.

`outcomes.attempts` uses verifier statuses for CHECKED records and pipeline
outcomes otherwise. `outcomes.runs` separately counts terminal reasons. This
keeps OUTPUT_ERROR/API_ERROR/INCOMPLETE distinct from verifier decisions and
from run termination.

Resources include failed runs. Calls count observed dispatched attempt records;
an end-to-end run duration is counted once, without adding attempt durations a
second time. The total must not be below the sequential attempt sum, allowing
small floating-point roundoff. Missing totals increase `elapsed_unknown_runs`.

Token sums are explicitly named `input_tokens_known`/`output_tokens_known`.
Null is not zero: unknown-attempt counts and token/call accounting flags remain
visible. Terminal totals must equal the sum of known usage, or null when none
was available; zero-call runs have actual zero usage. A fully reconciled log may
still have incomplete token accounting. Costs shown for damaged or missing logs
are observed known amounts, not claims of complete resource measurement.

## Deterministic outputs and preserved sources

`--output` writes stable JSON; `--summary` writes concise English text. Reports
include the input plan, manifest/config/log SHA-256 hashes, scheduled results,
run sources and integrity issues. Preserve the input files with the report;
hashes do not replace the saved logs/configurations. Neither output paths nor
current time/Git state enter the result, so changing only the output destination
does not change its bytes. Input overwrite is rejected, as is creating a JSONL
output inside the input log tree that would contaminate the next read.

Both supplied batches were scored twice to different destinations. Each batch's
JSON and English summary were byte-identical across the two invocations; all
eight original manifest/config/log/source files retained their hashes.

## Actual scripted acceptance results

These are two independent single-task, single-repeat scoring batches, not two
competing runs in the same scheduled cell.

| Batch | First equivalence | First simplification | Final simplification | Recovery | Calls | Accepted removal | Total elapsed | Input/output tokens |
| --- | --- | --- | --- | --- | ---: | --- | ---: | --- |
| README_E2 | 0/1 | 0/1 | 1/1 | 1/1 | 2 | 4 of 7 nodes, 57.14% | 70 ms | 0 / 0, complete |
| EVALUATION_COPY | 1/1 | 0/1 | 0/1 | 0/0, N/A | 4 | 0 total; no accepted candidate | 50 ms | 0 / 0, complete |

The first `logs/trace.jsonl` is an exact copy of README 4.5's three scripted
lines. The second is evaluation-authored: four exact original-query copies,
7-to-7 sizes, budget exhaustion and original SQL fallback. Its four 10 ms
attempts and 50 ms total are invented fixture values. The README times are also
illustrative contract values. These elapsed/token values are not measured model
performance or cost. Both batches have their own config, input log directory,
`SOURCE.md`, `result.json` and `summary.txt`.

The configs record actual installed dependency versions and the round's starting
revision to identify this fixture packaging. They do not claim that a live model
adapter or prompt executed. The E2 manifests are scoring fixtures outside Martin's
32-task contribution; they do not create additional benchmark ownership.

## Regression coverage and remaining handoffs

The complete evaluation regression suite passed **161/161** tests: the 124-test
A checkpoint plus 19 primary scorer tests and 18 additional integrity tests.
Coverage includes missing-task denominators, missing/duplicate terminals,
duplicate attempts and runs, identity/config conflicts, malformed JSON, attempt-1
OUTPUT_ERROR, zero-call preflight versus absent logs, terminal failures, unknown
tokens, impossible acceptance, source-size conflicts, stable scoring, read-only
inputs and internal witness contradictions. An 80-task, three-repeat schedule is
tested with temporary synthetic records; it is not a model experiment.

The unchanged personal packet still passes 24 seed checks, 64 query checks,
26 negative mutations across 18 variants and 32 author size reductions. The
original sample command passes 6/6. Experiment A remains 30 passed fixtures,
10 independently replayed witnesses and 182 finite assignments, plus eight
separate DuckDB type-bound expectations. Its real production command still
returns 2 and reports NOT_RUN/N/A.

Fetched dependencies at this checkpoint:

| Branch | SHA | Available delivery |
| --- | --- | --- |
| origin/David | c2030c1784ec0517add6fe0536ac8d0d40fec06f | Plan documents, no verifier exports |
| origin/main | c2030c1784ec0517add6fe0536ac8d0d40fec06f | Plan documents |
| origin/Alvin | a9ff6de685e84098f9669bf26330dd02c331ab42 | Empty pipeline skeleton, no generated scripted logs |

There is no Alvin-log integration result yet. Neither example is attributed to
him. David still needs to deliver the real verifier/encoder and evidence, and
Alvin the runnable pipeline, its generated scripted E2/copy traces and actual
task reviews. All 32 existing reviews remain unchanged and PENDING. Formal
Experiment A, the frozen reviewed team benchmark and genuine model logs are
still required before publishing Experiment B model statistics. No model call
or official 240-run experiment was made in this round.
