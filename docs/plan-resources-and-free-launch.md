# Plan: vetted learning resources and a free launch

Decision from the owner: no credits and no paywall at launch. The product launches free, with the existing daily limits, to collect users, ratings and outcomes first. Part C of `docs/plan-launch-build.md` (billing) is parked and not built. The owner's second requirement for learning resources: nothing sponsored, everything checked, every recommendation explainable. "I do not want to feed rubbish to my users."

## Part E. Vetted learning resources

### Principles (shown to the user in the app, page "How we pick resources")

1. A person checked every resource. Each has a `verified_at` date, a short "why we picked it" and the evidence we saw.
2. Free first. A paid resource appears only when it is clearly better than the free options for that skill, with its price.
3. No sponsors and no affiliate links. If that ever changes, affiliate links get a visible label and never change the ranking.
4. A resource needs a track record: a recognised institution or vendor, or public ratings with a real number of reviews, or a recommendation from a university or government career service. A "best courses" blog post is never evidence.
5. Hands-on beats video-only. Every plan ends with something the user can put on a CV.
6. If we have no vetted resource for a skill, we say so. We never fill the gap with a search link or a model-written link.
7. Links are re-checked every week and broken ones are hidden automatically.

### Data

Files: `cvmax/resources/skills.json` (vocabulary) and `cvmax/resources/catalog/<cluster>.json` for clusters `data`, `software`, `business`, `language_career`, `law_psychology`.

`skills.json`: `{"skills": [{"id": "sql", "label": "SQL", "aliases": ["postgresql", "mysql", "t-sql", "databases", "joins", "group by", "window functions"], "programs": ["economics_big_data", "software_engineering", "business_economics", "artificial_intelligence"]}]}`. Skill ids are lower case and match the market-skill names in `cvmax/learning/market.py` (`sql`, `excel`, `power bi`, `python`, `javascript`, `machine learning`, `english`).

Catalog file: `{"cluster": "data", "resources": [Resource, ...]}` with

```json
{
  "id": "mode-sql-tutorial",                 // lower-case slug, unique across all files
  "title": "SQL Tutorial for Data Analysis",
  "provider": "Mode",
  "url": "https://mode.com/sql-tutorial/",   // https only
  "kind": "course | tutorial | video-course | practice | book | certificate | exam | channel",
  "skills": ["sql"],                          // ids from skills.json, 1 to 3
  "level": "beginner | intermediate | advanced",
  "cost": "free | free-audit | freemium | paid",
  "price_usd": null,                          // number for paid and freemium, else null
  "hours": 8,                                 // honest estimate, integer
  "languages": ["en"],                        // add "uk" only when the resource itself is in Ukrainian
  "hands_on": true,
  "output": "A SQL analysis of a public dataset, with the queries in a GitHub repo",
  "why_picked": "At most 200 characters, specific, no marketing words",
  "evidence": [ {"claim": "What we saw", "url": "https://...", "seen": "2026-10-12"} ],
  "rating": {"value": 4.8, "scale": 5, "count": 15300, "source_url": "https://...", "seen": "2026-10-12"},
  "updated_year": 2025,                       // year the content was last updated, null if unknown
  "verified_at": "2026-10-12",
  "verification": "fetched | search-snippet",
  "status": "active | needs_check | dead"
}
```

Rules enforced by `cvmax.resources.catalog.validate()` and by tests: all fields present and typed; `id` unique and equal to a slug of the title or provider; `url` is https and its host is not an IP, a shortener or a known affiliate redirector; at least 1 `evidence` item for free resources and at least 2 for `paid` and `freemium`; evidence urls are https; `rating` needs `count >= 1000` and `value / scale >= 0.9` when present; `verified_at` is an ISO date not older than 400 days and not in the future; `why_picked` at most 200 characters; `skills` all exist; `hours` between 1 and 400; `cost` paid or freemium needs `price_usd`; `status` active for everything that ships.

### Selection (`cvmax/resources/select.py`, deterministic, no model)

```python
def match_skills(text: str) -> list[str]                      # alias match on word boundaries, longest alias first, max 3
def quality_points(r: Resource, votes: Votes | None = None) -> tuple[int, list[str]]   # points and the human-readable reasons
def pick(skill: str, *, budget: str = "free", level: str | None = None, language: str | None = None, limit: int = 3) -> list[Pick]
```

Points: free +3, free-audit +2, freemium +1; hands_on +2; evidence count capped at 3, one point each; rating with count >= 5000 and value/scale >= 0.92 +2, else with count >= 1000 +1; `updated_year` within 3 years +1; language match +2 (the user's feedback language "Ukrainian" prefers `uk`); level match +1; user votes: with at least 20 votes, helpful share >= 0.8 gives +2 and < 0.5 gives -3. Ties break by `id`. `budget="free"` removes paid; `budget="any"` allows at most one paid item and always keeps the best free one first when it exists. `Pick` carries the resource, the points and the `reasons` list that is shown verbatim ("Free", "Hands-on", "4.8 from 15,300 ratings (seen Oct 2026)", "Updated 2025"). No resource with `status != "active"` is ever returned.

### Plan (`cvmax/resources/plan.py`, deterministic)

`build_plan(picks: list[Pick], hours_per_week: int) -> list[PlanStep]`: orders the picks by level, converts hours to weeks (ceil, at least 1), and ends with a final step "Build and publish: <output>" naming the CV line the user can add. No model call.

### Interface

- `views/analyze.py`, "Skills to build" tab: under each gap, a "Learn it" block: two selectors (Free only or Free and paid; hours per week 3, 5, 10) kept per analysis, then up to 3 resource cards: title as a link button, provider, cost badge, hours, level, `reasons` as a caption, "How we checked this" expander with the evidence links and `verified_at`, and two buttons "Helpful" and "Not helpful". When a gap maps to no skill or no resource: "We don't have a vetted resource for this yet." and the event `resource_missing`. Gaps sorted so that skills with higher market demand (existing `skill_demand_cached`) come first when demand data exists.
- New page `views/learn.py` ("Learn", icon school) in the main navigation: pick a skill, same cards, plus the "How we pick resources" section with the seven principles and a "Report a bad link or suggest one" link to the feedback page.
- Every title, provider, reason and evidence text goes through `md_escape`; every link goes through `ui.jobs.safe_url`-style validation (https, host equals the catalog entry's host).
- Events (`cvmax/learning/events.py`): `resource_clicked` (`skill`, `resource_id`, `rank`), `resource_rated` (`resource_id`, `helpful`), `resource_missing` (`skill`). Votes are read back from `cvmax_events` through `db.learning_export` only after the weekly job; until then `votes=None`.

### Link health (`scripts/check_links.py`, `.github/workflows/resources.yml`)

Weekly (Mondays 05:00 UTC) and manual. The read-only job runs the checker; the PR job opens a pull request with `cvmax/resources/link_status.json` (`{id: {"ok": bool, "code": int|null, "checked": "YYYY-MM-DD", "fails": int}}`). A URL is `dead` after 2 consecutive failures (a 403 or 429 from bot protection counts as unknown, not a failure). The app hides resources whose status in `link_status.json` is dead. The workflow follows the read-only/write job split of `learn.yml` and never auto-merges.

### Catalog content process

Researchers (one per cluster, with web access) propose 2 to 3 resources per skill, at least one free; verifiers (different agents) re-open every URL and every evidence link; one judge pass culls weak entries and writes `docs/resources-review.md`, a table of every entry (skill, resource, cost, hours, why picked, evidence, verification) so the owner can veto anything by deleting a row's id.

Clusters and skill ids:

- `data`: `sql`, `excel`, `power bi`, `tableau`, `statistics`, `r`, `data visualization`, `a/b testing`, `google analytics`, `python`, `pandas`.
- `software`: `python`, `javascript`, `typescript`, `java`, `git`, `algorithms`, `system design`, `docker`, `aws`, `linux`, `rest api`, `react`, `software testing`, `machine learning`, `deep learning`, `prompt engineering`.
- `business`: `financial modeling`, `valuation`, `accounting`, `powerpoint`, `market research`, `case interview`, `marketing analytics`, `project management`, `product management`, `scrum`, `business analysis`, `acca`.
- `language_career`: `english` (split by `ielts`, `toefl`, `business english`), `interview skills`, `public speaking`, `business writing`, `linkedin profile`, `github profile`.
- `law_psychology`: `legal research`, `legal writing`, `contract law basics`, `gdpr`, `compliance`, `research methods`, `spss`, `academic writing`, `ux research`, `people analytics`, `statistics`, `r`.

Where a skill has a good Ukrainian-language resource from a reputable provider (for example Prometheus, EdEra, Diia Digital Education), include it with `languages: ["uk"]`.

## Part F. Free launch

- Landing (`site/`): the hero and nav button become "Try it free" and lead to the app when `APP_URL` in `site.js` is set (the owner sets it); the pricing section becomes "Free while we launch": what is included, the daily limit (5 reviews a day), and "Paid options may come later. We will email you before anything changes, and everything you made stays yours." No prices anywhere, no "100 left", no "14-day refund" promises for something that is free, JSON-LD offer price 0. The waitlist becomes "Get one email when something new ships" (no plan radios, the form sends plan `full` as before so the database needs no change). FAQ: "Can I try it for free?" becomes yes; "Is there a subscription?" becomes no; add a question about how resources are picked. `llms.txt` updated.
- Legal pages (`terms.html`, `refund.html`, `privacy.html`): remove purchase language that is not true yet; the refund page says the service is free during launch and that any future paid plan will state its own refund terms; the owner reviews all legal text. Keep the `FOP [Full legal name]` placeholder.
- `docs/launch.md`: the DOU, Telegram and slideshow texts rewritten for a free launch (no prices, no "pay once"); keep the style.
- App copy: any mention of prices or purchases in `views/` is removed or made true.
- Capacity: `cvmax/config.py` reads optional overrides from `CVMAX_LIMITS` (`analysis=10:200,jobs=20:600` meaning per-user:global per day) so the owner can raise limits after enabling paid Gemini, without a code change. Invalid entries are ignored and logged.
- `docs/launch-guide.md`: the owner's step-by-step launch guide for a free launch.

## Agents

| # | Agent | Model | Files |
|---|---|---|---|
| X1 | Review of match, share card, jobs descriptions, views (read-only) | opus | none |
| X2 | Fact-check of regional and program rubrics, applies corrections | opus | `cvmax/rubrics/**`, `docs/sources.md` |
| X3 | Wire regions into the prompt | sonnet | `cvmax/prompts.py`, `tests/test_core.py` |
| X4 | Catalog researchers, 5 clusters | sonnet | `cvmax/resources/catalog/<cluster>.json` |
| X5 | Catalog verifiers, 5 clusters | sonnet | the same file, after its researcher |
| X6 | Catalog judge, one pass | opus | `cvmax/resources/catalog/*.json`, `docs/resources-review.md` |
| X7 | Resources core | sonnet | `cvmax/resources/*.py`, `cvmax/resources/skills.json`, `cvmax/learning/events.py`, `tests/test_resources.py`, `tests/fixtures/resources/` |
| X8 | Link checker | sonnet, after X7 | `scripts/check_links.py`, `.github/workflows/resources.yml`, `tests/test_check_links.py` |
| X9 | Resources UI | sonnet, after X1 fixes and X7 | `views/analyze.py`, `views/learn.py`, `app.py`, `tests/test_pages.py` |
| X10 | Free-launch site and copy | sonnet | `site/*`, `docs/launch.md`, `views/home.py`, `views/terms.py`, `views/about.py` |
| X11 | Launch guide | sonnet | `docs/launch-guide.md` |
