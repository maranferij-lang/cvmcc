# GetCVmax project log

A running log of decisions, state and next steps, so a new session (or a new context window) can pick up without the chat history. Newest entries on top. No keys, tokens, passwords or personal data here, ever.

## How to use this file

- At the start of a session: read `CLAUDE.md`, then this file from the top.
- At the end of a session or after a milestone: add a dated entry on top (what changed, what is pending, who does it).

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
