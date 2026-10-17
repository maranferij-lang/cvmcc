# Prompt for Claude Cowork: Google sign-in and Railway (launch steps 6 and 7)

Paste everything below the line into Claude Cowork on your Mac. It will work in your browser. You stay at the keyboard: you log in yourself, you type every secret yourself, and you approve anything that costs money.

---

You are helping me put my web app GetCVmax online. Work in my browser. Do two tasks in order and stop at each checkpoint for my "ok".

Hard rules:
- Never read out, copy into this chat, or store any secret (Google client secret, API keys, tokens, cookie secret). When a secret has to be entered, stop, tell me the exact field, and let me paste it myself.
- Before anything that costs money (a paid Railway plan, a card), show me the price and wait for my "yes".
- Do not change any DNS record except the one Railway asks for in task B, step 8, and show it to me before saving.
- If a screen does not match these steps, describe what you see and ask me; do not guess.

## Task A. Google sign-in (console.cloud.google.com)

1. Make sure I am logged in to the Google account I want to own the app (ask me).
2. Create a new project named `GetCVmax` and select it.
3. Open Google Auth Platform (or APIs & Services → OAuth consent screen) and set up:
   - App name: `GetCVmax`; user support email: my email; developer contact: my email.
   - Audience: External.
   - Branding: home page `https://getcvmax.com`, privacy policy `https://getcvmax.com/privacy`, terms `https://getcvmax.com/terms`, authorised domain `getcvmax.com`.
   - Data access (scopes): only `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`. Add nothing else.
4. Clients → Create client → Web application, name `GetCVmax app`. Authorised redirect URI: `https://app.getcvmax.com/oauth2callback`. Create.
5. Checkpoint: tell me the Client ID and Client secret are on screen. I copy both into my password manager myself. Do not copy them anywhere.
6. Audience → Publish app → confirm, so the status is "In production". Checkpoint: show me the status.

## Task B. Railway (railway.com)

1. Sign in with GitHub (I approve the login).
2. New Project → Deploy from GitHub repo → `maranferij-lang/cvmcc`, branch `main`. If GitHub asks for access, grant only this repository.
3. If Railway needs a plan to run the service or to use a custom domain, stop and show me the plan and price.
4. Service → Settings: health check path `/_stcore/health`; region in the EU if offered.
5. Service → Variables. Add these with the values shown (they are not secret):
   - `CVMAX_PROVIDER` = `claude`
   - `CVMAX_HOSTING` = `Railway`
   - `CVMAX_CONTACT` = `hello@getcvmax.com`
   - `CVMAX_LIMITS` = `analysis=5:100,jobs=10:300`
   - `SUPABASE_URL` = `https://xpykuwvuykvcfwyhsewx.supabase.co`
6. Checkpoint, secrets: create these variables with empty values and let me paste each value myself:
   - `ANTHROPIC_API_KEY` (my Claude API key)
   - `SUPABASE_KEY` (Supabase publishable key, starts with `sb_publishable_`)
   - `CVMAX_DB_TOKEN` (the app token, starts with `cvmx_`)
   - `STREAMLIT_SECRETS_TOML`: give me this template in a text box so I can fill in the three values in my password manager and paste the whole block:
     ```
     [auth]
     redirect_uri = "https://app.getcvmax.com/oauth2callback"
     cookie_secret = "40-OR-MORE-RANDOM-CHARACTERS"
     [auth.google]
     client_id = "GOOGLE-CLIENT-ID"
     client_secret = "GOOGLE-CLIENT-SECRET"
     server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
     ```
7. Deploy. Watch the build and deploy logs until the app is running. If it fails, show me the last error lines (they must not contain secrets; if they do, do not show them, just tell me which variable looks wrong).
8. Service → Settings → Networking → Custom domain → `app.getcvmax.com`. Railway shows a CNAME target (and maybe a TXT record). Checkpoint: show me the records. Then in dash.cloudflare.com → getcvmax.com → DNS → Records add exactly those records with Proxy status **DNS only (grey cloud)**.
9. Wait until Railway shows the domain as ready, then open `https://app.getcvmax.com` and click "Sign in with Google" with my account.

At the end, report: what was created, what is running, what I still have to do. Do not include any secret in the report.
