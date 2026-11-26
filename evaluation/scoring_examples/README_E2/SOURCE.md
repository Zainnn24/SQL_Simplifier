# README E2 scripted scoring fixture

The three lines in `logs/trace.jsonl` are copied verbatim from the team's root
`README.md`, section 4.5, "E2 scripted JSONL trace". They are illustrative team
contract data, not an Alvin pipeline delivery, an executed model call, or a
measured model result. Raw responses, feedback, zero token counts and the stated
50/10/70 ms durations retain the README's illustrative values exactly.

`manifest.jsonl` packages the five input rows, original query and reference of
the existing SQL E2 seed. Its independently expected IDs are 3, 4 and 5. This
single-task scoring manifest is separate from Martin's 32-task collection and
must not be counted as a new benchmark contribution or held-out model run.

`config.json` follows the README RunConfig example with the same
`config_id="e2-scripted-v1"`. Its placeholders are replaced with the actual local
Python/dependency versions and the source revision at the start of this round.
It identifies an offline scripted fixture, not a frozen live experiment or proof
that a model adapter or prompt was executed.

Expected scores: first equivalence 0/1, first simplification 0/1, final verified
simplification 1/1, recovery 1/1, two dispatched calls, and four removed nodes.
Keep these results separate from measured model data and Experiment A cases.
