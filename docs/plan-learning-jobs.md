# Plan: a self-learning system and direct links to job postings

Plan author: Fable 5.1. Implementation: agents on weaker models for the tasks below, review: strong models.

## 1. What we are building

**Goal 1. Self-learning.** The app already collects signals (which edits were accepted, likes on a review, feedback), but does nothing with them. Now the signals are closed into four loops, each with its own horizon:

| Loop | What it learns | Horizon | Mechanism |
|---|---|---|---|
| A. Bandit over prompt variants | which review style is more useful to people | minutes | Thompson sampling, reward = like or accepted edits |
| B. Weekly reflection | what the model does wrong in each field | weeks | an LLM reads accepted and rejected edits → `rubrics/learned/<field>.md`; merge only if evals did not fail |
| C. Market | which skills are currently requested in job postings | days | daily snapshot of postings → skill extraction → `rubrics/market/<field>.md` and the "Skills to build" tab |
| D. Outcome | whether the CV led to an interview | months | a question in "My account" 7+ days after the review → long-term reward for A and B |

Flexibility comes from the fact that everything that learns lives in data, not in code: prompt variants in JSON, lessons and market data in Markdown. The code only chooses, weighs and rolls back. Safeguards: an evals gate before merging, automatic shutdown of a losing variant, automatic rollback of lessons if the share of likes dropped.

What we do not do now: fine-tuning (there is no data for it), a vector database of examples (stage 2, when there are 500+ accepted edits).

**Goal 2. Direct links to job postings.** On the "Where to apply" and "CV review" pages, live job postings with links appear under each direction: DOU, Work.ua, Robota.ua, Djinni for Ukraine; Jooble, Adzuna, Arbeitnow, Remotive, Jobicy for UK/US/EU/remote; Greenhouse and Lever for a specific company. The model ranks them against the CV and says what is missing. LinkedIn, Indeed and Glassdoor give only ready search links: parsing them is forbidden by their rules, and an account ban is of no use to us.

## 2. Decisions made in the plan

1. LinkedIn: only a deep link with keywords, region and the "internship / entry level" filter already filled in. The same for Indeed and Glassdoor.
2. Sources without keys work right away (DOU RSS, Arbeitnow, Remotive, Jobicy, Greenhouse, Lever). Jooble and Adzuna are enabled when you add free keys in Secrets (`JOOBLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`). Without keys these sources are silently skipped.
3. All job addresses pass the provider's domain allowlist. Everything that comes from outside is shown through `md_escape`; only a verified link is clickable.
4. Events are stored without CV text: only identifiers, scores, decisions and the knowledge version. Deleting data also erases the events.
5. Automatic changes to knowledge (lessons, variants, market) go through a PR from GitHub Actions with the evals results. Merging is automatic if the repository has the variable `LEARN_AUTOMERGE=1`, otherwise you press merge.

## 3. Architecture

```
cvmax/jobs/                 job search
  models.py                 Vacancy, JobQuery
  http.py                   fetch with timeout, size limit, cache and parallelism
  providers/*.py            dou, workua, robotaua, djinni, jooble, adzuna, arbeitnow, remotive, jobicy,
                            greenhouse, lever, linkedin (deep link), indeed (deep link), glassdoor (deep link)
  search.py                 routing by region, deduplication, level filter, preliminary ranking
  rank.py                   LLM ranking: fit 0-100, why, what is missing
cvmax/learning/
  events.py                 dictionary of events and their payloads
  bandit.py                 Thompson sampling over prompt variants, auto-shutdown
  version.py                knowledge hash (rubrics + lessons + market + variants)
cvmax/variants/analysis.json   prompt variants (data)
cvmax/rubrics/learned/*.md     lessons from feedback (written by scripts/learn.py)
cvmax/rubrics/market/*.md      skills market (written by scripts/learn_market.py)
scripts/learn.py            weekly reflection + auto-rollback
scripts/snapshot_jobs.py    daily snapshot of job postings into Supabase
scripts/learn_market.py     skill extraction from the snapshot → skill demand
.github/workflows/learn.yml, market.yml
supabase/schema.sql         cvmax_events, cvmax_vacancies, cvmax_skill_demand + RPC
```

**Event flow.** `analysis_done` (analysis_id, variant, program, region, score, knowledge_version, previous_score) → `analysis_rated` (rating) → `edits_decided` (accepted, total) → `grill_turn` (answered | skipped) → `export` → `jobs_shown` (source, n) → `job_applied` (source, fit) → `outcome` (interview: yes | no | not_yet). Reward for the bandit: like = 1, dislike = 0; without a like: accepted ≥ 50% of edits with ≥ 2 edits = 1, otherwise 0. `outcome=yes` is added as +2 to the variant's successes.

**Job flow.** direction (role, search_keywords) + region + level → the region's providers in parallel (≤ 8 s) → deduplication → senior-role filter → preliminary ranking by word match and freshness (top 40) → LLM (light model) gives fit and "what is missing" → a list with links, source, date, an "Applied" button → event.

## 4. Agents, models, files

Rules for all: only your own files; do not touch git; code comments in Ukrainian, interface texts in English; no keys in code; the network from the container is blocked, so parsers are written on fixtures, and the live check runs on GitHub Actions and on the server.

| # | Agent | Model | Files | Result |
|---|---|---|---|---|
| R1 | Source researcher | sonnet | report in JSON (no files in the repo) | exact feed and API URLs, response format, which keys are needed, deep links for each site |
| L1 | Database | sonnet | `supabase/schema.sql`, `cvmax/db.py`, `tests/test_learning_db.py` | tables for events, job postings, skill demand; RPC `cvmax_log_event`, `cvmax_variant_stats`, `cvmax_learning_export`, `cvmax_upsert_vacancies`, `cvmax_recent_vacancies`, `cvmax_save_skill_demand`, `cvmax_skill_demand`; methods in SupabaseDB and MemoryDB |
| L2a | Variant author | opus | `cvmax/variants/analysis.json` | 3 review-style variants that really differ |
| L2b | Bandit | sonnet | `cvmax/learning/{__init__,bandit,version,events}.py`, `tests/test_bandit.py` | variant choice, auto-shutdown, knowledge hash |
| E1 | Evals | sonnet | `evals/cases/*`, `evals/run_evals.py`, `evals/baseline.json` | 3 new made-up cases, `--json`, comparison with the baseline |
| J1 | Providers | sonnet | `cvmax/jobs/{models,http}.py`, `cvmax/jobs/providers/*`, `tests/fixtures/jobs/*`, `tests/test_jobs_providers.py`, `requirements.txt` (defusedxml) | each provider: `build_urls`, `parse`, `search_url`, domain allowlist |
| J2 | Search and ranking | sonnet | `cvmax/jobs/{__init__,search,rank,demo}.py`, `cvmax/schemas.py` (+JobFit), `cvmax/demo.py` (+RankedJobs), `cvmax/config.py` (+LIMITS jobs), `tests/test_jobs_search.py` | `search_jobs`, `rank_jobs`, demo data for tests |
| L3 | Reflection | sonnet | `cvmax/prompts.py`, `scripts/learn.py`, `.github/workflows/learn.yml`, `docs/learning/README.md`, `tests/test_learn.py` | lessons in the prompt, a weekly script with the evals gate and auto-rollback |
| J4 | Market | sonnet | `scripts/snapshot_jobs.py`, `scripts/learn_market.py`, `.github/workflows/market.yml`, `tests/test_market.py` | job snapshot, skill extraction, `rubrics/market/*.md` |
| U1 | Interface | sonnet | `views/career.py`, `views/analyze.py`, `views/account.py`, `ui/account.py`, `ui/jobs.py`, `tests/test_pages.py` | job postings under the directions and in the review, events, bandit in the review, outcome question, market in "Skills to build" |
| D1 | Documentation | haiku | `README.md`, `supabase/README.md`, `docs/learning/README.md` | what appeared, which secrets to add, how to apply the SQL |
| V1 | Tests | sonnet | nothing | full `pytest -q`, report |
| V2 | Security | opus | nothing | SSRF, XSS via Markdown, data leak into events, keys |
| V3 | Correctness | opus | nothing | bandit logic, SQL, parsers, UI flows |
| F | Fixes | sonnet | per findings | every confirmed finding fixed, tests green |

Order: R1, L1, L2a, L2b, E1 in parallel → J1 after R1; L3 after L1+L2b+E1 → J2 after J1; J4 after J1+L1 → U1 after J2+L1+L2b → D1 → V1+V2+V3 → F → V1 again (up to 3 rounds).

## 5. Verification

- `pytest -q` is green, together with the old 68 tests.
- Parsers on fixtures: DOU RSS, Work.ua, Robota.ua, Djinni, Jooble, Adzuna, Arbeitnow, Remotive, Jobicy, Greenhouse, Lever. Broken XML and an empty response do not break the page.
- AppTest: "Where to apply" in demo shows job postings with links; "CV review" logs events; "My account" shows the outcome question.
- Bandit: on 1,000 simulated reviews with rewards 0.7 / 0.5 / 0.3 it chooses the best variant in over 70% of cases and switches off the 0.3 variant.
- No address outside the allowlist reaches the screen. No event payload contains CV text.

## 6. What you do

1. Run the new SQL in Supabase (the "learning and jobs" block in `supabase/schema.sql`) or let me apply the migration.
2. Add to GitHub Secrets: `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_LEARN_TOKEN` (a token with the `learning` scope, step 5 in `supabase/README.md`; never the app token `CVMAX_DB_TOKEN`), `GEMINI_API_KEY`. The repository variable `LEARN_AUTOMERGE=1` if you want full autonomy.
3. Record the evals baseline once (without it `learn.yml` and `market.yml` always fail with code 3): with a real `GEMINI_API_KEY` in the environment run `python evals/run_evals.py --runs 2 --update-baseline` and commit `evals/baseline.json`.
4. Optionally: free keys for Jooble (jooble.org/api/about) and Adzuna (developer.adzuna.com) in Streamlit and GitHub Secrets.

## 7. Stage 2 (after the first 100 reviews with feedback)

- Examples of accepted edits in pgvector with Gemini embeddings, selected by line similarity.
- A message after 14 days: "did you get an interview" by email, not only in the account page.
- Separate prompt variants for Q&A and "Where to apply".
- Recalculating the rubric criteria weights from outcomes.
