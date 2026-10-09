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

## Gate for auto-learning

The learning script (`scripts/learn.py`) merges new lessons only if evals did not get worse.
- `--json PATH` writes the run result: `{"model", "runs", "passed", "total", "pass_rate", "cases": {"<case>/<check>": {"passed", "runs"}}}`.
- `--baseline PATH` compares `pass_rate` with the baseline (`evals/baseline.json`). The exit code is 0 if
  `pass_rate >= baseline - 0.05` (a tolerance for model noise), and 3 if worse. An empty baseline (`total` = 0) does not block.
- `--update-baseline` writes `evals/baseline.json` from this run (the path can be set via `--baseline`).
Set the baseline once with a real key: `python evals/run_evals.py --runs 2 --update-baseline`.
