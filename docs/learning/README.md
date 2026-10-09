# Weekly reflection (loop B)

Once a week, GitHub Actions reads anonymous student signals and turns them into short lessons for the rubrics.

## How it works

1. `scripts/learn.py` takes `learning_export` from Supabase for the last 30 days: decisions on edits, likes, events.
2. For each field where there are at least 20 signals, the model reads the accepted and rejected edits and writes
   a summary and up to 8 anti-patterns.
3. The result goes to `cvmax/rubrics/learned/<field>.md`. These files are automatically added to the rubric
   in the section "Learned from student feedback" (up to 3000 characters).
4. If the data clearly calls for a different approach, the model proposes a new prompt variant. It is added to
   `cvmax/variants/analysis.json` (if there are fewer than 5 active ones) as an **inactive** `auto-*` variant, and the bandit
   evaluates it only after a human activates it (see below).
5. The "Weekly lessons" workflow runs evals: the regular ones (`evals/cases`) and the synthetic ones (`evals/synth`, see below).
   If quality drops below the baseline, no PR is created.
   An inactive `auto-*` variant that fails evals (`--prune-failing`) is removed from the JSON in the PR, and the job does not fail;
   an active variant that fails makes the job fail.
6. The weekly report is stored in `docs/learning/<YYYY-MM-DD>.md` and goes in the same PR.

## How to read the report

- **Accept rate by section / priority**: the share of accepted edits. A low share in a section means
  the model suggests unnecessary changes there.
- **Thumbs up by variant**: which review style people like more.
- **Thumbs up by knowledge version**: knowledge version = hash of rubrics, lessons, market data and variants. A drop
  in the share of likes after a new version triggers an automatic rollback.
- Do not trust percentages on small numbers: look at the count column.

## Automatic and manual rollback

If the latest knowledge version has a share of likes at least 10 points lower than the previous one (both with 30+
ratings), the script overwrites all lesson files with a one-line comment "reverted".
Manually: delete `cvmax/rubrics/learned/<field>.md` (or restore the previous version from git) and merge the PR.

## Workflow permissions

Each workflow has two jobs. The first (`contents: read`) runs the scripts, evals and `pytest`, then uploads the changed paths
as an artifact. The second (`contents: write`, `pull-requests: write`) only unpacks the artifact, opens the PR and turns on
auto-merge; no repository code is executed there.

## Auto-merge and PRs

Repository settings:

- Settings -> Actions -> General -> "Allow GitHub Actions to create and approve pull requests";
- Settings -> General -> "Allow auto-merge";
- the repository variable `LEARN_AUTOMERGE=1` turns on `gh pr merge --auto --squash` for lesson and market PRs. Without it
  you press merge yourself after review. Auto-merge works only when the branch's required checks pass.

`LEARN_PR_TOKEN`: a fine-grained PAT for this repository with `Contents: write` and `Pull requests: write`.
PRs opened through `GITHUB_TOKEN` do not trigger `tests.yml`, so the required checks do not start and the PR
will not merge. With `LEARN_PR_TOKEN` the checks run. Without it the workflow falls back to `GITHUB_TOKEN`.

### Residual risk of auto-merge

The lesson text goes into every student's prompt, and it is written by a model from data that students control.
There are filters, but they are not perfect. So with `LEARN_AUTOMERGE=1` a prompt injection can reach everyone without
human review. Recommendation: keep auto-merge off until the paywall is launched, and
review the PR manually every week.

### `auto-*` variants

New `auto-*` variants are added as inactive. To enable one, a person sets `"active": true` in
`cvmax/variants/analysis.json` after reviewing the text.

## Required secrets

`SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_LEARN_TOKEN` (a token with the `learning` scope, step 5 in `supabase/README.md`), `GEMINI_API_KEY`, and also the optional `LEARN_PR_TOKEN` (see above). Never put the app token `CVMAX_DB_TOKEN` here: it reads all saved CVs. Keys do not end up in logs or files.

## Locally

`python scripts/learn.py --dry-run` shows the report and writes nothing. The same environment variables are needed here too.

## Synthetic evals

The `evals/synth/` set is made-up CVs with known flaws planted on purpose. Each flaw has a code from the catalog
`evals/flaws.json` (for example `weak_opener`, `no_result`, `personal_data`) and a rule the review must
follow: find the flaw or leave a strong line alone. So for each type of flaw you can see how many times the review
found it and how many times it made something up.

The format is the same as in `evals/cases/`: JSON and TXT. The JSON has `"synthetic": true`, and every check has a
`flaw` field. Every case also gets the automatic check `_no_invented_numbers` (flaw `hallucination`):
an edit must not introduce a number that is not in the CV.

Run: `python evals/run_evals.py evals/synth --runs 1`. At the end a "By flaw" table is printed with the worst
flaws on top, and with `--json` the file gets `by_flaw`. The `--raw` flag runs the raw model without
checks and the verifier, to compare with what the app gives.

### How to generate

```bash
python evals/generate_synthetic.py --program all --n 3 --seed 1 --out evals/synth
```

A model key is needed (`GEMINI_API_KEY` or `ANTHROPIC_API_KEY`). `--program` takes a program key from
`cvmax/profile.py` or `all`. Each case has 4-6 flaws chosen by `--seed` (the same seed gives the same
set), and one case per program also gets `over_length`. The files are named `<program>_s<seed>_<i>.json` and `.txt`.
Existing files are not overwritten without `--force`.

Before it is written, a case goes through `validate_case`, which can be called without a model: each line fragment
(`line_contains`) must occur in the CV exactly once and be 15-120 characters long, the CV must be 300-1200 words, and
real companies are banned. An unfit case is regenerated up to two times, then skipped. The starter set
(2 cases per program, `_s0_1` and `_s0_2`) was written by hand, so the set works without a model key.

### Gate and baseline

- The baseline is `evals/synth_baseline.json` (`pass_rate`, `passed`, `total`). It is separate from
  `evals/baseline.json`, which is calculated from the regular cases.
- The gate in "Weekly lessons" is the step "Synthetic evals gate":
  `python evals/run_evals.py evals/synth --runs 1 --json evals/last_synth.json --baseline evals/synth_baseline.json`.
  It exits with code 3 if `pass_rate` is lower than the baseline by more than the tolerance. The tolerance is the larger
  of two numbers: 0.05 or 2·√(p·(1−p)/n), where p is the baseline share and n is the number of checks in the run. On a small sample the tolerance
  is larger, because the model is noisy.
- An empty baseline (`total` = 0) also closes the gate. Until there is one, the weekly run exits
  with code 3, and no PR is opened.

### Baseline bootstrap

Done once, with a real model key:

- on GitHub: Actions -> "Weekly lessons" -> Run workflow with `bootstrap_baseline` = true. The workflow records
  `evals/baseline.json` and `evals/synth_baseline.json` and opens a PR instead of learning;
- locally: `python evals/run_evals.py evals/synth --runs 1 --baseline evals/synth_baseline.json --update-baseline`,
  then commit `evals/synth_baseline.json`.

If no check could be evaluated, the baseline is not written and the command exits with code 3.

### Regeneration in "Weekly lessons"

A manual run with `regenerate_synth` = true generates `--program all --n 2 --seed <run number>` before the gate:
up to 2 new cases for each of the 7 programs (unfit ones are skipped). The seed is the run number, so file names do not repeat. The new files
go into the PR together with the report (`evals/synth` is in `add-paths`), so you review them the same way as the lessons.
The same `GEMINI_API_KEY` as for the lessons is needed.

The gate compares `pass_rate` over the whole `evals/synth`, so new cases can change it without any code change.
If the gate fails after regeneration, first check whether the failure is in the new cases and not in the code. Only then
overwrite `evals/synth_baseline.json` with the command from the "Baseline bootstrap" section.
