# Analysis quality checks

This folder holds CVs with known problems and what the analysis must notice in them.

- `cases/` are made-up examples, they are in the repository.
- `private/` are real CVs. This folder does not go into git: the CVs contain personal data.

How to work with a miss that a user found:
1. Add the CV to `private/` and a JSON case next to it (the format is as in `cases/example_student.json`):
   `line_contains` is a piece of the line, `verdicts` are the verdicts considered correct.
2. Run `python evals/run_evals.py` and make sure the new case fails.
3. Change the rubric (`cvmax/rubrics/`) or the prompt (`cvmax/prompts.py`).
4. Run again: the new case must pass and the old ones must not fail.

The model does not always answer the same way, so before deciding that a change worked, run it several times:
`python evals/run_evals.py --runs 3`. At the end there is a table of how many times each check passed
(`!!` marks unstable ones). Each run uses one model request per case, and free Gemini
gives about 20 requests per day per model, so 3 runs over 4 cases is already 12 requests.

Compare models: the same set on Claude.
```
GEMINI_API_KEY=... python evals/run_evals.py --runs 3
CVMAX_PROVIDER=claude ANTHROPIC_API_KEY=... python evals/run_evals.py --runs 3
```
Look at the "Разом" (Total) line and at the unstable checks. If Claude is consistently better on the same cases,
it makes sense to switch the main model (`CVMAX_PROVIDER` in Secrets).

Check fields (`expect`):
- `line_contains`: pieces of text of the line the check is about.
- `verdicts`: which verdicts are considered correct (`keep`, `cut`, `shorten`, `rewrite`, `move`).
  `keep` means "do not touch": the check fails if the line got another verdict or an edit.
  `any` means the verdict does not matter, and only the forbidden words below are checked.
- `after_must_not_contain`: words that must not appear in the edit of this line. This catches cases
  where the model attributes to the person an activity that did not happen (e.g. "analysed industries" instead of "invited speakers").
- `type`: the kind of check. Without it this is a line check (the fields above). There are two more kinds, for flaws over the whole CV:
  - `"length"` with `min_cut_words`: the edits together shorten the CV by at least that many words (only edits
    where `after` is shorter than `before` count). This catches `over_length`.
  - `"mentions"` with `any_of`: at least one of the words is in `edits[].after`, `edits[].section`, `missing_info` or `summary`.
    This catches a missing contact (e.g. `email`).

Every case automatically gets one more check, `_no_invented_numbers` (flaw `hallucination`): no edit
introduces a number that is not in the CV, in the profile `background` or in the `before` quote. Numbers in [brackets] are allowed.
You do not need to add it to `expect`.

## Synthetic cases

Besides real CVs there are made-up ones in which the flaws are planted on purpose. They are in `evals/synth/` (pairs
`<program>_s<seed>_<n>.json` + `.txt`, the format is as in `cases/`, plus `"synthetic": true` and a `flaw` field in every `expect`).
The default run does not include them. The catalog of flaws, that is how to plant each one and which verdicts are correct, is `evals/flaws.json`.
- Run: `python evals/run_evals.py evals/synth --runs 1`. At the end there is a "By flaw" table:
  how many times each flaw passed, the worst on top. In `--json` it is in `by_flaw`:
  `{"<flaw>": {"passed", "total"}}`. `strong_keep` is the control for false alarms: lines that must not be touched.
- New cases are written by a heavy model: `python evals/generate_synthetic.py --program all --n 2 --seed 7` (a key is needed).
  Every case goes through `validate_case`: line fragments occur in the text exactly once, 300-1200 words, no real companies,
  LinkedIn slugs end with `-example` and email domains contain `example`. Existing files are not overwritten without `--force`.
- The baseline for the gate is separate: `evals/synth_baseline.json`.

Raw model vs the full path: by default a case goes the same way as in the app (`analyze_full`: deterministic
checks, edit verifier, stable score). With `--raw` only `analyze_cv` runs, without checks and the verifier,
that is, what the model says by itself. Run both and compare the "By flaw" table: the difference shows what these layers add.

## Gate for auto-learning

The learning script (`scripts/learn.py`) merges new lessons only if evals did not get worse.
- `--json PATH` writes the run result: `{"model", "runs", "passed", "total", "pass_rate", "cases": {"<case>/<check>": {"passed", "runs"}}}`.
- `--baseline PATH` compares `pass_rate` with the baseline (`evals/baseline.json`). The exit code is 0 if
  `pass_rate >= baseline - margin`, where the margin is at least 0.05 and grows with a small sample (a tolerance for
  model noise), and 3 if worse. An empty baseline (`total` = 0) also closes the gate: record one first.
- `--update-baseline` writes `evals/baseline.json` from this run (the path can be set via `--baseline`).
Set the baseline once with a real key: `python evals/run_evals.py --runs 2 --update-baseline`.
For the synthetic cases the same with their own baseline:
`python evals/run_evals.py evals/synth --runs 1 --baseline evals/synth_baseline.json --update-baseline`.
