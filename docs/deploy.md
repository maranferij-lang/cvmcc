# Deploying GetCVmax to Railway (app.getcvmax.com)

> `docs/launch-guide.md` is the click-by-click guide for the owner (accounts, secrets, domain, launch). This file is the technical reference for the Railway deploy: what the image does, every variable, DNS details, smoke test and rollback.

This is the production deploy: a Docker service on Railway, served at `https://app.getcvmax.com`, that does not sleep.

**Current status: Streamlit Community Cloud is what runs today.** The app is not on Railway yet and `app.getcvmax.com` does not point to it. The privacy text on the site (`site/privacy.html`) already says "Railway (EU): runs the app". That sentence becomes true only after this deploy is finished and the smoke test in step 8 passes. Until then, do not announce the site publicly, or change the privacy text to match what actually runs.
The quick option, [Streamlit Community Cloud](#fallback-streamlit-community-cloud), stays available as a fallback.

Why not stay on Community Cloud: it cannot use a custom domain (only a `*.streamlit.app` address), it puts idle apps to sleep after about 12 hours, and it has a memory cap of roughly 1 GB.

Everything below is done in the Railway, Google Cloud and Cloudflare web UIs. There is no `railway.toml`: config-as-code is deprecated, so the settings live in the dashboard.
The launch is free: there is no payment, no webhook and no billing variable to set.

## What the image does

The `Dockerfile` in the repo root builds the app on `python:3.11-slim`, runs it as a non-root user and installs only `requirements.txt` (all packages ship binary wheels, so no system packages are needed).
The application code is owned by root and read-only for the runtime user, so a compromised app cannot rewrite its own code. The only paths the runtime user can write are `/app/.streamlit` (for `secrets.toml`) and its home directory `/home/app`.
No secret is baked into the image, and `.dockerignore` keeps `.streamlit/secrets.toml`, private CVs, tests, docs and the static site out of it.

At start the entrypoint:

1. If the variable `STREAMLIT_SECRETS_TOML` is set, writes its value to `/app/.streamlit/secrets.toml` with mode 600, removes the variable from the process environment, and checks that the file is valid TOML. It never prints the value. If the TOML is broken the container exits with a short message, so the deploy fails and the previous deployment keeps serving.
2. Starts `streamlit run app.py` on `$PORT` (default 8501), address `0.0.0.0`, headless, with usage stats off.

Why a file: Google sign-in (`st.login`) reads its `[auth]` section only from `secrets.toml`. Streamlit exposes only top-level secrets as environment variables, never nested sections such as `[auth]` ([secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management), [st.login](https://docs.streamlit.io/develop/api-reference/user/st.login)). The app's own keys (`ANTHROPIC_API_KEY`, `SUPABASE_*`, ...) are read by `ui/common.py` `secret()` from `st.secrets` first and then from plain environment variables, so they are ordinary Railway variables.
`ui/account.py` `auth_configured()` turns sign-in on only when `[auth.google].client_id` ends with `.apps.googleusercontent.com`.

So the rule is: **Railway variables for keys and settings, `STREAMLIT_SECRETS_TOML` only for the `[auth]` block.**

## Before you start

- The GitHub repository of the app, with the `Dockerfile` on the branch you will deploy (normally `main`).
- A Railway account. A subscription is required (see [Cost](#cost)).
- The Supabase project from `supabase/README.md`, with `SUPABASE_URL`, `SUPABASE_KEY` and `CVMAX_DB_TOKEN`.
- A Claude API key from the Anthropic Console, with a monthly spend limit set (step 1 below). The app uses Claude as its only model provider: set `CVMAX_PROVIDER=claude`.
- Access to the Cloudflare DNS of `getcvmax.com` and to the Google Cloud project for sign-in.

## 1. Mandatory before public launch: Claude API key and a monthly spending limit

Do this before the public link goes out. The model calls are billed per use, so a fresh key that only you have seen and a monthly limit are required.

1. Open <https://console.anthropic.com> and sign in.
2. Settings, API keys, Create key. Name it `getcvmax-prod`. Copy it once into your password manager. This is the production key.
3. **Never reuse a key that was pasted anywhere** (a chat, an email, an issue, a file). Treat such a key as exposed: delete or disable it on the same page and use the fresh one.
4. Settings, Limits (or Billing, Limits): set a monthly spend limit. A reasonable start is 30 USD; raise it later on purpose. Add a payment method and a small credit if the console asks.
5. Keep the key only in Railway variables (`ANTHROPIC_API_KEY`) and GitHub Actions secrets (for the scheduled jobs). Never put it in a file, the repository, an email or a chat.
6. Remember: the monthly limit in the Console is the hard stop for the whole account, but it only acts after the money is spent up to that amount. Day-to-day spend is bounded by the per-day limits in `CVMAX_LIMITS` (step 3), which stop requests inside the app. Set both deliberately.

If the app later reports "Invalid API key" and the key starts with `sk-ant-usr`, create a standard API key on the API keys page instead.

## 2. Create the Railway project

1. Sign in at <https://railway.com>. Click New Project, then Deploy from GitHub repo.
2. If asked, install the Railway GitHub app and give it access to this repository only.
3. Pick the repository. Railway creates a service and starts a first build. Let it fail or finish, you will change settings next.
4. Open the service, then Settings. Under Source, check that the branch is the one you want to deploy (normally `main`) and that Root Directory is empty.
5. Under Build, set the Builder to Dockerfile. Railway also auto-detects a file named `Dockerfile` in the repo root (the build log says "Using detected Dockerfile!"). Leave "Dockerfile Path" empty.
6. Under Deploy, set Healthcheck Path to `/_stcore/health`. Leave the timeout at the default (300 seconds). Railway only checks during a deploy: if the path does not return 2xx in time the deploy is marked failed and the previous one stays live.
7. Leave the Start Command empty (the image already has one). Leave replicas at 1: the app keeps daily-limit counters in the database and sessions in memory, so one instance is right at launch.

## 3. Set the variables

Open the service, Variables tab. Add each variable with New Variable, or use the RAW Editor to paste several lines. Changes are staged: click Deploy when the banner appears.
Never paste a value anywhere else (chat, issues, commits).

### Required

| Variable | Value |
|---|---|
| `PORT` | `8501` (so the domain target port and the app agree) |
| `ANTHROPIC_API_KEY` | The Claude API key created in step 1. |
| `CVMAX_PROVIDER` | `claude`. Set it explicitly. With the default `auto`, if both a Gemini and a Claude key are present Gemini wins, so a leftover Gemini key would silently switch the app to Gemini. |
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_KEY` | The project's public key (`sb_publishable_...`) |
| `CVMAX_DB_TOKEN` | The site's secret token (`cvmx_...`, see `supabase/README.md`). It also keys the hashed role tag in job-search events unless `CVMAX_EVENT_SALT` is set. |
| `STREAMLIT_SECRETS_TOML` | The `[auth]` block from step 4. Multi-line: in the form field press Ctrl+Enter (Cmd+Enter on Mac) for a new line, or use the RAW Editor. Avoid the characters `${{` in it, because Railway treats them as a variable reference. |

Without the Supabase trio the site still runs, but limits are counted in memory (lost on every restart) and results are not saved. Do not launch like that.
Without `STREAMLIT_SECRETS_TOML` the site runs with no sign-in.

### Recommended

| Variable | Value |
|---|---|
| `CVMAX_LIMITS` | Daily limits, per user and for the whole site, as `kind=user:site` pairs separated by commas. Example: `analysis=5:60,career=3:40,grill=3:30,builder=2:20,build=4:30,export=5:30,feedback=10:500,jobs=10:300,match=5:60`. A side may be `none` for no limit. Kinds: `analysis`, `career`, `grill`, `builder`, `build`, `export`, `feedback`, `jobs`, `match`. Malformed or unknown entries are ignored, never fatal. **This is the real day-to-day spending cap**, so set the site-wide numbers to what your Claude budget can pay for. The defaults in `cvmax/config.py` may be larger than your budget allows, so check them. The launch-guide value is `analysis=5:100,jobs=10:300`. |
| `CVMAX_CONTACT` | Email or Telegram shown on the About, Privacy and Terms pages (for example `hello@getcvmax.com`) |
| `CVMAX_HOSTING` | `Railway`. Shown on the privacy page as the place where the app runs. |
| `CVMAX_EVENT_SALT` | A long random string. Keys the hashed role tag in job-search events (HMAC), so the tag cannot be guessed from a dictionary. Without it the app falls back to `CVMAX_DB_TOKEN`. |
| `JOOBLE_API_KEY` | Free Jooble key. Without it the Jooble job source is skipped. |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Free Adzuna keys, both are needed. Without them Adzuna is skipped. |

### Optional (defaults are fine)

| Variable | Default | What it does |
|---|---|---|
| `CVMAX_MODEL` | `claude-sonnet-5-5` | Claude model for heavy tasks (the CV review and edits) |
| `CVMAX_MODEL_LIGHT` | `claude-haiku-5-5` | Claude model for light tasks: Q&A questions, job ranking, the edit verifier and skill extraction |
| `CVMAX_FALLBACKS` | `1` | Claude: retry on a fallback model |
| `CVMAX_EFFORT_ANALYSIS` | `high` | Review depth: low, medium, high |
| `CVMAX_EFFORT_GRILL` | `low` | Depth for Grill me |
| `CVMAX_CHECKS` | `1` | `0` turns off the automatic CV checks |
| `CVMAX_VERIFY` | `1` | `0` turns off the edit verifier |
| `CVMAX_DEMO` | off | `1` forces demo mode (fake model). Never set it in production. |

Gemini variables, optional and unused when `CVMAX_PROVIDER=claude`:

| Variable | What it does |
|---|---|
| `GEMINI_API_KEY` | Gemini key. Not needed with Claude. Leave it unset in production. |
| `CVMAX_GEMINI_MODELS` | Heavy-task Gemini models (see `cvmax/config.py`) |
| `CVMAX_GEMINI_MODELS_LIGHT` | Light-task Gemini models (see `cvmax/config.py`) |
| `GOOGLE_API_KEY` | Fallback name for `GEMINI_API_KEY`. Do not set it unless you mean it. |

If both a Gemini and a Claude key are set and the provider is `auto`, Gemini wins. That is why `CVMAX_PROVIDER=claude` must be set explicitly.
Do not set any `PADDLE_*`, `CVMAX_BILLING_TOKEN` or other billing variable: the launch is free and billing code is parked.
`LEARN_AUTOMERGE`, `LEARN_PR_TOKEN` and `CVMAX_LEARN_TOKEN` belong to GitHub Actions, not to the app service. Do not put them in Railway.

## 4. The `[auth]` block (Google sign-in)

Generate a cookie secret locally: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
Create the variable `STREAMLIT_SECRETS_TOML` with this exact structure (replace the three placeholders):

```toml
[auth]
redirect_uri = "https://app.getcvmax.com/oauth2callback"
cookie_secret = "<the random string from above>"

[auth.google]
client_id = "<number>-<id>.apps.googleusercontent.com"
client_secret = "<client secret from Google>"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Notes:

- `redirect_uri` must be exactly `https://app.getcvmax.com/oauth2callback` and must match the Google client character for character.
- The app calls `st.login("google")`, so the provider section is named `[auth.google]`.
- Keep this block out of git and out of chats. Put only these sections in it. Keys and settings go in ordinary variables.
- To change anything in it, edit the variable and redeploy: the file is rewritten at every start.
- Alternative for advanced use: mount a Railway volume containing `/app/.streamlit/secrets.toml` and leave `STREAMLIT_SECRETS_TOML` unset. The entrypoint then uses the mounted file. The variable is simpler, so use it unless you have a reason.

## 5. Google Cloud OAuth client

In <https://console.cloud.google.com>, pick (or create) the project for sign-in. It has nothing to do with the model provider.

1. APIs & Services, OAuth consent screen (in newer consoles: Google Auth Platform, Branding / Audience / Data Access).
2. App name `GetCVmax`, support email and developer contact email set. App home page `https://getcvmax.com`, privacy policy `https://getcvmax.com/privacy`, terms `https://getcvmax.com/terms`. Add `getcvmax.com` as an authorised domain.
3. Scopes: only the basic ones, `openid`, `email` and `profile` (shown as `.../auth/userinfo.email` and `.../auth/userinfo.profile`). Adding anything else triggers a long verification.
4. Publishing status: set it to **In production** (button "Publish app"). In "Testing" only the listed test users can sign in and tokens expire after 7 days. With only basic scopes Google does not require a full verification review, but it may ask you to confirm the domain and links.
5. Credentials, Create credentials, OAuth client ID, type Web application, name `GetCVmax production`.
6. Authorised JavaScript origins: leave empty. Authorised redirect URIs: add `https://app.getcvmax.com/oauth2callback`. For local testing you may also add `http://localhost:8501/oauth2callback`.
7. Copy the client ID and client secret into the `[auth.google]` block above.

Login stays open to all Google accounts. The app only accepts accounts whose email Google marks as verified.

## 6. First deploy

1. Variables tab: review the staged changes and click Deploy.
2. Open the Deployments tab and watch the build (about a few minutes the first time). The deploy turns green when `/_stcore/health` answers.
3. In the deploy logs you should see `cvmax: wrote /app/.streamlit/secrets.toml from STREAMLIT_SECRETS_TOML` and then Streamlit's "You can now view your Streamlit app" lines. You should never see any key or token value. If you do, stop and rotate that secret.
4. Settings, Networking, Public Networking: click Generate Domain to get a `*.up.railway.app` address. Use it only for the first smoke test. Google sign-in will not work on it (the redirect URI does not match), everything else will.

## 7. Custom domain app.getcvmax.com via Cloudflare

1. In Railway: service, Settings, Networking, Public Networking, **+ Custom Domain**. Enter `app.getcvmax.com`. Choose the target port `8501` (the port the app listens on).
2. Railway shows two DNS records: a **CNAME** (a target like `xxxxxx.up.railway.app`) and a **TXT** record for ownership verification. Copy the Name and Value of both exactly as the dashboard shows them. Both records are required: the domain does not verify with the CNAME alone.
3. In Cloudflare: select `getcvmax.com`, DNS, Records, Add record.
   - Type `CNAME`, Name `app`, Target the value from Railway, Proxy status **DNS only** (grey cloud), TTL Auto.
   - Type `TXT`, Name and Content exactly as Railway shows (usually a `_railway-verify` name under `app`). TXT records are never proxied.
4. Back in Railway wait for the green check mark next to the domain. Propagation is usually minutes, in rare cases up to 72 hours. Railway then issues a Let's Encrypt certificate, usually within an hour.
5. Open `https://app.getcvmax.com`. The page should load over HTTPS with a valid certificate.
6. Optional second step, proxying through Cloudflare (orange cloud, for Cloudflare's caching and DDoS protection): only after step 5 works.
   - Cloudflare, SSL/TLS, Overview: set the encryption mode to **Full** (not Flexible, not Full strict; Railway's docs say Full strict does not work as intended for proxied domains). Flexible causes redirect loops.
   - DNS: switch the `app` CNAME to **Proxied** (orange cloud).
   - Re-run the smoke test below, with special attention to the CV analysis (it runs over a WebSocket and may take a minute). Cloudflare supports WebSockets on all plans, so this should work. If the app shows "Connecting" forever, a disconnect banner, or sign-in loops, switch the record back to DNS only and stay there. DNS only is fully supported and is the safe default.
   - Do not enable Cloudflare features that rewrite or cache dynamic content for `app.getcvmax.com` (Rocket Loader, Auto Minify, caching rules). Streamlit serves a single-page app.
7. Check that Google sign-in works on the custom domain (smoke test in step 8, item 3).

The landing page at `getcvmax.com` (Cloudflare Pages) is not affected: only the `app` host points to Railway.

## 8. Smoke test after every deploy

Open `https://app.getcvmax.com` in a private window, then check:

1. The home page loads, fonts and logo show (static files are served), no error banner.
2. `https://app.getcvmax.com/_stcore/health` returns `ok`.
3. Click "Sign in", complete Google sign-in, and land back on the app signed in. After the first sign-in you see the short welcome page. Sign out works.
4. Upload a short synthetic CV (never a real person's CV), run a CV review, and get a score and verdicts. Download the DOCX or PDF export.
5. Run the Where to apply page and check that job links are deep links.
6. In Supabase, the new user row and the usage counter appear. Run the same action until the per-user limit message shows, to confirm limits work.
7. Railway Metrics: memory stays reasonably flat after the test. Deploy logs show no stack traces and no secret values.

## Payments (Paddle)

Do this in the sandbox first, then repeat in live. Billing in the app switches on only when `PADDLE_API_KEY`, `PADDLE_ENV`, `PADDLE_PRICE_FULL` and `PADDLE_PRICE_HUNT` are all set. Spec: `docs/plan-paywall.md`.

1. Sandbox account: open <https://sandbox-vendors.paddle.com> and sign up. Live needs a separate account at <https://vendors.paddle.com> with business verification (the FOP details); start the verification early, it can take days.
2. Products and prices: Catalog, Products, **+ New product**.
   - Product "GetCVmax Full fix", price **15.00 USD**, one-time. Copy its price id (`pri_...`).
   - Product "GetCVmax Job hunt", price **29.00 USD**, one-time. Copy its price id.
   - After the first 100 buyers, change the prices to 25 and 49 in Paddle (edit the price or add a new price and switch the variable), and update `CVMAX_PRICES` (for example `full=25,hunt=49`) so the app labels match.
3. Default payment link: Checkout, Checkout settings, **Default payment link**: `https://getcvmax.com/pay`. Paddle asks you to approve the domain (Checkout, Website approval); submit `getcvmax.com` and wait for approval. In the sandbox it works without approval.
4. Client-side token: Developer tools, Authentication, **Client-side tokens**, New token. Open `site/pay.html` and set `data-paddle-token` on `<main id="pay">` to this token (it starts with `test_` in sandbox, `live_` in live; it is public by design) and `data-paddle-env` to `sandbox` or `live`. Commit and push; Cloudflare Pages redeploys. Never put the API key here.
5. API key: Developer tools, Authentication, **API keys**, New API key. Permissions: transactions read and write, customers read and write. Put it only in Railway (next step).
6. Railway variables on the app service: `PADDLE_API_KEY`, `PADDLE_ENV` (`sandbox` or `live`), `PADDLE_PRICE_FULL`, `PADDLE_PRICE_HUNT`, and `CVMAX_PRICES` (default `full=15,hunt=29`). Deploy.
7. Billing token in Supabase: follow the "billing" step in `supabase/README.md` (run `supabase/apply-billing.sql` in the SQL editor first). You get a token value; keep it for the next step.
8. Cloudflare Pages variables (Workers & Pages, getcvmax project, Settings, Variables and Secrets, Production, add as **Secret** for the secrets): `PADDLE_WEBHOOK_SECRET` (step 9), `PADDLE_PRICE_FULL`, `PADDLE_PRICE_HUNT` (the same ids as in Railway), `CVMAX_BILLING_TOKEN` (step 7). `SUPABASE_URL` and `SUPABASE_KEY` are already there for the waitlist. Redeploy after changing variables (Deployments, latest, Retry deployment).
9. Notification destination: Developer tools, Notifications, **New destination**. Type webhook, URL `https://getcvmax.com/api/paddle-webhook`. Subscribe to `transaction.completed`, `transaction.payment_failed`, `adjustment.created`, `adjustment.updated`. Save, open the destination, copy the **secret key** into `PADDLE_WEBHOOK_SECRET` (step 8), redeploy.
10. Test in the sandbox: sign in to the app, run a review, click Unlock, pay with Paddle's test card `4242 4242 4242 4242` (any future date, any CVC). You return to the app with `?paid=1` and the credit is spent. In Paddle, Developer tools, Notifications, Logs, the delivery should show status 200. In Supabase, `cvmax_purchases` has one row. Refund the transaction from the Paddle dashboard (Transactions, the payment, Refund) and check the row turns `refunded`.
11. Check the `/pay` page: open `https://getcvmax.com/pay` (it shows a calm message without `?_ptxn=`). If the checkout overlay does not open on a real transaction, open the browser console: a blocked resource means the Content-Security-Policy for `/pay` in `site/_headers` needs the named host added.
12. Go live: repeat steps 1 to 9 in the live account (new price ids, new client-side token, new API key, new webhook secret), set `data-paddle-env="live"`, `PADDLE_ENV=live`, and make one real purchase with a small test price or your own card, then refund it.
13. Before the first sale: fill the `FOP [Full legal name]` placeholders in `site/terms.html`, `site/refund.html` and `site/privacy.html`, and check that the refund page matches what you will really do.

## Logs

- Railway: service, Deployments tab, click a deployment, then Build Logs (image build) or Deploy Logs (what the app prints). The Observability / Logs view of the project lets you search and filter across deployments.
- The app logs exception types, not CV text. If you find CV text in a log, treat it as a bug and report it.
- The same container health can be tested from anywhere: `curl -s https://app.getcvmax.com/_stcore/health` should print `ok`.

## Roll back

1. Service, Deployments tab. Find the last good deployment (green, "Active" history).
2. Click the three dots at the end of that deployment and choose Rollback, then confirm.
3. Railway restores that deployment's image and variables. Run the smoke test.
4. Rollback is not available for deployments older than your plan's retention. If the bad deploy was caused by code, also revert the commit on GitHub, otherwise the next push redeploys the broken code.
5. To redeploy the current commit without changes: three dots on a deployment, Redeploy. To deploy the latest commit of the default branch, use the Command Palette (Cmd/Ctrl+K), "Deploy Latest Commit".

A broken `STREAMLIT_SECRETS_TOML` or a failing healthcheck never replaces a working deployment, so the live site stays up while you fix the variable.

## Cost

A few dollars to ten dollars a month at launch traffic. Check Railway's current pricing page (<https://railway.com/pricing>) for what applies today: a subscription plus usage (memory, CPU and network) is billed. The Claude bill is separate, and the per-day `CVMAX_LIMITS` bound it. Set the monthly spend limit from step 1 and add a usage limit in Railway under the workspace's Usage / billing settings.

The model prices below are estimates from the launch guide, not measurements. Check the Anthropic pricing page for the current numbers, and measure the real cost per review in the Console (Usage divided by the number of reviews).

| Model | Input | Output |
|---|---|---|
| Claude Sonnet 5.5 (heavy tasks) | 2 USD per million tokens | 10 USD per million tokens |
| Claude Haiku 5.5 (light tasks) | 0.10 USD per million tokens | 0.50 USD per million tokens |

A CV review on Sonnet is on the order of 5 to 15 US cents. With `analysis=5:100` at most 100 reviews a day, so the worst case is roughly 15 USD a day, and the monthly limit stops anything beyond that.

## Fallback: Streamlit Community Cloud

Use this if Railway is down or while you have not set up the custom domain. It has two limits: no custom domain (only a `*.streamlit.app` address) and idle apps sleep after about 12 hours (about 1 GB of memory).

1. On <https://streamlit.io/cloud>: New app, choose the repository and the file `app.py`.
2. Advanced settings, Secrets: paste the settings from `.streamlit/secrets.toml.example`. Here the `[auth]` block goes straight into Secrets, and `redirect_uri` is `https://<your-site>.streamlit.app/oauth2callback`.
3. Add that redirect URI to the Google OAuth client as well.

## Checklist before announcing

- [ ] Fresh Claude API key created (not one that was ever pasted anywhere) and a monthly spend limit set in the Anthropic Console
- [ ] `CVMAX_LIMITS` site-wide numbers match what the budget can pay for
- [ ] `PORT`, `ANTHROPIC_API_KEY`, `CVMAX_PROVIDER=claude`, `CVMAX_HOSTING`, `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` set; `CVMAX_DEMO` not set; no Gemini key set
- [ ] `STREAMLIT_SECRETS_TOML` set; Google consent screen In production
- [ ] `app.getcvmax.com` verified (green check) with a valid certificate
- [ ] Smoke test passed on the custom domain, including Google sign-in
- [ ] Privacy and Terms pages on `getcvmax.com` reachable from the consent screen
