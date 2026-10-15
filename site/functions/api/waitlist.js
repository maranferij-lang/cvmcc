// Cloudflare Pages Function: POST /api/waitlist
// Saves an email to the launch list through the token-checked Supabase function.
// Env vars (Pages → Settings → Variables, encrypted): SUPABASE_URL, SUPABASE_KEY, CVMAX_DB_TOKEN.

const PLANS = new Set(['scan', 'full', 'hunt']);
const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;

const ALLOWED_ORIGINS = new Set(['https://getcvmax.com', 'https://www.getcvmax.com']);

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

export async function onRequestPost({ request, env }) {
  // Only a JSON body (a cross-site form or text/plain post would skip the CORS preflight).
  const type = (request.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
  if (type !== 'application/json') return json(415, { ok: false });

  // A browser sends Origin on cross-site posts: accept our own site only.
  const origin = request.headers.get('Origin');
  if (origin !== null) {
    let self = '';
    try { self = new URL(request.url).origin; } catch { /* keep empty */ }
    if (!ALLOWED_ORIGINS.has(origin) && origin !== self) return json(403, { ok: false });
  }

  let data;
  try {
    data = await request.json();
  } catch {
    return json(400, { ok: false });
  }
  if (data === null || typeof data !== 'object' || Array.isArray(data)) return json(400, { ok: false });
  // Bots fill the hidden field; pretend it worked.
  if (data.company) return json(200, { ok: true });

  const email = String(data.email || '').trim().toLowerCase();
  if (email.length > 254 || !EMAIL.test(email)) return json(400, { ok: false, error: 'email' });
  const plan = PLANS.has(data.plan) ? data.plan : 'full';
  const source = String(data.source || '').toLowerCase().replace(/[^a-z0-9_.-]/g, '').slice(0, 60) || null;

  // A missing or malformed setting is a deploy mistake: say so (no values) instead of a bare 502.
  const base = String(env.SUPABASE_URL || '').trim().replace(/\/+$/, '');
  if (!/^https:\/\/[a-z0-9-]+\.supabase\.co$/.test(base) || !env.SUPABASE_KEY || !env.CVMAX_DB_TOKEN) {
    return json(500, { ok: false, error: 'config' });
  }
  const headers = { apikey: env.SUPABASE_KEY, 'Content-Type': 'application/json' };
  // Only the old JWT anon key goes in Authorization too (as in cvmax/db.py); new sb_ keys are not JWTs.
  if (env.SUPABASE_KEY.split('.').length === 3) headers.Authorization = `Bearer ${env.SUPABASE_KEY}`;

  let res;
  try {
    res = await fetch(`${base}/rest/v1/rpc/cvmax_join_waitlist`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        p_token: env.CVMAX_DB_TOKEN,
        p_email: email,
        p_plan: plan,
        p_source: source,
        p_country: request.cf && request.cf.country ? String(request.cf.country).slice(0, 2) : null,
      }),
    });
  } catch {
    return json(502, { ok: false });
  }
  if (!res.ok) return json(502, { ok: false });
  return json(200, { ok: true });
}

export function onRequest() {
  return json(405, { ok: false });
}
