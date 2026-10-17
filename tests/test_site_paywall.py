"""Static checks for the paywall pages and the Paddle webhook Pages Function (run with node)."""
import hashlib
import hmac
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parent.parent / "site"
FUNC = SITE / "functions" / "api" / "paddle-webhook.js"
SECRET = "pdl_ntfset_test_secret"
PRICE_FULL, PRICE_HUNT = "pri_full_test", "pri_hunt_test"
ENV = {
    "SUPABASE_URL": "https://abcdefgh.supabase.co",
    "SUPABASE_KEY": "sb_publishable_test",
    "CVMAX_BILLING_TOKEN": "billing-token-test",
    "PADDLE_WEBHOOK_SECRET": SECRET,
    "PADDLE_PRICE_FULL": PRICE_FULL,
    "PADDLE_PRICE_HUNT": PRICE_HUNT,
}

RUNNER = r"""
import { onRequestPost, onRequest } from './fn.mjs';
const spec = JSON.parse(await (async () => { let s = ''; for await (const c of process.stdin) s += c; return s; })());
const calls = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url, headers: init.headers, body: JSON.parse(init.body) });
  if (spec.fetch === 'throw') throw new Error('boom');
  return new Response('{}', { status: spec.fetch === 'fail' ? 500 : 200 });
};
const headers = {};
if (spec.signature) headers['Paddle-Signature'] = spec.signature;
const request = new Request('https://getcvmax.com/api/paddle-webhook', { method: spec.method, headers, body: spec.method === 'POST' ? spec.body : undefined });
const fn = spec.method === 'POST' ? onRequestPost : onRequest;
const res = await fn({ request, env: spec.env });
console.log(JSON.stringify({ status: res.status, body: await res.json(), calls }));
"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def sign(body: str, ts: int | None = None, secret: str = SECRET) -> str:
    ts = int(time.time()) if ts is None else ts
    h1 = hmac.new(secret.encode(), f"{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def tx_event(price=PRICE_FULL, user_key="user-key-example", **over):
    data = {
        "id": "txn_01example",
        "currency_code": "USD",
        "custom_data": {"user_key": user_key, "plan": "hunt"},
        "items": [{"price": {"id": price}, "quantity": 1}],
        "details": {"totals": {"grand_total": "1500"}},
    }
    data.update(over)
    return {"event_type": "transaction.completed", "data": data}


def adjustment(action="refund", status="approved", kind="adjustment.created"):
    return {"event_type": kind, "data": {"id": "adj_1", "action": action, "status": status, "transaction_id": "txn_01example"}}


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    d = tmp_path_factory.mktemp("webhook")
    (d / "fn.mjs").write_text(FUNC.read_text())
    (d / "run.mjs").write_text(RUNNER)

    def run(event=None, *, body=None, signature="auto", method="POST", env=None, fetch="ok"):
        if body is None:
            body = json.dumps(event)
        if signature == "auto":
            signature = sign(body)
        spec = {"body": body, "signature": signature, "method": method, "env": ENV if env is None else env, "fetch": fetch}
        out = subprocess.run(["node", str(d / "run.mjs")], input=json.dumps(spec), capture_output=True, text=True, timeout=30)
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    return run


@needs_node
def test_grant_full_and_hunt(runner):
    r = runner(tx_event())
    assert r["status"] == 200 and len(r["calls"]) == 1
    call = r["calls"][0]
    assert call["url"] == "https://abcdefgh.supabase.co/rest/v1/rpc/cvmax_grant_purchase"
    assert call["headers"]["apikey"] == ENV["SUPABASE_KEY"] and "Authorization" not in call["headers"]
    assert call["body"] == {
        "p_token": "billing-token-test", "p_user_key": "user-key-example", "p_plan": "full",
        "p_transaction_id": "txn_01example", "p_amount_cents": 1500, "p_currency": "USD",
    }
    assert runner(tx_event(price=PRICE_HUNT))["calls"][0]["body"]["p_plan"] == "hunt"
    # price_id on the item works too
    ev = tx_event(items=[{"price_id": PRICE_HUNT}])
    assert runner(ev)["calls"][0]["body"]["p_plan"] == "hunt"


@needs_node
def test_jwt_key_also_sent_as_bearer(runner):
    env = dict(ENV, SUPABASE_KEY="aaa.bbb.ccc")
    r = runner(tx_event(), env=env)
    assert r["calls"][0]["headers"]["Authorization"] == "Bearer aaa.bbb.ccc"


@needs_node
def test_plan_never_from_custom_data_and_wrong_price_ignored(runner):
    r = runner(tx_event(price="pri_unknown"))
    assert r["status"] == 200 and r["calls"] == []
    # custom_data says hunt, the price says full
    assert runner(tx_event())["calls"][0]["body"]["p_plan"] == "full"
    assert runner(tx_event(user_key=""))["calls"] == []


@needs_node
def test_bad_signature_and_tampered_body(runner):
    ev = json.dumps(tx_event())
    for sig in (None, "", "garbage", "ts=1;h1=zz", sign(ev, secret="other-secret"), "ts=%d" % time.time()):
        r = runner(body=ev, signature=sig)
        assert r["status"] == 400 and r["calls"] == [], sig
    tampered = ev.replace("1500", "1")
    assert runner(body=tampered, signature=sign(ev))["status"] == 400


@needs_node
def test_replay_window(runner):
    body = json.dumps(tx_event())
    assert runner(body=body, signature=sign(body, ts=int(time.time()) - 301))["status"] == 400
    assert runner(body=body, signature=sign(body, ts=int(time.time()) + 301))["status"] == 400
    assert runner(body=body, signature=sign(body, ts=int(time.time()) - 200))["status"] == 200


@needs_node
def test_several_h1_values_any_match(runner):
    body = json.dumps(tx_event())
    good = sign(body)
    ts = good.split(";")[0]
    assert runner(body=body, signature=f"{ts};h1={'0' * 64};{good.split(';')[1]}")["status"] == 200
    assert runner(body=body, signature=f"{ts};h1={'0' * 64};h1={'1' * 64}")["status"] == 400


@needs_node
def test_refund_and_chargeback(runner):
    for action in ("refund", "chargeback"):
        for kind in ("adjustment.created", "adjustment.updated"):
            r = runner(adjustment(action, kind=kind))
            assert r["status"] == 200 and len(r["calls"]) == 1
            assert r["calls"][0]["url"].endswith("/rpc/cvmax_refund_purchase")
            assert r["calls"][0]["body"] == {"p_token": "billing-token-test", "p_transaction_id": "txn_01example"}
    assert runner(adjustment(status="pending_approval"))["calls"] == []
    assert runner(adjustment(action="credit"))["calls"] == []


@needs_node
def test_other_events_ignored(runner):
    r = runner({"event_type": "transaction.payment_failed", "data": {"id": "txn_x"}})
    assert r["status"] == 200 and r["calls"] == []


@needs_node
def test_database_error_and_config_errors_are_500(runner):
    for mode in ("fail", "throw"):
        r = runner(tx_event(), fetch=mode)
        assert r["status"] == 500 and r["body"]["error"] == "database"
        assert runner(adjustment(), fetch=mode)["status"] == 500
    for key in ENV:
        env = {k: v for k, v in ENV.items() if k != key}
        r = runner(tx_event(), env=env)
        assert r["status"] == 500, key
        assert not any(v in json.dumps(r["body"]) for v in ENV.values())


@needs_node
def test_only_post_and_body_cap(runner):
    assert runner(method="GET", body="")["status"] == 405
    big = json.dumps({"event_type": "x", "data": {"pad": "a" * (256 * 1024)}})
    r = runner(body=big)
    assert r["status"] == 400 and r["calls"] == []
    assert runner(body="not json")["status"] == 400


def test_webhook_source_has_no_secret_logging():
    src = FUNC.read_text()
    assert "console." not in src
    assert "custom_data.plan" not in src


# ---- static pages ----

def read(name):
    return (SITE / name).read_text()


def test_pay_page_and_script():
    html, js = read("pay.html"), read("pay.js")
    assert "https://cdn.paddle.com/paddle/v2/paddle.js" in html
    assert 'data-paddle-token="REPLACE_WITH_CLIENT_SIDE_TOKEN"' in html
    assert 'data-paddle-env="sandbox"' in html
    assert "<script>" not in html and "onclick" not in html  # CSP: no inline script
    assert "https://app.getcvmax.com/?paid=1" in js and "overlay" in js and "Paddle.Initialize" in js
    assert "api_key" not in html.lower() and "PADDLE_API_KEY" not in js


def test_pay_script_returns_with_a_validated_transaction_id():
    js = read("pay.js")
    assert "successUrl" in js and "'&txn=' + ptxn" in js
    assert "^txn_[a-z0-9]{10,64}$" in js and "URLSearchParams(location.search).get('_ptxn')" in js


def test_landing_and_terms_say_builder_uses_one_credit():
    assert "one full credit per build" in read("index.html")
    assert "uses one full credit" in read("terms.html")
    assert "forfeited" in read("privacy.html") and "12 months" in read("refund.html")


def test_csp_for_pay_only():
    headers = read("_headers")
    blocks = re.split(r"\n(?=\S)", headers.strip())
    star = next(b for b in blocks if b.startswith("/*"))
    assert "paddle" not in star
    pay = next(b for b in blocks if b.startswith("/pay\n"))
    assert "! Content-Security-Policy" in pay
    csp = re.search(r"^\s+Content-Security-Policy: (.*)$", pay, re.M).group(1)
    assert "script-src 'self' https://cdn.paddle.com" in csp
    assert "frame-src https://*.paddle.com" in csp
    assert "frame-ancestors 'none'" in csp and "script-src" in csp and "'unsafe-eval'" not in csp
    assert not re.search(r"script-src[^;]*'unsafe-inline'", csp)


def test_landing_pricing_copy_and_jsonld():
    html = read("index.html")
    for text in ("Free scan", "$15", "first 100 buyers", "$25", "$29", "$49", "Paddle"):
        assert text in html, text
    assert "Free while we launch" not in html
    ld = [json.loads(m) for m in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]
    app = next(d for d in ld if d["@type"] == "SoftwareApplication")
    prices = {o["name"]: o["price"] for o in app["offers"]}
    assert prices == {"Free scan": "0", "Full fix": "15", "Job hunt": "29"}
    faq = next(d for d in ld if d["@type"] == "FAQPage")
    names = [q["name"] for q in faq["mainEntity"]]
    assert {"What is free?", "How does payment work?", "Can I get a refund?"} <= set(names)


def test_legal_pages_mention_paddle_and_keep_placeholders():
    for page in ("terms.html", "refund.html", "privacy.html"):
        assert "Paddle" in read(page), page
        assert "FOP [Full legal name]" in read(page), page
    refund = read("refund.html")
    assert "14 days" in refund and "12 months" in refund and "merchant of record" in refund
    assert "unlinked" in read("privacy.html")


def test_no_em_dashes_in_changed_files():
    for name in ("pay.html", "pay.js", "index.html", "terms.html", "refund.html", "privacy.html", "llms.txt", "functions/api/paddle-webhook.js"):
        assert "—" not in read(name), name
    root = SITE.parent / "docs"
    for name in ("deploy.md", "launch-guide.md"):
        section = (root / name).read_text().split("## Payments (Paddle)")[1].split("\n## ")[0]
        assert "—" not in section
