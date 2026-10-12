# Launch guide: free launch, step by step

The launch is free: no credits, no paywall, no prices. The goal is real users, honest ratings and interview outcomes, so the next decision (whether to charge) rests on data. Daily limits stay.

Each step says what to do, why it is needed and how to check it worked. Screens change, so a label may look slightly different; if something does not match, send a screenshot or the exact error text.

Order matters: steps 1 to 4 prepare accounts and secrets, steps 5 to 8 put things on the internet, steps 9 to 12 connect and test them, and then you launch.

## Where everything lives (so nothing gets lost)

| What | Where |
|---|---|
| App secrets (what the running app reads) | Railway, project → your service → Variables |
| CI secrets (what GitHub Actions reads) | GitHub repo → Settings → Secrets and variables → Actions |
| Landing secrets | Cloudflare → Workers & Pages → your site → Settings → Variables and Secrets |
| Database | Supabase dashboard → your project |
| The files mentioned here | in the repository, for example `supabase/apply-now.sql` |

Never put a key in a file, an email or a chat. Keys live only in the three places above.

## Step 1. Claude API key and a spending cap (10 minutes)

Why: the app now calls Anthropic's Claude API, and it is billed per use. A key pasted into a chat must be treated as exposed, so you make a fresh one that only you have seen, and a monthly cap makes a runaway bill impossible.

1. Open console.anthropic.com and sign in.
2. Settings → API keys → Create key. Name it `getcvmax-prod`. Copy it once into your password manager. You will paste it in steps 4 and 7.
3. Delete or disable the key you pasted in the chat (same page, the key's menu).
4. Settings → Limits (or Billing → Limits): set a monthly spend limit. Start with 30 USD. You can raise it later.
5. Add a payment method and a small credit (20 USD is enough to start) if the console asks.

Check: the Usage page shows 0 USD.

The app uses two models: Claude Sonnet 5.5 for the CV review and edits, and Claude Haiku 5.5 for the cheap tasks (Q&A questions, ranking jobs, checking edits). Opus is never used. If the app later says "Invalid API key" with a key that starts with `sk-ant-usr`, create a standard API key on the API keys page instead.

## Step 2. Run the database SQL (5 minutes)

Why: two functions are missing in your database. One saves "which skills job postings ask for" (the weekly market job fails without it). The other makes "Delete my data" also delete a person's learning events, which the privacy page promises. I checked your database: both are still missing.

1. Open the file `supabase/apply-now.sql` in the repository on GitHub. Click the "Copy raw file" button.
2. Supabase dashboard → your project → SQL Editor → New query → paste → Run.
3. It should say "Success. No rows returned". It is safe to run twice.

Check: tell me it is done and I verify from my side.

## Step 3. Create the token that GitHub uses to read learning data (5 minutes)

Why: the weekly jobs run on GitHub and need to talk to your database. They get their own key that can only use the learning functions and cannot touch saved CVs. If it leaks, the damage is small, and you can replace it any time by running this again.

1. Supabase → SQL Editor → New query → paste this → Run:

```sql
with t as (select 'cvmx_learn_' || encode(extensions.gen_random_bytes(24), 'hex') as token),
     u as (insert into cvmax_private.app_config (id, scope, token_hash)
           select 2, 'learning', encode(extensions.digest(token, 'sha256'), 'hex') from t
           on conflict (scope) do update set token_hash = excluded.token_hash
           returning 1)
select token from t;
```

2. The result shows one value starting with `cvmx_learn_`. Copy it now (it is not stored anywhere in readable form). It goes into GitHub in step 4.

## Step 4. GitHub secrets and settings (10 minutes)

Why: the automatic jobs (weekly lessons, daily vacancy snapshot, weekly link check) run on GitHub and need these values. They never go into the code.

GitHub → your `cvmcc` repository → Settings → Secrets and variables → Actions.

On the Secrets tab, "New repository secret", four times:

| Name | Value | Why |
|---|---|---|
| `ANTHROPIC_API_KEY` | the key from step 1 | the jobs call the model |
| `SUPABASE_URL` | the same as in your app settings | where the database is |
| `SUPABASE_KEY` | the same as in your app settings | the public database key |
| `CVMAX_LEARN_TOKEN` | the token from step 3 | the scoped key for learning data |

On the Variables tab, "New repository variable": name `CVMAX_PROVIDER`, value `claude`. Why: it tells the jobs to use Claude and not a free model.

Then Settings → Actions → General → "Workflow permissions": tick "Allow GitHub Actions to create and approve pull requests" and Save. Why: the jobs open pull requests with what they learned, and you click merge.

Do not create `LEARN_AUTOMERGE`. Why: until you trust the results, you read every automatic change yourself.

## Step 5. Buy the domain (20 minutes, about 10 USD a year)

Why: you need `hello@` email, the address `app.` for the app, and trust (people do not give a CV to a random web address).

1. dash.cloudflare.com → create an account → Domain Registration → Register Domains → search `getcvmax.com` → buy.
2. Turn on auto-renew. The domain now uses Cloudflare's DNS automatically.

If `getcvmax.com` is taken, tell me the name you pick instead and I change every place that mentions it.

## Step 6. Google sign-in (25 minutes)

Why: people sign in with Google so that results are saved and limits are fair. Google must be told your site's address, and the sign-in screen must be switched to public, or only people you list can sign in.

1. console.cloud.google.com → create a project named `GetCVmax`.
2. APIs and Services → OAuth consent screen (in newer screens: Google Auth Platform → Branding and Audience).
   - App name `GetCVmax`, support email = yours, authorised domain `getcvmax.com`.
   - Links: home page `https://getcvmax.com`, privacy `https://getcvmax.com/privacy`, terms `https://getcvmax.com/terms`.
   - Audience: External. Then "Publish app" so the status is **In production**.
   - Scopes: only `openid`, `email`, `profile` (the defaults). Do not add more: extra scopes trigger Google's review.
3. Credentials → Create credentials → OAuth client ID → Web application.
   - Authorised redirect URI: `https://app.getcvmax.com/oauth2callback`
4. Copy the Client ID and Client secret into your password manager. You need them in step 7.

## Step 7. Put the app on the internet with Railway (45 minutes)

Why: the app has to run somewhere that does not fall asleep and can use your own address. The free Streamlit hosting cannot do either. Railway costs a few dollars a month at launch size (check Railway's pricing page).

1. railway.com → sign in with GitHub → New Project → Deploy from GitHub repo → choose `cvmcc`. If it asks, give it access to that one repository.
2. Railway finds the `Dockerfile` itself. Open the service → Settings → set the health check path to `/_stcore/health`. Why: so Railway restarts the app if it hangs.
3. Region: choose an EU region if Railway offers it. Why: users are in Europe, and the privacy text is simpler.
4. Variables tab → add:

| Name | Value | Why |
|---|---|---|
| `ANTHROPIC_API_KEY` | key from step 1 | the model |
| `CVMAX_PROVIDER` | `claude` | do not fall back to a free model |
| `SUPABASE_URL`, `SUPABASE_KEY` | your values | the database |
| `CVMAX_DB_TOKEN` | the token you already have in your current app settings | lets the app use the database |
| `CVMAX_CONTACT` | `hello@getcvmax.com` | shown on the legal pages |
| `CVMAX_HOSTING` | `Railway` | the privacy page names where the app runs |
| `CVMAX_LIMITS` | `analysis=5:100,jobs=10:300` | the real spending cap (see "Money" below) |
| `STREAMLIT_SECRETS_TOML` | the block below | Google sign-in settings |

The `STREAMLIT_SECRETS_TOML` value (replace the three placeholders; `cookie_secret` is any random text of 40 or more characters from a password generator):

```toml
[auth]
redirect_uri = "https://app.getcvmax.com/oauth2callback"
cookie_secret = "PASTE-40-RANDOM-CHARACTERS"
[auth.google]
client_id = "PASTE-GOOGLE-CLIENT-ID"
client_secret = "PASTE-GOOGLE-CLIENT-SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

5. Deploy. The first build takes a few minutes. Watch the logs until you see the app listening.
6. Service → Settings → Networking → Custom domain → `app.getcvmax.com`. Railway shows a CNAME target (and maybe a TXT record). In Cloudflare → DNS → add exactly those records, with the cloud set to "DNS only" (grey). Wait a few minutes until Railway shows the domain as ready.

Check: open `https://app.getcvmax.com`, the home page loads, and "Sign in with Google" works with your own account.

## Step 8. The landing page on Cloudflare Pages (25 minutes)

Why: this is the page you share. It collects emails for "one email when something new ships".

1. Cloudflare → Workers and Pages → Create → Pages → Connect to Git → choose `cvmcc`, branch `main`.
2. Build command: leave empty. Build output directory: `site`.
3. Settings → Variables and Secrets: add three (type Secret) `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` (same values as the app). Why: the email form writes to your database through a small server function.
4. Custom domains → add `getcvmax.com` and `www.getcvmax.com`.
5. Metrics → Web Analytics → Enable. Why: counts visitors without cookies, so no cookie banner is needed.
6. Email → Email Routing → create `hello@getcvmax.com` forwarding to your inbox. Why: the legal pages promise an address that works.

Check: `https://getcvmax.com` loads. Submit your own email in the form; it should say "We'll email you once when something new ships".

## Step 9. Point the landing buttons at the app (2 minutes)

Why: until this is set, every "Try it free" button scrolls to the email form instead of opening the app.

On GitHub, open `site/site.js`, click the pencil icon, change line 4 to `var APP_URL = "https://app.getcvmax.com";`, commit. Cloudflare redeploys in a minute. Or tell me the address and I do it.

## Step 10. Legal names (15 minutes)

Why: Terms and Privacy must name who runs the service. I do not know your legal details.

1. In `site/terms.html`, `site/privacy.html`, `site/refund.html` replace every `FOP [Full legal name]` with your registered name (GitHub web editor, pencil icon).
2. Read the Terms and Privacy pages once. I rewrote them for a free launch, but I am not a lawyer. Two things to check: the liability sentence ("the amount you paid us, if any") and the new paragraph about learning events.

## Step 11. Record the quality baseline (10 minutes, about 4 USD of Claude usage)

Why: later, every prompt or rubric change is tested against this baseline, so a change that makes reviews worse is blocked automatically. Without it the weekly job refuses to run, on purpose.

1. GitHub → Actions → "Weekly lessons" → Run workflow → tick `bootstrap_baseline` → Run.
2. It takes 5 to 10 minutes and opens a pull request with two small files. Open it, check that it only changes `evals/baseline.json` and `evals/synth_baseline.json`, and click Merge.
3. Then Actions → "Weekly lessons" → the three dots → Disable workflow. Why: the weekly run costs real money (my estimate: up to about 14 USD a week on Sonnet) and has nothing to learn from until there are rated reviews. Turn it back on in week 3 of the launch.

## Step 12. Test like a stranger (1 hour)

Why: every problem you find now is one a user does not find.

1. Open a private window. Use a Google account that is not yours.
2. Sign in, finish onboarding, upload a made-up CV (never your own real one), run a review.
3. Check: score, line-by-line, Edits, Q&A, Skills to build (a "Learn it" block with resources), Jobs, download PDF and DOCX, the score card.
4. My account → delete your data. Sign in again and confirm it is gone.
5. Repeat the first four on your phone.
6. In Supabase → SQL Editor: `select * from cvmax_private.events_by_day order by 1 desc limit 14;` should show events.
7. In the Anthropic Console → Usage: divide today's cost by the number of reviews you ran. That is your real cost per review.

Then give 10 trusted people their own real CVs and ask: "What confused you first, and what felt wrong first?" Fix what repeats.

## Money: what this will cost

Estimates, not measurements. Check the real numbers in step 12.

| Item | Rough cost |
|---|---|
| Domain | about 10 USD a year |
| Railway | a few USD a month at launch size |
| One CV review on Claude Sonnet 5.5 | on the order of 5 to 15 US cents (price: 2 USD input and 10 USD output per million tokens) |
| Cheap tasks on Claude Haiku 5.5 | a fraction of a cent each (0.10 and 0.50 USD per million tokens) |
| Baseline run (step 11) | about 4 USD, once |

The site-wide cap in `CVMAX_LIMITS` is what bounds spend: with `analysis=5:100` at most 100 reviews a day, so at 15 cents the worst case is about 15 USD a day, and the monthly limit from step 1 stops anything beyond that. Raise the numbers only after you measured the real cost per review.

If you want cheaper reviews later, the way to decide is the quality test: run the same checks with `CVMAX_MODEL=claude-haiku-5-5` and compare to the baseline. If quality holds, it is about 20 times cheaper.

## Launch (after steps 1 to 12)

Use one tag per channel: `getcvmax.com/?ref=<channel>`. The texts for each channel are in `docs/launch.md`.

| Day | Do | Tag |
|---|---|---|
| 1 | Pinned post on your own Telegram and X, a 60 to 90 second segment in your next YouTube video (screen recording of a review of a made-up CV, link in the description and a pinned comment) | `yt-<video>`, `tg`, `x` |
| 1 | The DOU forum post. Read the forum rules first, reply to every comment for 48 hours, do not defend, ask what is wrong | `dou` |
| 2 to 3 | Student Telegram channels: ask the admin first, offer a live "CV roast" of 3 to 5 volunteered CVs, link in the last post | `tg-<channel>` |
| 2 to 7 | Ten slideshows (TikTok, Reels, Shorts), one a day, the scripts are in `docs/launch.md` | `tt`, `ig`, `yts` |
| 3 to 7 | Career centres and student organisations (KSE, KPI, UCU, KNEU, AIESEC, BEST): a short email offering the tool free for their students | `uni-<name>` |
| 7 | Partner letters to Djinni, Robota.ua and DOU: ask for distribution, do not give away the code | `djinni`, `robota`, `dou-partner` |
| 14 | Product Hunt, Peerlist or Microlaunch, only after 50 real reviews and a stable week | `ph` |

Rules: never post a real person's CV, only made-up or anonymised ones with written consent; no fake reviews; answer every comment; if a post is removed, do not repost it.

## Read the data every morning (10 minutes)

| Question | Where |
|---|---|
| How many people arrived | Cloudflare → Web Analytics |
| How many left an email, from which channel | `select * from cvmax_private.waitlist_by_source;` |
| How many signed up | `select count(*) from public.cvmax_users;` |
| How many reviews per day | `select * from cvmax_private.events_by_day order by 1 desc limit 14;` |
| Do people like the reviews | `select * from cvmax_private.variant_performance;` |
| Do people accept the edits | `select * from cvmax_private.edit_acceptance limit 50;` |
| What breaks | Railway → the service → Logs |
| What it costs | Anthropic Console → Usage, divided by reviews |

## Gates before any talk of payment

| Gate | Passes when |
|---|---|
| Interest | 100 sign-ups and 50 completed reviews within 14 days, from at least 2 channels |
| Quality | at least 60% thumbs up and at least 30% of reviewers accept half or more of the edits |
| Pull | at least 25% come back for a second review within 14 days |
| Outcomes | 50 answers to "did you get an interview" |

## If something goes wrong

| Symptom | Likely cause and fix |
|---|---|
| "Sign in with Google" says redirect_uri_mismatch | the redirect URI in step 6 and in `STREAMLIT_SECRETS_TOML` differ by even one character |
| Sign-in works only for you | the Google consent screen is still in Testing: publish it (step 6) |
| "Invalid API key" | wrong key or a key type that is not a standard API key: create a new one (step 1) |
| Reviews stop with a limit message | the daily cap in `CVMAX_LIMITS` is reached; look at the cost and raise it on purpose |
| Weekly job fails with code 3 | the baseline is not recorded (step 11) |
| App is slow on the first open | Railway is starting a new container; refresh once |
