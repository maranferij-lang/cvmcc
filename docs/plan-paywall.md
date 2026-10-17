# Plan: paywall (free scan + paid fix) with Paddle

Owner decision 2026-10-17: free scan + paid fix. This plan adapts `docs/plan-launch-build.md` Part C (read it: the checkout flow, Paddle client, signature check, DB semantics and security rules there still apply) with these changes.

## Products

| Plan | Price now (first 100 buyers) | Later | Grants |
|---|---|---|---|
| (free scan) | free | free | Every review. Daily limits apply as today. |
| `full` | $15 | $25 | 1 full credit: unlocks one review (all its content) + 1 re-scan of the same target role |
| `hunt` | $29 | $49 | 5 full credits + the "Where to apply" page |

There is no paid `scan` and no `upgrade` plan. Prices live in Paddle; the app shows labels from `config.PRICES` (env `CVMAX_PRICES`, default `full=15,hunt=29`), and the price ids come from env `PADDLE_PRICE_FULL`, `PADDLE_PRICE_HUNT`.

## What the free scan shows

Score, summary, criterion scores, line verdicts, automatic checks, the 3 highest-priority edits. Locked (not rendered at all, so nothing reaches the browser): the remaining edits (show their count), Q&A, Skills to build, vacancy match, Final CV and exports, the CV builder; "Where to apply" needs `hunt`.

## How a review gets unlocked

- A review always runs as a scan (no credit needed). The full analysis is generated once and kept server-side; the tier decides what is rendered.
- "Unlock the full fix" on a scan: if the user has a full credit, spend it on this `analysis_id` (or a free re-scan if the previous full use was the same target role and has `rescans_left`); otherwise show the plan cards → Paddle checkout → back with `?paid=1` → verify → grant → spend on the pending `analysis_id` → render full.
- Saved results remember their tier (`tier` in the result payload); opening a saved full result does not spend again. A scan reopened later can be unlocked the same way.
- An `LLMError` in a paid follow-up step (Q&A, Final CV) does not consume anything extra; the review unlock itself never fails after payment because the content already exists.

## Billing on/off

On only when `PADDLE_API_KEY`, `PADDLE_ENV` (`sandbox` | `live`, default sandbox), `PADDLE_PRICE_FULL`, `PADDLE_PRICE_HUNT` are all set and sign-in is configured. Off: the app behaves as today (everything free, daily limits). Demo and tests: off unless a test turns it on with `MemoryDB` and `FakePaddle`.

## Webhook: a Cloudflare Pages Function, not a separate service

`site/functions/api/paddle-webhook.js` (Web Crypto HMAC-SHA256 over `ts:body`, several `h1` values allowed, 5-minute replay window, body cap 256 KB). Events: `transaction.completed` → RPC `cvmax_grant_purchase` (plan from the first item's `price_id` mapped through env `PADDLE_PRICE_FULL` / `PADDLE_PRICE_HUNT`, never from `custom_data`; `user_key` from `custom_data` accepted only after the signature verified); `adjustment.created`/`adjustment.updated` with action `refund` or `chargeback` and status `approved` → `cvmax_refund_purchase`. Returns 200 for handled/ignored, 400 bad signature, 500 on a database error so Paddle retries. Pages env: `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICE_FULL`, `PADDLE_PRICE_HUNT`, `CVMAX_BILLING_TOKEN` (+ existing `SUPABASE_URL`, `SUPABASE_KEY`).

## Database (`supabase/schema.sql` + `supabase/apply-billing.sql` for the owner)

Tables `cvmax_purchases` and `cvmax_credit_use` as in Part C but `plan in ('full','hunt')` and `kind = 'full'` only. Token scope `billing` (a new `app_config` row, created with the same SQL pattern as the learning token, documented in the file header). RPCs as in Part C: `cvmax_grant_purchase` (billing or app scope; idempotent on transaction_id; full → 1 full credit, hunt → 5 full credits + hunt flag), `cvmax_refund_purchase` (billing), `cvmax_credits` (app: `{"full": n, "has_hunt": bool, "last_use": {...}|null}`), `cvmax_use_credit(p_token, p_user_key, p_analysis_id, p_target_role)` (app; atomic with an advisory lock; idempotent per analysis_id; rescan rule), `cvmax_refund_credit` (app). `cvmax_delete_my_data` also deletes purchases' personal link? No: purchases are accounting records; keep them but replace `user_key` with a hash on delete (`'deleted:' || md5(user_key)`), and delete credit uses. Say so on the privacy page.

### Per-purchase accounting (fix after review)

- Every credit use is linked to the purchase it consumed: the oldest purchase that is not refunded, not older than 12 months and still has an unspent credit (FIFO). Balance = sum over non-refunded purchases from the last 12 months of (credits minus live credit uses linked to it). This is also how credits expire (12 months after purchase).
- Refunding a purchase removes only that purchase's unspent credits; uses linked to it stay consumed; other purchases are untouched; a second refund is a no-op.
- `cvmax_use_credit(..., p_purpose)`: `'review'` (default) or `'builder'`. A builder use gets `rescans_left` 0 and is never the parent of a re-scan. Every CV build uses one full credit (a new use id per build; the builder clears nothing it must not keep and never passes the gate on a stale flag).
- Re-scan: a review unlock is a free re-scan when any earlier review use (not builder) of this user with the same normalized role has `rescans_left > 0` and its purchase is not refunded; that use is decremented. Normalization (UI and SQL): lower-case, trim, collapse inner whitespace. `cvmax_credits` also returns `rescan_roles`. If the UI expected a re-scan and the DB answers `no_credit`, the plan cards are shown.
- Return flow: the checkout's `custom_data` carries `user_key`, `plan` and `analysis_id`. `site/pay.js` sets `successUrl` to `https://app.getcvmax.com/?paid=1&txn=<id>` using `_ptxn` from its own URL only when it matches `^txn_[a-z0-9]{10,64}$`. The app reads `txn` (same check; the session's pending id is the fallback), requires status `completed` and `custom_data.user_key` equal to the signed-in user, grants (idempotent) and unlocks `custom_data.analysis_id` only if the analysis is this session's own or one of the user's saved results. Not completed: "payment still processing, refresh in a minute" with a Refresh button. Query parameters are cleared afterwards.
- The Jobs tab stays free in the scan tier but gets no skills-to-build items while locked.

## Site and legal

- `site/pay.html` + `site/pay.js` (Paddle.js from `https://cdn.paddle.com/paddle/v2/paddle.js`, client token and environment in `data-` attributes, `successUrl` `https://app.getcvmax.com/?paid=1`), CSP in `site/_headers` widened only for that page as far as needed (Paddle domains).
- Landing `site/index.html`: replace the "free launch" pricing copy with: free scan, Full fix $15 (first 100 buyers, then $25), Job hunt $29 (then $49); FAQ about what is free and refunds; JSON-LD offers updated.
- `site/terms.html`, `site/refund.html`: payments through Paddle as merchant of record (Paddle is the reseller and handles VAT and invoices), refund: full refund within 14 days if the paid review was not used or failed; credits do not expire for 12 months. Same in the app's privacy/terms views if they exist.
- `docs/deploy.md` and `docs/launch-guide.md`: a "Payments" section with every click (Paddle sandbox account, products and prices, default payment link `https://getcvmax.com/pay` with domain approval, client-side token into `site/pay.html`, notification destination `https://getcvmax.com/api/paddle-webhook` with the four events, secrets into Railway and Cloudflare, switch to live).

## Acceptance

Full test suite green. Tests: signature vectors (Python and the JS function via node if available), price-id mapping, idempotent grant, refund, credits/rescan semantics in MemoryDB, paywall rendering (locked content absent from the rendered app, unlock spends one credit once, saved full result reopens without spending), billing off = unchanged behaviour.
