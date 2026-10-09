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
5. The "Weekly lessons" workflow runs evals. If quality drops below the baseline, no PR is created.
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
