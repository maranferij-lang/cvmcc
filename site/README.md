# getcvmax.com

A static landing page, legal pages and a waitlist form. Hosted on Cloudflare Pages, no build step.

## Deploy
1. Cloudflare → Workers & Pages → Create → Pages → Connect to Git → repository `cvmcc`, branch `main`.
2. Build command: empty. Build output directory: `site`. Root directory: empty.
3. Settings → Variables and Secrets (type Secret): `SUPABASE_URL`, `SUPABASE_KEY`, `CVMAX_DB_TOKEN` (the same as in the app).
4. Custom domains → `getcvmax.com` and `www.getcvmax.com`.
5. Metrics → Web Analytics → Enable (counts views without cookies).

## Before publishing
- Replace `FOP [Full legal name]` in `terms.html`, `privacy.html`, `refund.html` with the sole proprietor (FOP, Ukrainian sole proprietorship) with a full name.
- When payments go live: in `site.js` set `APP_URL = "https://app.getcvmax.com"`, and all price buttons lead to the app.

## Waitlist
Form → `functions/api/waitlist.js` → RPC `cvmax_join_waitlist` (the token only on the server).
How many signed up and from where: SQL Editor → `select * from cvmax_private.waitlist_by_source;`
A link with a channel tag: `https://getcvmax.com/?ref=youtube`, `?ref=dou`, `?ref=tg-happymonday` and so on.
