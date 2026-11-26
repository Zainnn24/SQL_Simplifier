# Evaluation-authored copy-only scoring fixture

Evaluation authored every record in `logs/trace.jsonl` solely to test the scorer.
This fixture was not delivered by Alvin and was not produced by an executed LLM
pipeline, a model API, or the absent production verifier. It is not measured model
data and must stay separate from the README E2 batch and future live experiments.

The manifest packages the existing E2 SQL seed solely for offline scoring. It
uses the same schema and original query as the README scoring example, and adds
no task to Martin's 32-task collection. The independent expected sample IDs are
3, 4 and 5. `config_id="evaluation-copy-scripted-v1"` and
`run_id="evaluation-copy-r1"` keep this authored batch distinct.

The four scripted responses copy the original SQL exactly. Each authored
CheckResult is EQUIVALENT with 7 original and 7 candidate nodes, so no attempt
qualifies as a simplification. The terminal reason is BUDGET_EXHAUSTED and its
selected SQL is the original. Feedback strings are authored illustrative request
content, not captured messages sent to a model.

All token counts are explicit scripted zeros. The four 10 ms attempt durations
and the 50 ms total duration are invented fixture values, including an illustrative
10 ms of preflight/overhead. They are not performance or cost measurements. Local
versions and the round's starting source revision are recorded in the configuration.

Expected scores: first equivalence 1/1, first simplification 0/1, final verified
simplification 0/1, recovery 0/0 with rate N/A, four calls, zero accepted runs,
and zero removed nodes. Returning the original after exhaustion earns no final
simplification credit.
