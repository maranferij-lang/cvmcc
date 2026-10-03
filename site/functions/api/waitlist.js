// Cloudflare Pages Function: POST /api/waitlist
// Saves an email to the launch list through the token-checked Supabase function.
// Env vars (Pages → Settings → Variables, encrypted): SUPABASE_URL, SUPABASE_KEY, CVMAX_DB_TOKEN.

const PLANS = new Set(['scan', 'full', 'hunt']);
const EMAIL = /^[^\s@]{1,64}@[^\s@]{1,190}\.[^\s@]{2,}$/;

function json(status, body) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

export async function onRequestPost({ request, env }) {
  let data;
  try {
    data = await request.json();
  } catch {
    return json(400, { ok: false });
  }
  // Bots fill the hidden field; pretend it worked.
  if (data.company) return json(200, { ok: true });

  const email = String(data.email || '').trim().toLowerCase();
  if (email.length > 254 || !EMAIL.test(email)) return json(400, { ok: false, error: 'email' });
  const plan = PLANS.has(data.plan) ? data.plan : 'full';
  const source = String(data.source || '').toLowerCase().replace(/[^a-z0-9_.-]/g, '').slice(0, 60) || null;

  const res = await fetch(`${env.SUPABASE_URL}/rest/v1/rpc/cvmax_join_waitlist`, {
    method: 'POST',
    headers: {
      apikey: env.SUPABASE_KEY,
      Authorization: `Bearer ${env.SUPABASE_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      p_token: env.CVMAX_DB_TOKEN,
      p_email: email,
      p_plan: plan,
      p_source: source,
      p_country: request.cf && request.cf.country ? String(request.cf.country).slice(0, 2) : null,
    }),
  });
  if (!res.ok) return json(502, { ok: false });
  return json(200, { ok: true });
}

export function onRequest() {
  return json(405, { ok: false });
}
