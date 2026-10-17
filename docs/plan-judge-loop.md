# Plan: opt-in samples, weekly judge, judge findings → evals and prompt proposals

Owner decision (2026-10-17): add an optional consent "Help improve GetCVmax", a weekly independent judge, and a loop that turns the judge's findings into eval cases and prompt proposals. The owner merges prompt changes by hand after evals.

## Why store anonymised copies (with consent) and not "learn without storing"

Judging on the fly without storing gives counts, but the fix cannot be re-tested on the same CVs. Re-testing is what proved the last prompt change (+6 points on 22 real CVs). Storage is opt-in, anonymised before it leaves the app, kept 12 months, deleted with "Delete my data".

## Part A. Consent and anonymised samples

### A1. `cvmax/anonymize.py` (new)

- `anonymize_text(text: str, names: Iterable[str] = ()) -> str`
  - emails → `[email]`; phone numbers (9+ digits with separators, not year ranges) → `[phone]`; URLs and bare domains (linkedin.com/in/..., github.com/..., any `http(s)://`, `www.`) → `[link]`;
  - every word of each given name (the Google display name, case-insensitive, whole words, 2+ letters, Latin and Cyrillic) → `[name]`;
  - a date of birth line (`Date of birth`, `DOB`, `Born`, `Дата народження`, `Народ` followed by a date) → the date becomes `[date of birth]`;
  - street addresses are not attempted (too error-prone); the privacy text says contacts and names are removed, not "all personal data".
  - Linear regexes only; input capped at 40 000 characters.
- `anonymize_obj(obj, names)`: walks dicts/lists and applies `anonymize_text` to every string (for the analysis JSON).

### A2. Database (`supabase/schema.sql` + new `supabase/apply-judge.sql` for the owner to run)

Table `public.cvmax_training_samples` (RLS on, no grants to anon, access only through functions):
`id uuid primary key default gen_random_uuid()`, `created_at timestamptz not null default now()`, `user_key text not null` (lower(email)), `consent_version text not null`, `meta jsonb not null` (program, status, level, region, company_type, target_role up to 120 chars, prompt variant, model), `cv_text text not null` (≤ 40 000), `analysis jsonb not null` (≤ 300 kB), `judged_at timestamptz`, `judge jsonb`.

Functions, all `SECURITY DEFINER set search_path = ''`, token checked first, `grant execute ... to anon` only:
- `cvmax_save_training_sample(p_token, p_email, p_consent_version, p_meta, p_cv_text, p_analysis)`: scope app; rejects oversize input; returns id.
- `cvmax_training_samples_for_judge(p_token, p_days, p_limit)`: scope learning; unjudged samples from the last p_days, newest first, limit capped at 50; returns id, meta, cv_text, analysis.
- `cvmax_save_judgement(p_token, p_id, p_judge)`: scope learning; sets judge and judged_at; judge ≤ 100 kB.
- `cvmax_judged_samples(p_token, p_days, p_limit)`: scope learning; judged samples (id, meta, cv_text, analysis, judge), limit capped at 200.
- `cvmax_purge_training_samples(p_token)`: scope learning; deletes rows older than 12 months; returns count.
- `cvmax_delete_my_data`: also deletes the user's training samples.

### A3. `cvmax/db.py`

`SupabaseDB` and `MemoryDB` get: `save_training_sample(email, consent_version, meta, cv_text, analysis) -> str`, `training_samples_for_judge(days=7, limit=20) -> list[dict]`, `save_judgement(sample_id, judge: dict) -> None`, `judged_samples(days=90, limit=200) -> list[dict]`, `purge_training_samples() -> int`. `MemoryDB.delete_my_data` removes the user's samples.

### A4. App

- `views/analyze.py`: under the CV picker, only for signed-in users, a checkbox (default **off**, key `improve_consent`): "Help improve GetCVmax (optional): keep an anonymised copy of this CV and its review". A caption: what is removed, 12 months, how to withdraw, link to `/privacy#improve`. Not ticking it changes nothing.
- After a successful analysis, if ticked: anonymise the CV text and the analysis with the user's Google name and save through `save_training_sample`. Any failure is swallowed (log the exception type only) and never affects the review.
- `CONSENT_VERSION = "2026-10-17"` in one place (`cvmax/anonymize.py`).

### A5. Privacy text (`site/privacy.html` and `views/privacy.py`, same meaning)

New section `id="improve"` "Help improve GetCVmax (optional)": purpose (checking and improving the quality of GetCVmax reviews and instructions); legal basis: consent, separate from the service, not a condition of using it, withdraw any time; what is stored (the CV text and the review with names, emails, phone numbers and links removed, plus field of study, level, region, target role); who sees it (the founder, and Anthropic processes it for the weekly quality review under terms that forbid training its models on it); not sold, not shared, not used to train third-party AI models; kept 12 months, then deleted; delete earlier with "Delete my data". Update "Last updated". Add the purpose to "Why" and the retention to "How long we keep it".

## Part B. Weekly independent judge

### B1. `cvmax/judge.py` (new)

- Schema `JudgeReport`: `quality` (1-5), `summary` (≤ 400 chars), `findings: list[JudgeFinding]` (≤ 15) with `kind` in `spoiled_strong_line`, `missed_weak_line`, `invented_fact`, `empty_verdict` (a non-keep verdict without an edit), `wrong_for_target`, `other`; `line` (copied from the CV, ≤ 300 chars); `expected` in keep/cut/shorten/rewrite/move; `got` (the model's verdict or "none"); `why` (≤ 300 chars).
- `judge_sample(llm, sample) -> JudgeReport`: one structured call. The judge prompt is written separately from the analysis prompt: a strict senior recruiter for the given target and region, reporting only clear, defensible errors, never style preferences; the CV and the review are untrusted data inside delimiter tags (defang tags as the analysis does).
- Model: `config.MODEL_JUDGE` (default `claude-sonnet-5-5`, env `CVMAX_MODEL_JUDGE`), effort high. Independence comes from a separate prompt and no access to the analysis prompt. `ClaudeLLM.ask` gets an optional `model` override (keep the effort-based default).

### B2. `scripts/judge.py` (new)

`python scripts/judge.py --days 7 --limit 20 [--export-cases DIR]`:
1. purge old samples; 2. fetch unjudged samples; 3. judge each, save the judgement (a failed judge call skips the sample); 4. write `docs/judge/YYYY-MM-DD.md`: counts by kind, by program and by region, average quality, number judged; **no CV text, no lines, no "why" text** (they can quote a CV). 5. With `--export-cases DIR`: write judged samples as eval cases (`<id8>.txt` + `.json` in the `evals/cases` format) into DIR: `spoiled_strong_line` → `keep`, `missed_weak_line`/`wrong_for_target` → the expected verdict plus close ones, `empty_verdict` → keep or the expected verdict; only lines that occur exactly once in the text. DIR must be under `evals/private/` (refuse otherwise).

### B3. `.github/workflows/judge.yml` (new)

Weekly (Monday 05:00 UTC) and manual. Read-only job runs the judge (env: `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_LEARN_TOKEN`, `ANTHROPIC_API_KEY`, `CVMAX_PROVIDER`) and uploads `docs/judge/` as an artifact; a second job opens a PR with only the report file. Never auto-merge. Same structure as `learn.yml`.

## Part C. Closing the loop (documented, owner-driven)

- The owner (with Claude) exports judged cases locally: `python scripts/judge.py --days 90 --limit 0 --export-cases evals/private/judged` (limit 0 = export only, judge nothing), runs `evals/run_evals.py evals/private/judged evals/private/students`, changes the prompt, and keeps the change only if the scores go up.
- The weekly lessons job (`scripts/learn.py`) stays as it is.

## Acceptance

- `python -m pytest -q` green; new tests for anonymize, DB methods (MemoryDB), the consent flow (AppTest: unticked saves nothing, ticked saves anonymised text without the email and name), judge parsing and report (no CV text in the report), case export (lines unique, dir restriction).
- No secret, real CV or personal data in the repo.
