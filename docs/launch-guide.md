# Launch guide: free launch, step by step

The launch is free. No credits, no paywall, no prices. The goal of this launch is users, honest ratings and interview outcomes, so the next decision (whether and how to charge) rests on data. Daily limits stay.

Everything below is in the order to do it. Items marked **(you)** only the owner can do. Items marked **(me)** are code or text the assistant writes.

## Phase 0. Make the infrastructure real (about 2 hours of your time)

1. **(you) Run the pending SQL.** Supabase → SQL Editor → paste the whole file `supabase-pending.sql` (kept in the session scratchpad) → Run. It adds the skill-demand function and makes "delete my data" also delete learning events.
2. **(you) Enable billing on the Gemini API project.** Google AI Studio or Google Cloud console → the project that owns `GEMINI_API_KEY` → link a Cloud Billing account. This is mandatory before public launch: on the unpaid quota Google may use prompts to improve its products, and the site tells users the opposite. Set a budget alert in Cloud Billing. A budget alert does not cap spending, so the real cap is the per-day limits in step 6.
3. **(you) GitHub secrets and permissions** (your cvmcc repository, Settings → Secrets and variables → Actions): `SUPABASE_URL`, `SUPABASE_KEY`, `GEMINI_API_KEY`, `CVMAX_LEARN_TOKEN` (the value is in `learn_token.txt` in the scratchpad; never the app token `CVMAX_DB_TOKEN`). Settings → Actions → General → tick "Allow GitHub Actions to create and approve pull requests". Leave `LEARN_AUTOMERGE` unset.
4. **(you) Record the quality baselines once.** Actions → "Weekly lessons" → Run workflow → tick `bootstrap_baseline` → Run. It opens a pull request with `evals/baseline.json` and `evals/synth_baseline.json`. Read it and merge. Until you do, both learning gates stay closed by design. Do this after step 2 so it runs on paid quota.
5. **(you) Google sign-in for everyone.** Google Cloud console → APIs and Services → OAuth consent screen (Audience) → set publishing status to **In production**. Scopes: only `openid`, `email`, `profile`. Add the privacy policy and home page links once the domain is live. Authorised redirect URI: the final app address plus `/oauth2callback`. Without this, only listed test users may be able to sign in.
6. **(you) Raise the daily limits to match your budget.** The defaults are sized for the free model quota (60 reviews a day for the whole site). In the app's settings add for example `CVMAX_LIMITS` = `analysis=5:200,jobs=10:600,match=5:200`. The first number is per user, the second for the whole site per day. Watch the real Gemini cost per review for two days before raising it further. The assistant has not measured that cost.
7. **(you) Host the app.** Two options.
   - Quick: Streamlit Community Cloud. Free, but it cannot use your own domain (only `something.streamlit.app`), idle apps sleep after about 12 hours and memory is about 1 GB. Fine for the first 10 testers.
   - Recommended for the public launch: Railway at `app.getcvmax.com`. Follow `docs/deploy.md` (Dockerfile, variables, domain, health check). It costs a few dollars a month at launch traffic; check Railway's pricing page.
8. **(you) Domain and landing.** Buy `getcvmax.com` (Cloudflare Registrar is the simplest). Cloudflare → Workers & Pages → Create → Pages → Connect to Git → repo `cvmcc`, branch `main`, build command empty, output directory `site`. Add the Pages variables `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN`. Custom domains: `getcvmax.com` and `www`. Metrics → Web Analytics → Enable. Cloudflare → Email Routing: `hello@getcvmax.com` forwards to your inbox.
9. **(you) Fill in the legal placeholders.** Replace `FOP [Full legal name]` in `site/terms.html`, `site/privacy.html`, `site/refund.html` and set `CVMAX_CONTACT` in the app. Read every legal sentence the assistant changed for the free launch (the list is in its report); it is not a lawyer.
10. **(me) Point the landing at the app.** Tell the assistant the final app address and it sets `APP_URL` in `site/site.js`, so every "Try it free" button opens the app with the channel tag attached.

## Phase 1. Test before anyone sees it (2 to 3 days)

11. **Smoke test as a stranger.** Incognito window, a Google account that is not yours: sign in, finish onboarding, upload a synthetic CV (never your own), review, Q&A, accept edits, download PDF and DOCX, open Skills to build and click a resource, open Jobs, download the score card, delete your data. Repeat on a phone.
12. **Check the data landed.** Supabase SQL Editor:
    ```sql
    select count(*) from public.cvmax_users;
    select * from cvmax_private.events_by_day order by 1 desc limit 14;
    select * from cvmax_private.waitlist_by_source;
    ```
13. **Soft launch to 10 people you trust**, each with their own real CV, with this ask: "Tell me the first thing that confused you and the first thing that felt wrong." Fix what repeats. Add any wrong verdict as a case in `evals/private/` (never commit real CVs).
14. **Check the cost.** After the 10 reviews, open the Gemini usage page and Cloud Billing, divide by the number of reviews. That number decides how far to raise `CVMAX_LIMITS`.

## Phase 2. Public launch (week 1 to 2)

Use one tag per channel so you can see what works: `getcvmax.com/?ref=<channel>`. The tag shows up in `cvmax_private.waitlist_by_source`. Texts for each channel are in `docs/launch.md`.

| Day | Do | Tag |
|---|---|---|
| 1 | Pinned post on your own Telegram and X, a 60 to 90 second segment in your next YouTube video (screen recording of a real review of a synthetic CV, link in the description and a pinned comment) | `yt-<video>`, `tg`, `x` |
| 1 | The DOU forum post. Read the forum rules first, reply to every comment for 48 hours, do not defend, ask what is wrong | `dou` |
| 2 to 3 | Student Telegram channels: ask the admin first, offer a live "CV roast" of 3 to 5 volunteered CVs in their channel, link in the last post | `tg-<channel>` |
| 2 to 7 | Ten slideshows (TikTok, Reels, Shorts), one a day, the ten scripts are in `docs/launch.md` | `tt`, `ig`, `yts` |
| 3 to 7 | Career centres and student organisations (KSE, KPI, UCU, KNEU, AIESEC, BEST): a short email offering the tool free for their students | `uni-<name>` |
| 7 | Partner letters to Djinni, Robota.ua and DOU: a link on their vacancy pages or digest. Ask for distribution, do not give away the code | `djinni`, `robota`, `dou-partner` |
| 14 | Product Hunt, Peerlist or Microlaunch, only after at least 50 real reviews and a stable week | `ph` |

Rules of conduct: never post a real person's CV, only synthetic or anonymised ones with written consent; no fake reviews, no testimonials on the site (decision already made); answer every comment; if a post gets removed, do not repost it.

## Phase 3. Read the data every morning (10 minutes)

| Question | Where |
|---|---|
| How many people arrived | Cloudflare Web Analytics |
| How many left an email, from which channel | `select * from cvmax_private.waitlist_by_source;` |
| How many signed up | `select count(*) from public.cvmax_users;` |
| How many reviews per day | `select * from cvmax_private.events_by_day order by 1 desc limit 14;` |
| Do people like the reviews | `select * from cvmax_private.variant_performance;` and the thumbs rating |
| Do people accept the edits | `select * from cvmax_private.edit_acceptance limit 50;` |
| What breaks | Railway or Streamlit logs, the Feedback page |
| What it costs | Cloud Billing, divided by reviews |

## Phase 4. Gates (no credits until they pass)

| Gate | Pass when | Then |
|---|---|---|
| A: interest | 100 sign-ups and 50 completed reviews within 14 days from at least 2 channels | keep going; if not, change the landing headline and the first screen, not the product |
| B: quality | at least 60% of rated reviews are thumbs up and at least 30% of reviewers accept half or more of the edits | if not, fix the review before growing |
| C: pull | at least 25% of users return for a second review within 14 days, or ask for more than the daily limit | this is the signal that people would pay: only then plan paid options |
| D: outcomes | 50 answers to "did you get an interview" | start tuning the score weights from real outcomes |

## What the assistant is building in parallel (code)

- Vetted learning resources in the Skills tab and a Learn page: a verified catalog, a visible reason and verification date for every link, no sponsors, no affiliate links, weekly link checks. Spec: `docs/plan-resources-and-free-launch.md`.
- Match score against a job posting, share card, regional CV conventions, full job descriptions.
- Free-launch landing, legal pages and launch texts, a Dockerfile and Railway guide, the `CVMAX_LIMITS` override.
- Next: log which channel each user came from, so the app, not only the landing, can attribute sign-ups.
