# getcvmax.com

A static landing page for the free launch, legal pages and an email form ("Get one email when something new ships"). Hosted on Cloudflare Pages, no build step.

## Deploy
1. Cloudflare → Workers & Pages → Create → Pages → Connect to Git → repository `cvmcc`, branch `main`.
2. Build command: empty. Build output directory: `site`. Root directory: empty.
3. Settings → Variables and Secrets (type Secret): `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` (the same as in the app).
4. Custom domains → `getcvmax.com` and `www.getcvmax.com`.
5. Metrics → Web Analytics → Enable (counts views without cookies).

## Before publishing
- Replace `FOP [Full legal name]` in `terms.html`, `privacy.html`, `refund.html` with the sole proprietor (FOP, Ukrainian sole proprietorship) with a full name.
- `APP_URL` in `site.js` is the address of the app. While it is `null`, every "Try it free" button leads to the email section. When the app is open to everyone, set `APP_URL = "https://app.getcvmax.com"` (your real app address) and the same buttons open the app and pass the traffic source (`?src=`). The launch is free, so nothing here depends on payments.
- The owner reviews all legal text (`terms.html`, `privacy.html`, `refund.html`) before publishing.

## Email list
The form sends plan `full` so the database function needs no change. Form → `functions/api/waitlist.js` → RPC `cvmax_join_waitlist` (the token only on the server). The function accepts only `Content-Type: application/json` (415) and, when an `Origin` header is present, only https://getcvmax.com, https://www.getcvmax.com or its own origin (403). Test it with a stub `fetch` and fake `Request` objects under Node.
How many signed up and from where: SQL Editor → `select * from cvmax_private.waitlist_by_source;`
A link with a channel tag: `https://getcvmax.com/?ref=youtube`, `?ref=dou`, `?ref=tg-happymonday` and so on.
