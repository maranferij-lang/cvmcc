# GetCVmax project log

A running log of decisions, state and next steps, so a new session (or a new context window) can pick up without the chat history. Newest entries on top. No keys, tokens, passwords or personal data here, ever.

## How to use this file

- At the start of a session: read `CLAUDE.md`, then this file from the top.
- At the end of a session or after a milestone: add a dated entry on top (what changed, what is pending, who does it).

---

## 2026-10-18 (later): no FOP, free beta, security audit

- Owner has no FOP and does not want a legal name on the site yet: site back to free-beta copy (no prices); operator named as "Marian, an individual based in Ukraine". Payments stay built but off. Before payments or a big public launch a legal entity (FOP) is needed.
- Limits tightened in code (per user / site per day): analysis 2/30, match 2/30, grill 2/30, career 1/15, builder 1/10, build 2/20, export 3/40, jobs 5/150, upload 10/400, grill_retry 6/120; plus a per-IP limit of 3x the account limit (salted hash, last X-Forwarded-For entry). Owner should delete `CVMAX_LIMITS` in Railway so these apply.
- Pre-launch audit (Opus) fixed: Export tab CPU stall (15 s per rerun → computed once), model text escaped in warnings and .md export, parser slot wait capped, upload limit, delimiter defanging in all prompts, Q&A retry limit, generic API error text. 1643 tests green.
- Known remaining: waitlist form can be spammed by script (add Cloudflare Turnstile or a rate-limit rule later); PDF bytes sent to the model are not defanged (text is).
- Cost per review: estimate $0.10-0.15 (owner to confirm from Anthropic Usage: spend / ~85 eval reviews).

---

## 2026-10-18: live, paywall built

- **Live:** app at `app.getcvmax.com` on Railway with Google sign-in (owner via Cowork); site buttons open the app. First real run by the owner: 1 user, 1 result, 1 consented sample with name/email/links/phone removed (checked by markers only).
- **Decision (owner):** free scan + paid fix replaces the free launch (CLAUDE.md updated). Full $15 (first 100 buyers, then $25), Job hunt $29 (then $49). Paddle as merchant of record. Spec `docs/plan-paywall.md`.
- **Built:** `cvmax/billing.py`, billing methods in `cvmax/db.py`, `supabase/apply-billing.sql` (creates the billing token, shown once), `ui/billing.py`, locked rendering in the review/builder/career/account pages, `site/pay.html` + `pay.js`, Paddle webhook as a Pages Function `site/functions/api/paddle-webhook.js`, pricing and legal copy.
- **Reviewed:** adversarial review found 8 defects (refund ate other purchases' credits, unlimited builder builds, builder credit as a free re-scan, return flow broken across tabs, re-scan rules, role normalisation, Jobs tab leaking gaps, copy claims); all fixed with tests. Real SQL now tested in a local Postgres (`tests/test_sql_live.py`); it caught older apply files reverting "Delete my data". 1625 tests green.
- **Billing is off** until the Paddle variables are set; the app stays free until then.

### Owner to do for payments
1. Apply to Paddle (Billing), ask about payouts to a Ukrainian FOP; open a Sandbox account.
2. Give Claude the exact FOP name (UA + Latin) for the legal pages.
3. Run `supabase/apply-billing.sql` once in the SQL editor and save the `cvmx_bill_` token it prints.
4. Follow "Payments (Paddle)" in `docs/launch-guide.md` (products, prices, default payment link, client token, webhook, variables in Railway and Cloudflare).

---

## 2026-10-17: launch prep, real-CV evals, learning loop

### State

- **Landing** `getcvmax.com` is live on Cloudflare Pages (project `getcvmax`, root directory `site`, output empty). www, 404, security headers, Web Analytics, Email Routing `hello@getcvmax.com` → owner's Gmail, waitlist form saves to Supabase (checked end to end, test row removed).
- **App** is not public yet: `app.getcvmax.com` has no DNS record, `site/site.js` has `APP_URL = null`. Supabase: 0 users, 0 waitlist rows.
- **Branches**: `main` and `claude/gemini-provider` both at the latest commit; the owner fast-forwards `main` from the branch.
- **CI** green (it had been red since Oct 11 because CI installs Streamlit 1.66; fixed).
- **Weekly "Job market" job** works again (the GitHub `SUPABASE_KEY` secret was wrong and every save got 401 while the run showed green).

### Decisions (owner)

- Free launch, no payments yet (unchanged).
- Provider: Claude, Sonnet 5.5 heavy, Haiku 5.5 light (unchanged).
- Domain: `getcvmax.com`.
- 22 real student CVs (consent confirmed by the owner) are used for evals, anonymised, only in gitignored `evals/private/students/` and on the owner's Mac.
- Add an opt-in consent "Help improve GetCVmax" (off by default), a weekly independent judge, and a loop that turns judge findings into eval cases and prompt proposals (owner merges by hand).
- The owner will use Claude Cowork in a local browser for the Google OAuth and Railway steps: prompt in `docs/cowork-launch-prompt.md`.

### Done this session (commits on `claude/gemini-provider`)

- `db268ca`, `805176b`: Pages settings in the guide; deploy README no longer served; real 404 page.
- `7625f65`: CI fix for Streamlit 1.66; waitlist function reports bad config, sends new `sb_` keys correctly.
- `7db6edd`: deterministic checks tuned on real CVs (glued headings, Ukrainian weak openers, soft skills, merged-line junk). missing_education false alarms 5 → 0.
- `8fd936e`: **Claude answers stream with a 64k output cap**; before, 6 of 10 real CVs failed with "does not fit the expected format" (output cut at 16k).
- `b7f1888`: prompt protects strong lines, no rewrite without an edit, up to 25 edits. Evals on 22 real CVs: 113/139 → 121/139 and 101/121 (two passes), 0 invented numbers.
- `b114e97`: a connection dropped mid-stream is retried once, then a soft error (it crashed the eval run).

### Eval findings still open (real CVs)

- Weak, irrelevant lines left as keep (sports and volunteering for a marketing role, school project on a Master's CV, vague exchange lines).
- "move" verdicts without an edit.
- A publication line got rewritten in both passes after the prompt change (watch).

### Built after the eval runs (consent, judge, loop)

- Opt-in consent "Help improve GetCVmax" on the review page (signed-in users, default off). When ticked, an anonymised copy of the CV text and its review is saved (`cvmax/anonymize.py`: names from Google and the CV's first line incl. Latin/Cyrillic spellings and inflected forms, emails, phones, links, messenger handles, dates of birth). Kept up to 12 months (enforced in the save function), deleted with "Delete my data". Privacy page section `#improve`.
- Weekly independent judge (`cvmax/judge.py`, `scripts/judge.py`, `.github/workflows/judge.yml`, Monday 05:00 UTC): Sonnet with its own prompt reviews up to 20 opted-in samples, saves findings, PR with a counts-only report in `docs/judge/`. About $0.05 per sample (estimate).
- Loop: `python scripts/judge.py --days 90 --limit 0 --export-cases evals/private/judged` turns judged samples into private eval cases; prompt changes are kept only if evals go up.
- Owner must run `supabase/apply-judge.sql` once in the Supabase SQL editor.
- Adversarial review found 4 privacy holes (case-sensitive links, messenger handles, Cyrillic/inflected names, retention relying on CI); all fixed and tested. 1503 tests green.

### Next

1. Owner: steps 6 (Google OAuth) and 7 (Railway) via Cowork, then tell Claude.
2. Claude: set `APP_URL`, check the live app, replace `FOP [Full legal name]` once the owner gives the exact name.
3. Owner: run `supabase/apply-judge.sql` in the Supabase SQL editor (consent saves fail softly until then).
4. Stranger test, then soft launch.
