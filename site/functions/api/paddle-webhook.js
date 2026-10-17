// Cloudflare Pages Function: POST /api/paddle-webhook
// Receives Paddle Billing notifications and records purchases and refunds through the
// token-checked Supabase functions cvmax_grant_purchase and cvmax_refund_purchase.
// Pages env vars (Settings → Variables, encrypted): PADDLE_WEBHOOK_SECRET, PADDLE_PRICE_FULL,
// PADDLE_PRICE_HUNT, CVMAX_BILLING_TOKEN, SUPABASE_URL, SUPABASE_KEY.
// Responses: 200 handled or ignored, 400 bad signature or body, 405 not POST,
// 500 database error or missing configuration (Paddle retries on 5xx). No secrets in any body.

const MAX_BODY = 256 * 1024;
const TOLERANCE_SECONDS = 300;

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

function hexToBytes(hex) {
  if (typeof hex !== 'string' || hex.length % 2 !== 0 || !/^[0-9a-f]*$/i.test(hex)) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(2 * i, 2 * i + 2), 16);
  return out;
}

// Constant-time comparison of two byte arrays of the same length.
function equalBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

async function hmac(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey('raw', enc.encode(secret), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, enc.encode(message)));
}

// Header: "ts=1671552777;h1=<hex>[;h1=<hex>]" (several h1 while a secret is being rotated).
export async function verifySignature(secret, header, body, nowSeconds) {
  if (!secret || !header || header.length > 2000) return false;
  let ts = null;
  const candidates = [];
  for (const part of header.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    const k = part.slice(0, i).trim();
    const v = part.slice(i + 1).trim();
    if (k === 'ts') ts = v;
    else if (k === 'h1') candidates.push(v);
  }
  if (ts === null || !/^\d{1,12}$/.test(ts) || candidates.length === 0 || candidates.length > 5) return false;
  if (Math.abs(nowSeconds - Number(ts)) > TOLERANCE_SECONDS) return false;
  const expected = await hmac(secret, `${ts}:${body}`);
  let ok = false;
  for (const c of candidates) {
    const bytes = hexToBytes(c);
    if (bytes && equalBytes(bytes, expected)) ok = true; // no early exit
  }
  return ok;
}

function planFor(item, env) {
  if (!item || typeof item !== 'object') return null;
  const id = (item.price && item.price.id) || item.price_id;
  if (typeof id !== 'string' || !id) return null;
  if (env.PADDLE_PRICE_FULL && id === env.PADDLE_PRICE_FULL) return 'full';
  if (env.PADDLE_PRICE_HUNT && id === env.PADDLE_PRICE_HUNT) return 'hunt';
  return null;
}

async function rpc(env, name, params) {
  const base = String(env.SUPABASE_URL || '').trim().replace(/\/+$/, '');
  const headers = { apikey: env.SUPABASE_KEY, 'Content-Type': 'application/json' };
  // Only the old JWT anon key goes in Authorization too (as in cvmax/db.py); new sb_ keys are not JWTs.
  if (env.SUPABASE_KEY.split('.').length === 3) headers.Authorization = `Bearer ${env.SUPABASE_KEY}`;
  try {
    const res = await fetch(`${base}/rest/v1/rpc/${name}`, { method: 'POST', headers, body: JSON.stringify(params) });
    return res.ok;
  } catch {
    return false;
  }
}

export async function onRequestPost({ request, env }) {
  const base = String(env.SUPABASE_URL || '').trim().replace(/\/+$/, '');
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(base) || !env.SUPABASE_KEY || !env.CVMAX_BILLING_TOKEN || !env.PADDLE_WEBHOOK_SECRET) {
    return json(500, { ok: false, error: 'config' });
  }

  const declared = Number(request.headers.get('Content-Length') || 0);
  if (declared > MAX_BODY) return json(400, { ok: false, error: 'too_large' });
  let raw;
  try {
    raw = new Uint8Array(await request.arrayBuffer());
  } catch {
    return json(400, { ok: false, error: 'body' });
  }
  if (raw.byteLength > MAX_BODY) return json(400, { ok: false, error: 'too_large' });
  const body = new TextDecoder().decode(raw);

  const valid = await verifySignature(env.PADDLE_WEBHOOK_SECRET, request.headers.get('Paddle-Signature'), body, Math.floor(Date.now() / 1000));
  if (!valid) return json(400, { ok: false, error: 'signature' });

  let event;
  try {
    event = JSON.parse(body);
  } catch {
    return json(400, { ok: false, error: 'body' });
  }
  if (!event || typeof event !== 'object' || !event.data || typeof event.data !== 'object') {
    return json(200, { ok: true, ignored: true });
  }
  const data = event.data;

  if (event.event_type === 'transaction.completed') {
    if (!env.PADDLE_PRICE_FULL || !env.PADDLE_PRICE_HUNT) return json(500, { ok: false, error: 'config' });
    const plan = planFor(Array.isArray(data.items) ? data.items[0] : null, env);
    const userKey = data.custom_data && typeof data.custom_data.user_key === 'string' ? data.custom_data.user_key.trim() : '';
    if (!plan || !userKey || userKey.length > 320 || typeof data.id !== 'string' || !data.id) {
      return json(200, { ok: true, ignored: true });
    }
    const total = data.details && data.details.totals ? parseInt(data.details.totals.grand_total, 10) : NaN;
    const done = await rpc(env, 'cvmax_grant_purchase', {
      p_token: env.CVMAX_BILLING_TOKEN,
      p_user_key: userKey,
      p_plan: plan,
      p_transaction_id: data.id,
      p_amount_cents: Number.isFinite(total) ? total : null,
      p_currency: typeof data.currency_code === 'string' ? data.currency_code.slice(0, 8) : null,
    });
    if (!done) return json(500, { ok: false, error: 'database' });
    return json(200, { ok: true });
  }

  if (event.event_type === 'adjustment.created' || event.event_type === 'adjustment.updated') {
    const refunds = (data.action === 'refund' || data.action === 'chargeback') && data.status === 'approved';
    if (!refunds || typeof data.transaction_id !== 'string' || !data.transaction_id) {
      return json(200, { ok: true, ignored: true });
    }
    const done = await rpc(env, 'cvmax_refund_purchase', {
      p_token: env.CVMAX_BILLING_TOKEN,
      p_transaction_id: data.transaction_id,
    });
    if (!done) return json(500, { ok: false, error: 'database' });
    return json(200, { ok: true });
  }

  return json(200, { ok: true, ignored: true });
}

export function onRequest() {
  return json(405, { ok: false });
}
