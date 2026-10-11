# Deploying GetCVmax to Railway (app.getcvmax.com)

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

Why a file: Google sign-in (`st.login`) reads its `[auth]` section only from `secrets.toml`. Streamlit exposes only top-level secrets as environment variables, never nested sections such as `[auth]` ([secrets management](https://docs.streamlit.io/develop/concepts/connections/secrets-management), [st.login](https://docs.streamlit.io/develop/api-reference/user/st.login)). The app's own keys (`GEMINI_API_KEY`, `SUPABASE_*`, ...) are read by `ui/common.py` `secret()` from `st.secrets` first and then from plain environment variables, so they are ordinary Railway variables.
`ui/account.py` `auth_configured()` turns sign-in on only when `[auth.google].client_id` ends with `.apps.googleusercontent.com`.

So the rule is: **Railway variables for keys and settings, `STREAMLIT_SECRETS_TOML` only for the `[auth]` block.**

## Before you start

- The GitHub repository of the app, with the `Dockerfile` on the branch you will deploy (normally `main`).
- A Railway account. A subscription is required (see [Cost](#cost)).
- The Supabase project from `supabase/README.md`, with `SUPABASE_URL`, `SUPABASE_KEY` and `CVMAX_DB_TOKEN`.
- A Gemini API key, and billing enabled on its project (step 1 below).
- Access to the Cloudflare DNS of `getcvmax.com` and to the Google Cloud project for sign-in.

## 1. Mandatory before public launch: enable billing on the Gemini API project

Do this before the public link goes out. On the unpaid (free) tier Google may use the prompts and responses to improve its products. The paid terms say it does not. The CVs people upload are personal data, so the app must run on a billed project. Terms: <https://ai.google.dev/gemini-api/terms>.

1. Open <https://aistudio.google.com/apikey> and find the project that owns the key you will use in production.
2. Next to that project click "Set up billing" and link a Cloud Billing account. Wait until the project shows the paid tier.
3. Open Google Cloud console, Billing, Budgets & alerts, Create budget. Scope it to that project, set an amount you are happy to lose in a month, and add alert thresholds (for example 50%, 90%, 100%) that email you.
4. Remember: **a budget alert does not cap spend.** It only emails you. The real cap is the per-day limits in `CVMAX_LIMITS` (step 3), which stop requests inside the app. Set them deliberately.

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
| `GEMINI_API_KEY` | Key from the billed project in step 1. If you use Claude instead, set `ANTHROPIC_API_KEY` and `CVMAX_PROVIDER=claude`. If both keys are set, Gemini is used. |
| `SUPABASE_URL` | `https://<project>.supabase.co` |
| `SUPABASE_KEY` | The project's public key (`sb_publishable_...`) |
| `CVMAX_DB_TOKEN` | The site's secret token (`cvmx_...`, see `supabase/README.md`). It also keys the hashed role tag in job-search events unless `CVMAX_EVENT_SALT` is set. |
| `STREAMLIT_SECRETS_TOML` | The `[auth]` block from step 4. Multi-line: in the form field press Ctrl+Enter (Cmd+Enter on Mac) for a new line, or use the RAW Editor. Avoid the characters `${{` in it, because Railway treats them as a variable reference. |

Without the Supabase trio the site still runs, but limits are counted in memory (lost on every restart) and results are not saved. Do not launch like that.
Without `STREAMLIT_SECRETS_TOML` the site runs with no sign-in.

### Recommended

| Variable | Value |
|---|---|
| `CVMAX_LIMITS` | Daily limits, per user and for the whole site, as `kind=user:site` pairs separated by commas. Example: `analysis=5:60,career=3:40,grill=3:30,builder=2:20,build=4:30,export=5:30,feedback=10:500,jobs=10:300,match=5:60`. A side may be `none` for no limit. Kinds: `analysis`, `career`, `grill`, `builder`, `build`, `export`, `feedback`, `jobs`, `match`. Malformed or unknown entries are ignored, never fatal. **This is the real spending cap**, so set the site-wide numbers to what your Gemini budget can pay for. The defaults in `cvmax/config.py` are sized for the free tier. |
| `CVMAX_CONTACT` | Email or Telegram shown on the About, Privacy and Terms pages (for example `cvmax.team@gmail.com`) |
| `CVMAX_EVENT_SALT` | A long random string. Keys the hashed role tag in job-search events (HMAC), so the tag cannot be guessed from a dictionary. Without it the app falls back to `CVMAX_DB_TOKEN`. |
| `JOOBLE_API_KEY` | Free Jooble key. Without it the Jooble job source is skipped. |
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Free Adzuna keys, both are needed. Without them Adzuna is skipped. |

### Optional (defaults are fine)

| Variable | Default | What it does |
|---|---|---|
| `CVMAX_PROVIDER` | `auto` | `gemini`, `claude` or `auto` |
| `CVMAX_GEMINI_MODELS` | see `cvmax/config.py` | Heavy-task Gemini models, comma-separated, in the order to try |
| `CVMAX_GEMINI_MODELS_LIGHT` | see `cvmax/config.py` | Light-task (Grill me) Gemini models |
| `CVMAX_MODEL` | `claude-opus-5-5` | Claude model, used only with Claude |
| `CVMAX_FALLBACKS` | `1` | Claude: retry on a fallback model |
| `CVMAX_EFFORT_ANALYSIS` | `high` | Review depth: low, medium, high |
| `CVMAX_EFFORT_GRILL` | `low` | Depth for Grill me |
| `CVMAX_CHECKS` | `1` | `0` turns off the automatic CV checks |
| `CVMAX_VERIFY` | `1` | `0` turns off the edit verifier |
| `CVMAX_DEMO` | off | `1` forces demo mode (fake model). Never set it in production. |
| `ANTHROPIC_API_KEY` | none | Claude key, see `GEMINI_API_KEY` |

Do not set `GOOGLE_API_KEY` unless you mean it: the app uses it as a fallback for `GEMINI_API_KEY`.
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

In <https://console.cloud.google.com>, pick (or create) the project for sign-in. It can be a different project from the Gemini one.

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

A few dollars to ten dollars a month at launch traffic. Check Railway's current pricing page (<https://railway.com/pricing>) for what applies today: a subscription plus usage (memory, CPU and network) is billed. The Gemini bill is separate and is bounded by `CVMAX_LIMITS`. Set the Cloud budget alert from step 1 and add a usage limit in Railway under the workspace's Usage / billing settings.

## Fallback: Streamlit Community Cloud

Use this if Railway is down or while you have not set up the custom domain. It has two limits: no custom domain (only a `*.streamlit.app` address) and idle apps sleep after about 12 hours (about 1 GB of memory).

1. On <https://streamlit.io/cloud>: New app, choose the repository and the file `app.py`.
2. Advanced settings, Secrets: paste the settings from `.streamlit/secrets.toml.example`. Here the `[auth]` block goes straight into Secrets, and `redirect_uri` is `https://<your-site>.streamlit.app/oauth2callback`.
3. Add that redirect URI to the Google OAuth client as well.

## Checklist before announcing

- [ ] Billing enabled on the Gemini API project and a Cloud budget alert set
- [ ] `CVMAX_LIMITS` site-wide numbers match what the budget can pay for
- [ ] `PORT`, `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` set; `CVMAX_DEMO` not set
- [ ] `STREAMLIT_SECRETS_TOML` set; Google consent screen In production
- [ ] `app.getcvmax.com` verified (green check) with a valid certificate
- [ ] Smoke test passed on the custom domain, including Google sign-in
- [ ] Privacy and Terms pages on `getcvmax.com` reachable from the consent screen
