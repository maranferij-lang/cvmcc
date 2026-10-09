# GetCVmax

An AI assistant that improves the English-language CVs of students and young professionals for a specific role. Free, with an English interface.

## What it does

The site has a home page that describes the product, tools in the top menu, Google sign-in
with a short onboarding, a personal account page, and the pages "About", "Privacy" and "Terms".

- **CV review.** A PDF or DOCX is analyzed for a specific role, company type and job posting: a score from 0 to 100, scores on
  7 criteria, "before / after" edits with a warning about invented facts, a "what to learn" plan,
  a questionnaire of up to 8 questions about experience, and a ready CV text in DOCX.
- **Where to apply.** 4-5 directions where this CV has the best chances, with an explanation of what is missing,
  first steps and job titles to search for. A button opens the review already set for the chosen direction.
- **Live jobs.** Under the directions in "Where to apply" and in the CV review: jobs with direct links
  from DOU, Djinni, Jooble and other sources, with a note on what is missing. LinkedIn, Indeed, Glassdoor, Work.ua
  and Robota.ua give search links only. Details are in the section "Learning and job links".
- **CV builder.** A first CV from scratch: a form, a free-form story, up to 8 questions, and an English CV in DOCX.
- **My account.** Profile, saved results, deletion of all your data, sign out.
- **Daily limits.** Per user and for the whole site, so the free model quota is not used up.

## How to run locally

Python 3.10+ is required.

```bash
cd cvmax
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# put your key into .streamlit/secrets.toml
streamlit run app.py
```

Without a key the app opens in demo mode with prepared answers.
This lets you look at the interface without spending credits.

## Which model and which limits

The prototype runs on **Gemini** with a free key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey).
Checked with a real key on 30 September 2026:

| What | Value |
|---|---|
| Limit of full Flash models | 20 requests per day per model, per Google project |
| Lite models | a noticeably higher daily limit |
| Reset | daily at midnight Pacific time (10:00 in Kyiv) |
| Pro models | not available on the free tier |

So GetCVmax tries the models one by one, and their limits add up. Heavy tasks (review, "Where to apply",
CV building) go to the full Flash models first, light ones (Grill me questions) to the Lite models. The site skips a model
that is used up for today until the reset. The model order is set in `cvmax/config.py`.

The daily site limits are in the same place, in `LIMITS`:

| Action | Per user | For the whole site |
|---|---|---|
| CV review | 5 | 60 |
| Where to apply | 3 | 40 |
| Questionnaire | 3 | 30 |
| CV building | 2 | 20 |

To remove these limits, it is enough to enable billing in Google Cloud for the key's project: paid limits
are much higher, and one review costs a fraction of a cent.

On the free tier, Google may use the submitted data to improve its products.
This is stated in the consent text and on the "Privacy" page.

**Switching to Claude** needs no code changes: add `ANTHROPIC_API_KEY` and set `CVMAX_PROVIDER = "claude"`.

## Database and sign-in

- **Supabase** stores users, profiles, results and limit counters. The schema is in `supabase/schema.sql`.
  The site has no direct access to the tables: only to a few functions that check a secret token.
  Without a configured database the site also works, but limits are counted in memory and results are not saved.
- **Google sign-in** is enabled by the `[auth]` section in Secrets (see `.streamlit/secrets.toml.example`).
  Without it the site works without sign-in.

## How to deploy for students

The simplest way is [Streamlit Community Cloud](https://streamlit.io/cloud), which is free.

1. A GitHub repository that contains `app.py`.
2. On streamlit.io: New app, choose the repository and the file `app.py`.
3. In Advanced settings, Secrets, paste the settings from `.streamlit/secrets.toml.example`.
4. Get the link and send it to students.

On the free Gemini tier no money is charged, but there is a limit of requests per minute and per day.
For Claude, set a monthly spending limit on platform.claude.com.

## Configuration

Everything is set through environment variables or Streamlit secrets.

| Variable | Default | What it does |
|---|---|---|
| `CVMAX_PROVIDER` | `auto` | `gemini`, `claude` or `auto`: Gemini if its key is present |
| `CVMAX_GEMINI_MODELS` | see above | Gemini models, comma-separated, in the order to try |
| `CVMAX_MODEL` | `claude-opus-5-5` | Claude model. `claude-sonnet-5-5` is twice as cheap |
| `CVMAX_EFFORT_ANALYSIS` | `high` | Review depth: low, medium, high |
| `CVMAX_EFFORT_GRILL` | `low` | Depth for the Grill me questions |
| `CVMAX_FALLBACKS` | `1` | Claude: retry on a fallback model if the main one fails |
| `CVMAX_DEMO` | off | `1` turns on demo mode even with a key |
| `CVMAX_CONTACT` | none | Email or Telegram for contact on the "About", "Privacy" and "Terms" pages |
| `JOOBLE_API_KEY` | none | Free Jooble key. Without it the Jooble source is skipped |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | none | Free Adzuna keys, both are needed. Without them Adzuna is skipped |
| `LEARN_AUTOMERGE` | none | GitHub repository variable (not a secret). `1` turns on auto-merge of PRs with lessons and market data. Recommended off, see docs/learning/README.md |
| `LEARN_PR_TOKEN` | none | GitHub Actions secret: a fine-grained PAT (contents and pull-requests write) for PRs that trigger `tests.yml` |

## How it is built

The model is not trained. All quality comes from the context it gets in every request:
the CV, the user profile, the goal, the job posting text and the rubric.

```
app.py                 navigation between pages, onboarding after the first sign-in
views/home.py          home: product description
views/analyze.py       CV review, edits, Grill me, ready CV
views/career.py        where to apply
views/builder.py       CV builder
views/onboarding.py    introduction and profile after the first sign-in
views/account.py       my account
views/login.py         Google sign-in
views/about.py, privacy.py, terms.py   about, privacy, terms
ui/common.py           shared: model, consent, CV upload, styles
ui/account.py          sign-in, profile, limits, saving results
cvmax/builder.py       builder logic
cvmax/cv_render.py     CV layout in DOCX
cvmax/db.py            database (Supabase or in memory)
supabase/              database schema
docs/sources.md        sources the rubrics are built on
cvmax/career.py        "Where to apply" logic
cvmax/profile.py       onboarding data, goal clarity
cvmax/cv_input.py      reading PDF and DOCX
cvmax/prompts.py       system prompts
cvmax/rubrics/*.md     criteria: general + one per field of study
cvmax/analyze.py       full review
cvmax/grill.py         Grill me mode
cvmax/edits.py         applying edits and export
cvmax/schemas.py       format of model responses
cvmax/llm.py           model call: Gemini or Claude
cvmax/demo.py          fake client for demo and tests
```

Model responses arrive as structured JSON following the schema in `schemas.py`, so the interface does not break
on an unexpected format. The PDF is sent to the model as a document, so it can also see the layout.

**Protection against invention.** The model has a rule not to add facts the user did not state. Gemini still
sometimes adds tools or links. So every edit is also checked automatically: tool names,
numbers and links that appear neither in the CV nor in the user's answers are highlighted with a warning.

**Rubrics** are built on advice from the career centers of Harvard, MIT, Columbia, Oxford and LSE, recruiters at
McKinsey, BCG, Bain and Google, and law schools. The list of sources is in `docs/sources.md`.

**The easiest way to improve quality is through the rubrics.** They are plain text files in `cvmax/rubrics/`.
Add what recruiters in your field really look for, and the advice becomes more precise without any code.

## Learning and job links

The model is not trained, but the site learns from student feedback through four loops, each with its own horizon:

- **A. Bandit over prompt variants** (minutes). Thompson sampling chooses the review style, the reward
  is a like or accepted edits. A weak variant switches itself off.
- **B. Weekly reflection** (weeks). A script reads accepted and rejected edits and writes lessons for the rubrics.
- **C. Market** (days). A snapshot of job postings becomes the skills currently requested in each direction.
- **D. Outcome** (months). The answer to "did you get an interview" (the `outcome` event) is counted in the variant's successes.

Knowledge lives in data, not in code:

- `cvmax/variants/analysis.json`: prompt variants;
- `cvmax/rubrics/learned/<field>.md`: lessons from feedback;
- `cvmax/rubrics/market/<field>.md`: skills from job postings.

Events in Supabase (`cvmax_events`) contain only identifiers, scores and decisions, without CV text.

**Automation (GitHub Actions):**

- `learn.yml`, "Weekly lessons": every Monday at 06:00 UTC runs `scripts/learn.py`, then evals.
  If evals fail, no PR is created. Otherwise a PR with lessons and variants is opened.
- `market.yml`, "Job market": every day at 03:00 UTC takes the job snapshot (`snapshot_jobs.py`), every Sunday at 04:30
  extracts skills (`learn_market.py`) and opens a PR.
- Before the first run, record the evals baseline: `python evals/run_evals.py --runs 2 --update-baseline` (a real model key is needed),
  and commit `evals/baseline.json`. While `total` = 0, both workflows exit with code 3 and do not open PRs.
- The repository variable `LEARN_AUTOMERGE=1` turns on auto-merge of these PRs. Without it you merge yourself.
  Required repository settings: Settings → Actions → General → "Allow GitHub Actions to create and approve
  pull requests" and Settings → General → "Allow auto-merge". A PR from `GITHUB_TOKEN` does not trigger `tests.yml`,
  so `pytest -q` runs inside the workflow itself before the PR is created. To make PRs trigger `tests.yml`, add the secret
  `LEARN_PR_TOKEN` (a fine-grained PAT for this repository with Contents and Pull requests: write); without it
  `GITHUB_TOKEN` is used.
- Each workflow has two jobs: the first, with read-only permission, runs the scripts, evals and tests and uploads the changes
  as an artifact, and the second (with write permission) only opens the PR. Auto-merge is risky for lessons: more detail in
  [docs/learning/README.md](docs/learning/README.md). Recommendation: keep `LEARN_AUTOMERGE` off until the paywall.
- Secrets for Actions: `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_LEARN_TOKEN` (a separate token with the `learning` scope, see supabase/README.md; not `CVMAX_DB_TOKEN`), `GEMINI_API_KEY`, and for the job
  snapshot also `JOOBLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`.

**Job sources** (`cvmax/jobs/providers/`):

- Without keys: DOU and Djinni (RSS, Ukraine and remote), Arbeitnow (EU and remote), Remotive and Jobicy (remote),
  Greenhouse and Lever (by company name).
- With a key: Jooble (`JOOBLE_API_KEY`, all regions) and Adzuna (`ADZUNA_APP_ID` and `ADZUNA_APP_KEY`,
  United Kingdom, US, EU; no Ukraine).
- Search links only: LinkedIn, Indeed, Glassdoor (UK, US, EU, remote), and also Work.ua and Robota.ua.
  The site does not load their pages.

Every job address passes the domain allowlist of its source, and only verified links become clickable.

**New scripts.** Run them from the repository root. With `--dry-run` the script only shows the result:

```bash
python scripts/learn.py --days 30 --dry-run               # lessons from feedback, report
python scripts/learn_market.py --days 30 --dry-run        # skills from job postings
python scripts/snapshot_jobs.py --dry-run --programs law  # job snapshot, count only
```

`learn.py` and `learn_market.py` need `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` and
`GEMINI_API_KEY`. `snapshot_jobs.py` needs the first three, and the source keys are optional.
In `--dry-run` the snapshot does not write to the database, but it makes live requests to the sources.

More details: [docs/learning/README.md](docs/learning/README.md) (loop B) and [docs/plan-learning-jobs.md](docs/plan-learning-jobs.md).

## Privacy

The CV is sent to the model API only for analysis. GetCVmax stores nothing: data lives in the browser session
and disappears when the tab is closed. Before uploading, the user gives consent to processing.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## What next

- Check the advice on real student CVs and tune the rubrics.
- Turn on Gemini billing when there are more users than the free limits can handle.
- A good PDF export and several layout templates.
