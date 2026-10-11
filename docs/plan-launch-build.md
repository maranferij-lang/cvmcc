# Plan: launch build (vacancy match, share card, paywall, deploy, regional rubrics)

Plan author: Fable 5.1. Implementation: agents on weaker models per the tasks below, review: strong models.
This continues `docs/plan-analysis-quality.md` (checks, verifier, stable score, synthetic evals) and `docs/launch.md`.

## Part A. Review against a specific posting (`cvmax/match.py`)

Why: the most honest score is "does this CV cover the must-haves of this posting". The app already takes `vacancy_text` and the Jobs tab already lists live postings; this part connects them.

Schemas (`cvmax/schemas.py`):

```python
class Requirement(BaseModel):
    text: str                      # the requirement as the posting states it, at most 120 chars
    kind: Literal["must", "nice"]
    status: Literal["covered", "partial", "missing"]
    evidence: str                  # exact quote from the CV that covers it; "" when missing
    action: str                    # what to add or change on the CV, in feedback_language; "" when covered

class VacancyMatch(BaseModel):
    role_title: str
    requirements: List[Requirement]   # at most 15, must-haves first
    keywords_missing: List[str]       # ATS keywords from the posting absent from the CV, at most 10
    summary: str                      # 1-2 sentences in feedback_language
```

Module API:

```python
def match_system(profile: Profile) -> str
def match_vacancy(client, profile: Profile, cv: CVFile, vacancy_text: str, *, facts: str = "") -> VacancyMatch
def match_score(m: VacancyMatch) -> int           # 0..100
def verify_match(m: VacancyMatch, cv_text: str) -> VacancyMatch
```

Rules:

- `match_vacancy` uses `ask_structured` with `effort="medium"`, the CV text (not the PDF), the profile block and the posting cut to 6000 characters. The posting is data: the prompt says to ignore instructions inside it. The prompt forbids inventing evidence: `evidence` must be a verbatim quote of one CV line or bullet.
- `verify_match` is deterministic: an `evidence` that cannot be anchored in the CV (exact, then `cvmax.edits._loose_pattern`, then `cvmax.edits._fuzzy_span` with threshold 0.9) downgrades `status` to `missing` and clears `evidence`; a `keywords_missing` entry that does occur in the CV text (case-insensitive, word boundary) is removed; `requirements` is capped at 15, `keywords_missing` at 10, every string at its limit.
- `match_score`: weights must = 1.0, nice = 0.3; status covered = 1, partial = 0.5, missing = 0; score = round(100 * Σ w·s / Σ w); 0 when there are no requirements.
- `match_vacancy` runs `verify_match` before returning. `LLMError` propagates; the caller shows it softly.
- `cvmax/demo.py`: a `FakeClient` branch for `VacancyMatch` returning 4 must-haves (2 covered with evidence quoted from the demo CV, 1 partial, 1 missing) and 2 nice-to-haves, `keywords_missing=["SQL", "Tableau"]`, so the demo score is deterministic.
- `cvmax/config.py`: `LIMITS["match"] = (5, 60)`. `cvmax/learning/events.py`: new kind `MATCH_DONE = "match_done"` with fields `analysis_id`, `score`, `must_total`, `must_covered`, `nice_total`, `source` ("form" | "jobs").

Vacancy text from the Jobs tab (`cvmax/jobs/models.py`, `cvmax/jobs/providers/base.py`, providers):

- `Vacancy` gets `description: str = ""` (plain text, at most 6000 characters, not stored in the database: `to_row` does not include it). `make_vacancy` gets a `description` argument and fills it with `clean_html(description, DESCRIPTION_LEN)` where `DESCRIPTION_LEN = 6000`. Every provider that has a full description (DOU, Djinni, Arbeitnow, Remotive, Jobicy, Greenhouse, Lever, Jooble, Adzuna) passes it; `snippet` stays as it is. Dedupe, prerank and ranking are unchanged.

Interface (`views/analyze.py`, `ui/jobs.py`):

- Overview tab, when `s.profile.vacancy_text` has at least 200 characters: a block "Match to this posting" with the score ("68% · 5 of 7 must-haves covered") and an expander listing every requirement with an icon per status, the evidence (through `md_escape`) or the action. The match is computed once per analysis (session key `match:<analysis_id>`) with `take_limit("match")`; `LLMError` shows `st.info` with the message and does not break the page. Event `match_done` with `source="form"`.
- Jobs tab: every posting card gets a button "Review for this posting". When `vacancy.description` has at least 300 characters, the button copies the description into the form's vacancy text (session state used by the form), sets `s["open_form"] = True`, shows `st.toast("Posting pasted into the form. Click Review my CV.")` and reruns; otherwise it shows `st.info("Open the posting and paste its text into the form above.")`. `ui/jobs.py` gets an optional callback `on_review: Callable[[Vacancy], None] | None` so the career page keeps working without it.

Tests: `tests/test_match.py` (score math, verify downgrades, keyword filtering, FakeClient path, prompt contains the data warning), `tests/test_jobs_providers.py` additions (description filled from fixtures, capped at 6000, absent from `to_row`), `tests/test_pages.py` additions (Overview shows the match expander when vacancy text is given, the Jobs button pastes the description).

## Part B. Share card (`cvmax/share_card.py`)

Why: the only free distribution that scales is people posting their result. The card is the artefact.

```python
CARD_SIZES = {"story": (1080, 1350), "square": (1080, 1080), "og": (1200, 630)}
def render_card(after: int, *, before: int | None = None, role: str = "", size: str = "story") -> bytes   # PNG
```

Rules:

- Pillow only (already installed as a dependency of fpdf2; add `Pillow>=10,<13` to `requirements.txt` explicitly). Fonts from `assets/fonts/LiberationSans-Bold.ttf` and `-Regular.ttf`; no network.
- Style: white paper `#FFFFFF`, ink `#15171C`, red `#E0261B`. Layout: top-left wordmark "GetCVmax" with the red dot (text, not the PNG logo); centre: the score in a hand-drawn-looking red ellipse (draw the ellipse with 6 px stroke, slightly rotated, like the app's metric); when `before` is given: "58 → 84" with the old score struck through in ink and the new one circled in red, plus the caption "after one review"; below: the role line (sanitised: printable characters only, at most 40 characters, otherwise cut with "…"); footer: "Checked with GetCVmax · getcvmax.com". Nothing else, no dates, no names.
- Deterministic: the same arguments give byte-identical PNGs (no timestamps in metadata; `Image.save(..., optimize=True)` without `pnginfo`).
- `render_card` never raises on odd input: scores are clamped to 0..100, unknown `size` falls back to "story".

Interface (`views/analyze.py`): in the Overview tab, under the score caption, a row "Share your score": `st.download_button("Download card (1080×1350)", data=png, file_name="getcvmax-score.png", mime="image/png")` and the square variant. When `s.last_analysis` holds a previous score for the same role (a rescan), the card is the before/after one and the row title is "Your progress: 58 → 84". Event `share_card` (fields `analysis_id`, `before`, `after`, `size`) is logged through the download button's `on_click`. Add `SHARE_CARD` to `cvmax/learning/events.py`.

Tests: `tests/test_share_card.py` (valid PNG of the right size for each variant, deterministic bytes, clamping, long role cut, control characters removed, before/after variant differs from single), `tests/test_pages.py` (the download button exists after a demo review; the progress title appears after a second review of the same role).

## Part C. Paywall and deploy

Decisions (from the landing page `site/index.html`, already published):

| Plan | Price now | Later | Grants |
|---|---|---|---|
| `scan` | $5 | $9 | 1 scan credit |
| `full` | $15 | $25 | 1 full credit (includes 1 re-scan of the same goal) |
| `hunt` | $29 | $49 | 5 full credits + the "Where to apply" page + saved results |
| `upgrade` | $10 | $16 | 1 full credit; offered only when the latest review was a scan |

What a scan shows: score, summary, criterion scores, line verdicts, automatic checks, the 3 highest-priority edits. Locked behind "Unlock the full fix": the remaining edits (their count is shown), Q&A, Skills to build, Jobs, Final CV. What full unlocks: everything on the review page, the CV builder, one re-scan of the same target role. What hunt adds: the "Where to apply" page.

Billing is on only when `PADDLE_API_KEY`, `PADDLE_PRICE_SCAN`, `PADDLE_PRICE_FULL`, `PADDLE_PRICE_HUNT`, `PADDLE_PRICE_UPGRADE` are all set and auth is configured; otherwise the app behaves as today (free, limited by `LIMITS`). Demo mode and tests run with billing off unless a test turns it on with `MemoryDB` credits.

Database (`supabase/schema.sql`, new block "billing"):

```sql
create table if not exists public.cvmax_purchases (
  id bigint generated always as identity primary key,
  user_key text not null,
  plan text not null check (plan in ('scan','full','hunt','upgrade')),
  transaction_id text not null unique,
  scan_credits int not null default 0,
  full_credits int not null default 0,
  amount_cents int,
  currency text,
  status text not null default 'paid' check (status in ('paid','refunded')),
  created_at timestamptz not null default now()
);
create table if not exists public.cvmax_credit_use (
  id bigint generated always as identity primary key,
  user_key text not null,
  kind text not null check (kind in ('scan','full')),
  analysis_id text not null unique,
  target_role text not null default '',
  rescans_left int not null default 0,
  refunded boolean not null default false,
  created_at timestamptz not null default now()
);
```

RPCs (security definer, `set search_path = ''`, `check_token(p_token, 'billing')` for the webhook ones, `check_token(p_token)` for the app ones; scope `billing` is added to `app_config` like `learning`):

- `cvmax_grant_purchase(p_token, p_user_key, p_plan, p_transaction_id, p_amount_cents, p_currency) returns jsonb`: idempotent on `transaction_id` (returns `{"granted": false, "reason": "duplicate"}` the second time); maps plan to credits as in the table above. Scope: `billing` or `app` (the app grants after it confirms a transaction with the Paddle API; the webhook grants too).
- `cvmax_refund_purchase(p_token, p_transaction_id) returns jsonb`: marks the purchase refunded and removes its unconsumed credits (never below zero). Scope `billing`.
- `cvmax_credits(p_token, p_user_key) returns jsonb`: `{"scan": n, "full": n, "has_full": bool, "has_hunt": bool, "last_use": {...} | null}` where credits are granted minus used (not refunded).
- `cvmax_use_credit(p_token, p_user_key, p_kind, p_analysis_id, p_target_role) returns jsonb`: atomic (advisory lock per user); when `p_kind = 'full'` and the latest full use has the same `target_role` and `rescans_left > 0`, it decrements `rescans_left` and returns `{"allowed": true, "mode": "rescan"}` instead of spending a credit; otherwise spends a credit of that kind (`rescans_left = 1` for full) or returns `{"allowed": false}`.
- `cvmax_refund_credit(p_token, p_user_key, p_analysis_id)`: marks the use refunded (the model failed), so the credit comes back.
- `cvmax_delete_my_data` also deletes the user's rows in both tables. Grants only to `anon`; RLS on; views `cvmax_private.revenue_by_day` for the dashboard.

Python (`cvmax/billing.py`, `cvmax/db.py`, `ui/billing.py`):

- Checkout flow (Paddle Billing, hosted on our static site): the app creates a transaction through the API and sends the user to `checkout.url`, which Paddle builds from the account's default payment link plus `?_ptxn=<transaction id>`. The default payment link is our own page `https://getcvmax.com/pay` (`site/pay.html` + `site/pay.js`): it loads Paddle.js, calls `Paddle.Initialize({token: <client-side token>, environment, checkout: {settings: {successUrl: "https://app.getcvmax.com/?paid=1", displayMode: "overlay"}}})`, and Paddle.js opens the checkout for the `_ptxn` transaction automatically. The client-side token and environment live in `data-` attributes on the page (public values, set by the owner; sandbox by default). After payment Paddle redirects to the app with `?paid=1`; the app verifies the pending transaction through the API (`GET /transactions/{id}`, status `completed`) and grants credits itself, so the happy path does not depend on the webhook. The webhook grants too (idempotent) and handles refunds.
- Customer resolution: before creating the transaction, `POST /customers {email}`; on `409 customer_already_exists` parse the `ctm_` id from the error detail (fall back to `GET /customers?email=`), and pass `customer_id` so the email is prefilled. Any failure here falls back to a transaction without a customer (the checkout then asks for the email).
- `cvmax/billing.py`: `PLANS` (price ids from env, credits, labels, prices from `config.PRICES`), `PaddleClient(api_key, env)` with `create_checkout(plan, user_key, email) -> CheckoutLink(transaction_id, url)` (POST `/transactions` with `items=[{price_id, quantity: 1}]`, `custom_data={"user_key", "plan", "app": "getcvmax"}`, `customer_id` when resolved; base URL `https://api.paddle.com` or `https://sandbox-api.paddle.com`; `Authorization: Bearer`; timeout 10 s; `requests`), `get_transaction(id) -> dict`, `verify_signature(secret, header, body, now, tolerance=300) -> bool` (HMAC-SHA256 over `ts:body`, hex, `hmac.compare_digest`, replay window; the header can carry several `h1` values during secret rotation, any match passes), `parse_event(body) -> PaddleEvent(event_id, type, transaction_id, custom_data, total_cents, currency, action, status)`. A `FakePaddle` for tests and demo. The plan in `custom_data` is never trusted for granting: the grant uses the `price_id` of the first item mapped back to a plan, and the `user_key` in `custom_data` is only accepted when the event signature verified.
- `cvmax/db.py`: `grant_purchase`, `refund_purchase`, `credits`, `use_credit`, `refund_credit` on `SupabaseDB` and `MemoryDB` (same semantics, including the rescan rule).
- `ui/billing.py`: `billing_enabled()`, `credits_cached()`, `paywall(plan_hint)` (the three plan cards with `st.link_button` to the checkout URL created on click; the `?plan=` and `?src=` query params preselect a card and are logged as event `checkout_started`), `on_return()` (when `?paid=1` is in the query: look up the pending transaction id in the session, `get_transaction`, grant on `completed`, clear the query params, toast), `entitlement_for_review(target_role) -> "full" | "scan" | None` (calls `use_credit`), `refund_on_error(analysis_id)`.
- `views/analyze.py`: before running a review, `entitlement_for_review`; a `None` shows the paywall instead of running. The result page gets `s.tier` ("scan" | "full"); in scan tier the locked tabs show a short explanation and an "Unlock the full fix · +$10" button (the `upgrade` plan). `LLMError` during the review refunds the credit. `views/career.py` requires `has_hunt` when billing is on; `views/builder.py` requires `has_full`. `views/account.py` shows credits and purchases. `views/home.py` buttons go to the paywall when billing is on.
- Events: `checkout_started` (plan, source), `purchase_granted` (plan, source: "return" | "webhook"), `credit_used` (kind, mode), `credit_refunded`.

Webhook service (`webhooks/app.py`, Starlette + uvicorn, `requirements-webhooks.txt`): `POST /paddle` reads the raw body (cap 256 KB), verifies `Paddle-Signature` with `PADDLE_WEBHOOK_SECRET`, handles `transaction.completed` (grant, scope `billing` token `CVMAX_BILLING_TOKEN`), `transaction.payment_failed` (log only), `adjustment.created` and `adjustment.updated` with `action = refund` or `chargeback` and status `approved` (refund by transaction id, idempotent); returns 200 for handled and ignored events, 400 for a bad signature, 500 on a database error so Paddle retries. `GET /healthz`. Tests with Starlette's `TestClient` and a `MemoryDB`.

Deploy: `Dockerfile` (python:3.11-slim, non-root user, `pip install -r requirements.txt`, `CMD streamlit run app.py --server.port $PORT --server.address 0.0.0.0 --server.headless true`), `Dockerfile.webhooks` (`uvicorn webhooks.app:app --host 0.0.0.0 --port $PORT`), `.dockerignore`. Railway's config-as-code is deprecated, so the two services are configured in the Railway UI (one repo, two services, each with its Dockerfile path; healthcheck `/_stcore/health` for the app and `/healthz` for the webhooks); `docs/deploy.md` lists every click. It also covers: env vars for each service, Streamlit `[auth]` secrets with the production redirect URI `https://app.getcvmax.com/oauth2callback` (and the same URI in the Google OAuth client), Paddle setup (products and the four prices in sandbox and live; Checkout → Checkout configuration → default payment link `https://getcvmax.com/pay`, with the domain approved; a client-side token for `site/pay.html`; a notification destination `https://hooks.getcvmax.com/paddle` subscribed to `transaction.completed`, `transaction.payment_failed`, `adjustment.created`, `adjustment.updated`), RevenueCat connection (Paddle API key with automatic purchase tracking, charts only), the `billing` token (step 6 in `supabase/README.md`), `site/pay.html` data attributes, and `site/site.js` `APP_URL`.

Tests: `tests/test_billing.py` (signature verification with a known vector, replay window, event parsing, checkout request body, `MemoryDB` credits incl. rescan and refund semantics, idempotent grant), `tests/test_webhooks.py` (good and bad signature, duplicate event, refund path), `tests/test_pages.py` (billing on with `MemoryDB`: paywall shown without credits, scan tier locks tabs, full tier unlocks, upgrade button only after a scan, credit refunded on `LLMError`), `tests/test_security.py` additions (price ids never trusted from the client, custom_data user_key must match the signed event).

## Part D. Regional and program rubric depth (`cvmax/rubrics/regions/*.md`)

Why: the same CV is right for Kyiv and wrong for London. Today the rubric is global.

- Files `cvmax/rubrics/regions/{us_canada,uk,eu,ukraine,remote}.md`, each at most 1500 characters, loaded by `load_rubric(program, region)` as a section "Regional conventions" after the program rubric. Mapping from `profile.REGIONS` to file names lives in `cvmax/prompts.py`.
- Content per region: page length, photo and personal data, date and number formats, spelling (US/UK), CV vs resume wording, education and grade conventions, what local recruiters screen for first, typical ATS. Each claim with a source in `docs/sources.md` (university career services, government career services, large employers).
- Program rubrics: each `cvmax/rubrics/<program>.md` gets a "Screening order" paragraph (what a recruiter for this field reads first, second, third) and two more example bullets, within 4000 characters per file.
- Written by research agents with web access; reviewed by a second strong agent for factual claims; `tests/test_core.py` asserts the regional section appears in the system prompt for each region and that every regional file is under the cap.

## Agents

Rules for all: only your files; no git; code comments and docstrings in English; UI strings and prompts in English; no keys in code; the container has no network to job sites or model APIs, tests use `FakeClient` and `MemoryDB`; use Read/Edit/Write for changes; run `python -m pytest -q <your tests>` from `/home/user/cvmcc`.

| # | Agent | Model | Files | Part |
|---|---|---|---|---|
| M1 | Match | sonnet | `cvmax/match.py`, `cvmax/schemas.py`, `cvmax/demo.py`, `cvmax/config.py`, `cvmax/learning/events.py`, `tests/test_match.py` | A |
| J1 | Descriptions | sonnet | `cvmax/jobs/models.py`, `cvmax/jobs/providers/*.py`, `tests/test_jobs_providers.py` | A |
| S1 | Share card | sonnet | `cvmax/share_card.py`, `requirements.txt`, `tests/test_share_card.py` | B |
| U1 | Interface | sonnet, after M1, J1, S1 | `views/analyze.py`, `ui/jobs.py`, `tests/test_pages.py` | A, B |
| B1 | Billing core | sonnet | `cvmax/billing.py`, `cvmax/db.py`, `cvmax/config.py`, `supabase/schema.sql`, `supabase/README.md`, `tests/test_billing.py` | C |
| B2 | Webhook service | sonnet, after B1 | `webhooks/*`, `requirements-webhooks.txt`, `Dockerfile.webhooks`, `tests/test_webhooks.py` | C |
| B3 | Paywall UI | sonnet, after B1 | `ui/billing.py`, `views/*.py`, `app.py`, `tests/test_pages.py`, `tests/test_security.py` | C |
| B4 | Deploy | sonnet | `Dockerfile`, `.dockerignore`, `railway.toml`, `docs/deploy.md`, `site/site.js` comment, `README.md` | C |
| R1–R3 | Regional research | opus | `cvmax/rubrics/regions/*.md`, `docs/sources.md` | D |
| R4 | Program depth | opus | `cvmax/rubrics/<program>.md` | D |
| P1 | Prompt wiring | sonnet | `cvmax/prompts.py`, `tests/test_core.py` | D |
| V | Review (security, correctness, product) | opus | nothing | all |
| F | Fixes | sonnet | per finding | all |
