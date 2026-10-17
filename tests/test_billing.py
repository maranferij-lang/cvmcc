"""Billing core: Paddle signatures, event parsing, plans, the Paddle client and credits in MemoryDB."""

import hashlib
import hmac
import json

import pytest

from cvmax import billing, config
from cvmax.billing import BillingError, FakePaddle, PaddleClient, parse_event, plan_for_price_id, verify_signature
from cvmax.db import MemoryDB

SECRET = "pdl_ntfset_example_secret"
NOW = 1_800_000_000


@pytest.fixture
def paddle_env(monkeypatch):
    monkeypatch.setenv("PADDLE_API_KEY", "pdl_example_key")
    monkeypatch.setenv("PADDLE_ENV", "sandbox")
    monkeypatch.setenv("PADDLE_PRICE_FULL", "pri_fakefull")
    monkeypatch.setenv("PADDLE_PRICE_HUNT", "pri_fakehunt")


def sign(body: bytes, ts: int = NOW, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()


# --- signature ---

def test_signature_known_vector():
    body = b'{"event_type":"transaction.completed"}'
    header = f"ts={NOW};h1={sign(body)}"
    assert verify_signature(SECRET, header, body, now=NOW)
    assert verify_signature(SECRET, header, body, now=NOW + 299)


def test_signature_tampered_body_or_secret():
    body = b'{"a":1}'
    header = f"ts={NOW};h1={sign(body)}"
    assert not verify_signature(SECRET, header, b'{"a":2}', now=NOW)
    assert not verify_signature("other", header, body, now=NOW)
    assert not verify_signature("", header, body, now=NOW)


def test_signature_expired_and_future_timestamp():
    body = b"{}"
    header = f"ts={NOW};h1={sign(body)}"
    assert not verify_signature(SECRET, header, body, now=NOW + 301)
    assert not verify_signature(SECRET, header, body, now=NOW - 301)
    assert verify_signature(SECRET, header, body, now=NOW + 301, tolerance=600)


def test_signature_multiple_h1_values_any_match():
    body = b"{}"
    header = f"ts={NOW};h1={'0' * 64};h1={sign(body)}"
    assert verify_signature(SECRET, header, body, now=NOW)
    assert not verify_signature(SECRET, f"ts={NOW};h1={'0' * 64};h1={'1' * 64}", body, now=NOW)


@pytest.mark.parametrize("header", [None, "", "garbage", "ts=abc;h1=00", f"h1={'a' * 64}", f"ts={NOW}", "ts=;h1="])
def test_signature_malformed_header_is_false_not_error(header):
    assert verify_signature(SECRET, header, b"{}", now=NOW) is False


def test_signature_timestamp_is_part_of_the_signed_text():
    body = b"{}"
    header = f"ts={NOW + 1};h1={sign(body, ts=NOW)}"  # signature made for another timestamp
    assert not verify_signature(SECRET, header, body, now=NOW)


# --- events and price ids ---

def completed_body(price_id="pri_fakefull", custom=None) -> bytes:
    return json.dumps({
        "event_id": "evt_01", "event_type": "transaction.completed",
        "data": {"id": "txn_01abc", "status": "completed", "currency_code": "USD",
                 "items": [{"price": {"id": price_id}, "quantity": 1}],
                 "custom_data": custom if custom is not None else {"user_key": "ann@example.com", "plan": "hunt"},
                 "details": {"totals": {"grand_total": "1500"}}}}).encode()


def test_parse_transaction_event(paddle_env):
    ev = parse_event(completed_body())
    assert (ev.event_id, ev.type, ev.transaction_id) == ("evt_01", "transaction.completed", "txn_01abc")
    assert (ev.total_cents, ev.currency, ev.price_id) == (1500, "USD", "pri_fakefull")
    assert ev.custom_data["user_key"] == "ann@example.com"


def test_parse_adjustment_event():
    body = json.dumps({"event_id": "evt_2", "event_type": "adjustment.updated",
                       "data": {"transaction_id": "txn_01abc", "action": "refund", "status": "approved",
                                "totals": {"total": "1500"}, "currency_code": "USD"}}).encode()
    ev = parse_event(body)
    assert (ev.transaction_id, ev.action, ev.status, ev.total_cents) == ("txn_01abc", "refund", "approved", 1500)


@pytest.mark.parametrize("body", [b"not json", b"[]", b'{"event_type": 5}', b"{}", b"null"])
def test_parse_event_rejects_garbage(body):
    with pytest.raises(BillingError):
        parse_event(body)


def test_price_id_mapping_ignores_custom_data_plan(paddle_env):
    # custom_data claims "hunt" but the price is the cheap "full" one: the price id wins.
    ev = parse_event(completed_body(price_id="pri_fakefull", custom={"plan": "hunt", "user_key": "x@example.com"}))
    assert ev.custom_data["plan"] == "hunt"
    assert plan_for_price_id(ev.price_id) == "full"
    assert plan_for_price_id("pri_fakehunt") == "hunt"
    assert plan_for_price_id("pri_unknown") is None
    assert plan_for_price_id("") is None and plan_for_price_id(None) is None


def test_price_mapping_needs_configured_env(monkeypatch):
    monkeypatch.delenv("PADDLE_PRICE_FULL", raising=False)
    monkeypatch.delenv("PADDLE_PRICE_HUNT", raising=False)
    assert plan_for_price_id("") is None and plan_for_price_id("pri_x") is None


def test_transaction_info_uses_price_not_custom_data(paddle_env):
    tx = json.loads(completed_body(price_id="pri_fakefull", custom={"plan": "hunt", "user_key": "A@example.com"}))["data"]
    info = billing.transaction_info(tx)
    assert (info.plan, info.status, info.user_key, info.total_cents) == ("full", "completed", "A@example.com", 1500)


# --- plans and configuration ---

def test_plans_and_prices(paddle_env, monkeypatch):
    assert set(billing.PLANS) == {"full", "hunt"}
    assert billing.PLANS["full"].credits == 1 and not billing.PLANS["full"].hunt
    assert billing.PLANS["hunt"].credits == 5 and billing.PLANS["hunt"].hunt
    assert billing.PLANS["full"].price_id == "pri_fakefull"
    assert (billing.PLANS["full"].price, billing.PLANS["hunt"].price) == (15, 29)
    assert config.parse_prices("full=25, hunt=49, bogus=1, full=x") == {"full": 25, "hunt": 49}
    assert config.parse_prices(None) == {"full": 15, "hunt": 29}


def test_billing_configured_needs_every_variable(monkeypatch):
    for name in ("PADDLE_API_KEY", "PADDLE_PRICE_FULL", "PADDLE_PRICE_HUNT"):
        monkeypatch.delenv(name, raising=False)
    assert not billing.billing_configured()
    monkeypatch.setenv("PADDLE_API_KEY", "k")
    monkeypatch.setenv("PADDLE_PRICE_FULL", "pri_a")
    assert not billing.billing_configured()
    monkeypatch.setenv("PADDLE_PRICE_HUNT", "pri_b")
    assert billing.billing_configured()
    monkeypatch.setenv("PADDLE_ENV", "nonsense")
    assert config.paddle_settings()["env"] == "sandbox"


# --- Paddle client (fake HTTP session, no network) ---

class Resp:
    def __init__(self, status, doc):
        self.status_code, self._doc = status, doc
        self.content = b"x" if doc is not None else b""

    def json(self):
        return self._doc


class Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def request(self, method, url, json=None, params=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json, "params": params,
                           "headers": headers, "timeout": timeout})
        return self.responses.pop(0)


TXN_OK = Resp(201, {"data": {"id": "txn_01abc", "checkout": {"url": "https://getcvmax.com/pay?_ptxn=txn_01abc"}}})
CTM = "ctm_01h844p3h41s12zs5mn4axja51"


def test_create_checkout_request_shape(paddle_env):
    s = Session(Resp(201, {"data": {"id": CTM}}), TXN_OK)
    link = PaddleClient("pdl_example_key", "sandbox", session=s).create_checkout("full", "ann@example.com", "ann@example.com")
    assert (link.transaction_id, link.url) == ("txn_01abc", "https://getcvmax.com/pay?_ptxn=txn_01abc")
    cust, txn = s.calls
    assert cust["url"] == "https://sandbox-api.paddle.com/customers" and cust["timeout"] == 10
    assert txn["url"] == "https://sandbox-api.paddle.com/transactions"
    assert txn["headers"]["Authorization"] == "Bearer pdl_example_key"
    assert txn["json"]["items"] == [{"price_id": "pri_fakefull", "quantity": 1}]
    assert txn["json"]["custom_data"] == {"user_key": "ann@example.com", "plan": "full", "app": "getcvmax"}
    assert txn["json"]["customer_id"] == CTM


def test_create_checkout_customer_conflict_and_failure_fall_back(paddle_env):
    conflict = Resp(409, {"error": {"code": "customer_already_exists", "detail": f"customer exists with id {CTM}"}})
    s = Session(conflict, TXN_OK)
    PaddleClient("k", "live", session=s).create_checkout("hunt", "a@example.com", "a@example.com")
    assert s.calls[0]["url"].startswith("https://api.paddle.com/") and s.calls[1]["json"]["customer_id"] == CTM
    s = Session(Resp(500, {"error": {"code": "internal"}}), TXN_OK)
    PaddleClient("k", "sandbox", session=s).create_checkout("full", "a@example.com", "a@example.com")
    assert "customer_id" not in s.calls[1]["json"]


def test_create_checkout_errors_are_safe(paddle_env, caplog):
    secret_key = "pdl_live_SECRETKEY123"
    c = PaddleClient(secret_key, "sandbox", session=Session(Resp(403, {"error": {"code": "forbidden", "detail": "x"}})))
    with pytest.raises(BillingError) as e:
        c.create_checkout("full", "a@example.com")
    assert secret_key not in str(e.value) and "a@example.com" not in caplog.text and secret_key not in caplog.text
    assert secret_key not in repr(c)
    bad_url = Resp(201, {"data": {"id": "txn_01abc", "checkout": {"url": "https://evil.example/pay"}}})
    with pytest.raises(BillingError):
        PaddleClient("k", session=Session(bad_url)).create_checkout("full", "")
    with pytest.raises(BillingError):
        PaddleClient("k", session=Session()).create_checkout("upgrade", "")


def test_get_transaction_validates_id(paddle_env):
    s = Session(Resp(200, {"data": {"id": "txn_01abc", "status": "completed"}}))
    assert PaddleClient("k", session=s).get_transaction("txn_01abc")["status"] == "completed"
    with pytest.raises(BillingError):
        PaddleClient("k", session=s).get_transaction("../customers")


# --- FakePaddle ---

def test_fake_paddle_round_trip(paddle_env):
    fake = FakePaddle()
    link = fake.create_checkout("hunt", "Ann@example.com", "")
    info = billing.transaction_info(fake.get_transaction(link.transaction_id))
    assert (info.plan, info.status, info.user_key) == ("hunt", "completed", "Ann@example.com")
    pending = FakePaddle(auto_complete=False)
    link = pending.create_checkout("full", "a@example.com")
    assert pending.get_transaction(link.transaction_id)["status"] == "ready"
    pending.complete(link.transaction_id)
    assert pending.get_transaction(link.transaction_id)["status"] == "completed"
    with pytest.raises(BillingError):
        fake.get_transaction("txn_missing")


# --- credits in MemoryDB ---

U = "ann@example.com"


def test_grant_is_idempotent_on_transaction_id():
    db = MemoryDB()
    assert db.grant_purchase(U, "full", "txn_1", 1500, "USD")["granted"] is True
    assert db.grant_purchase(U, "full", "txn_1", 1500, "USD") == {"granted": False, "reason": "duplicate"}
    assert db.credits(U)["full"] == 1
    with pytest.raises(ValueError):
        db.grant_purchase(U, "scan", "txn_2")
    with pytest.raises(ValueError):
        db.grant_purchase("", "full", "txn_2")


def test_hunt_grants_five_credits_and_hunt_flag():
    db = MemoryDB()
    assert db.credits(U) == {"full": 0, "has_hunt": False, "last_use": None, "rescan_roles": []}
    db.grant_purchase(U, "hunt", "txn_h")
    c = db.credits(U)
    assert c["full"] == 5 and c["has_hunt"] is True
    db.grant_purchase(U, "full", "txn_f")
    assert db.credits(U)["full"] == 6 and db.credits("bob@example.com")["full"] == 0


def test_credits_are_case_insensitive_on_user_key():
    db = MemoryDB()
    db.grant_purchase("Ann@Example.com", "full", "txn_1")
    assert db.credits("ann@example.com")["full"] == 1


def test_use_credit_spends_once_per_analysis_id():
    db = MemoryDB()
    db.grant_purchase(U, "hunt", "txn_h")
    first = db.use_credit(U, "a1", "Data analyst")
    assert first == {"allowed": True, "mode": "credit", "duplicate": False}
    again = db.use_credit(U, "a1", "Data analyst")
    assert again["allowed"] and again["duplicate"] is True
    assert db.credits(U)["full"] == 4
    assert db.use_credit(U, "a2", "Other role")["mode"] == "credit"
    assert db.credits(U)["full"] == 3


def test_use_credit_without_credit_is_denied():
    db = MemoryDB()
    assert db.use_credit(U, "a1", "Role") == {"allowed": False, "reason": "no_credit"}
    db.grant_purchase(U, "full", "txn_1")
    assert db.use_credit(U, "a1", "Role")["allowed"]
    assert db.use_credit(U, "a2", "Another")["allowed"] is False


def test_analysis_id_cannot_be_claimed_by_another_user():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.grant_purchase("bob@example.com", "full", "txn_2")
    db.use_credit(U, "a1", "Role")
    assert db.use_credit("bob@example.com", "a1", "Role") == {"allowed": False, "reason": "conflict"}
    assert db.credits("bob@example.com")["full"] == 1


def test_rescan_rule_same_target_role_only_once():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "a1", "Data Analyst")
    assert db.credits(U)["last_use"]["rescans_left"] == 1
    rescan = db.use_credit(U, "a2", "  data analyst ")  # same role, case and spaces ignored
    assert rescan == {"allowed": True, "mode": "rescan", "duplicate": False}
    assert db.credits(U)["last_use"]["rescans_left"] == 0
    assert db.use_credit(U, "a3", "Data Analyst")["allowed"] is False  # rescan used, no credit left
    assert db.use_credit(U, "a2", "Data Analyst")["duplicate"] is True  # replaying the rescan is free


def test_rescan_not_allowed_for_a_different_role():
    db = MemoryDB()
    db.grant_purchase(U, "hunt", "txn_h")
    db.use_credit(U, "a1", "Data Analyst")
    assert db.use_credit(U, "a2", "Product Manager")["mode"] == "credit"
    assert db.credits(U)["full"] == 3
    # the earlier Data Analyst use still has its re-scan, whatever was reviewed in between
    assert db.use_credit(U, "a3", "Data Analyst")["mode"] == "rescan"
    assert db.credits(U)["full"] == 3


def test_refund_credit_gives_it_back_and_retry_spends_again():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "a1", "Role")
    assert db.refund_credit(U, "a1")["refunded"] is True
    assert db.refund_credit(U, "a1")["refunded"] is False
    assert db.credits(U)["full"] == 1
    assert db.use_credit(U, "a1", "Role")["mode"] == "credit"  # a retry of the same analysis spends again
    assert db.credits(U)["full"] == 0


def test_refund_rescan_restores_the_rescan():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "a1", "Role")
    db.use_credit(U, "a2", "Role")
    assert db.credits(U)["last_use"]["rescans_left"] == 0
    assert db.refund_credit(U, "a2") == {"refunded": True, "mode": "rescan"}
    assert db.credits(U)["last_use"]["rescans_left"] == 1


def test_refund_purchase_removes_only_unconsumed_credits():
    db = MemoryDB()
    db.grant_purchase(U, "hunt", "txn_h")  # 5 credits
    db.use_credit(U, "a1", "R1")
    db.use_credit(U, "a2", "R2")  # 2 spent, 3 left
    out = db.refund_purchase("txn_h")
    assert out["refunded"] is True and out["removed"] == 3
    c = db.credits(U)
    assert c["full"] == 0 and c["has_hunt"] is False  # never negative
    assert db.refund_purchase("txn_h") == {"refunded": False, "reason": "already_refunded"}
    assert db.refund_purchase("txn_nope") == {"refunded": False, "reason": "unknown"}


def test_refund_purchase_leaves_other_purchases_untouched():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.grant_purchase(U, "full", "txn_2")
    assert db.refund_purchase("txn_1")["removed"] == 1
    assert db.credits(U)["full"] == 1


def test_refund_after_all_credits_spent_removes_nothing():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "a1", "R")
    assert db.refund_purchase("txn_1")["removed"] == 0
    assert db.credits(U)["full"] == 0


def test_delete_my_data_keeps_purchases_hashed_and_deletes_uses():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1", 1500, "USD")
    db.use_credit(U, "a1", "R")
    db.delete_my_data(U)
    assert db.credit_uses == []
    assert len(db.purchases) == 1
    owner = db.purchases[0]["user_key"]
    assert owner.startswith("deleted:") and U not in owner
    assert db.credits(U)["full"] == 0
    assert db.refund_purchase("txn_1")["refunded"] is True  # still refundable by transaction id


def test_billing_methods_exist_on_both_backends():
    from cvmax.db import SupabaseDB
    for name in ("grant_purchase", "refund_purchase", "credits", "use_credit", "refund_credit"):
        assert callable(getattr(SupabaseDB, name)) and callable(getattr(MemoryDB, name))


def test_supabase_billing_rpc_params():
    from cvmax.db import SupabaseDB
    sent = []

    class S:
        def post(self, url, json=None, headers=None, timeout=None):
            sent.append((url.rsplit("/", 1)[1], json))
            return Resp(200, {"allowed": True})

    db = SupabaseDB("https://x.example.co", "key", "tok", session=S())
    db.use_credit("Ann@Example.com", "a1", "  Data Analyst ")
    db.grant_purchase("Ann@Example.com", "hunt", "txn_1", 2900, "USD")
    assert sent[0] == ("cvmax_use_credit", {"p_token": "tok", "p_user_key": "ann@example.com",
                                            "p_analysis_id": "a1", "p_target_role": "data analyst",
                                            "p_purpose": "review"})
    assert sent[1][1]["p_plan"] == "hunt" and sent[1][1]["p_transaction_id"] == "txn_1"


def test_sql_files_contain_the_billing_contract():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "supabase"
    for name in ("schema.sql", "apply-billing.sql"):
        sql = (root / name).read_text()
        for fn in ("cvmax_grant_purchase", "cvmax_refund_purchase", "cvmax_credits", "cvmax_use_credit",
                   "cvmax_refund_credit"):
            assert f"function public.{fn}(" in sql
        assert "check_token(p_token, 'billing')" in sql and "pg_advisory_xact_lock" in sql
        assert "cvmax_training_samples where user_key" in sql and "'deleted:' || md5(user_key)" in sql
    assert "'billing'" in (root / "apply-billing.sql").read_text().split("create or replace function public.cvmax_delete_my_data")[1]


# --- per-purchase accounting, builder purpose, re-scans, role normalization (reviewer repro) ---

def _age(db, transaction_id, days):
    from datetime import timedelta
    next(p for p in db.purchases if p["transaction_id"] == transaction_id)["created_at"] -= timedelta(days=days)


def test_refund_after_use_does_not_swallow_the_next_purchase():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "an1", "BA")
    assert db.refund_purchase("txn_1")["removed"] == 0
    db.grant_purchase(U, "full", "txn_2")
    assert db.credits(U)["full"] == 1


def test_refund_hunt_leaves_other_purchase_credit():
    db = MemoryDB()
    db.grant_purchase(U, "hunt", "txn_h")
    db.use_credit(U, "an1", "BA")  # consumes the (older) hunt purchase
    db.grant_purchase(U, "full", "txn_f")
    r = db.refund_purchase("txn_h")
    assert r["removed"] == 4
    assert db.credits(U)["full"] == 1  # the separate full purchase is untouched
    assert db.refund_purchase("txn_h")["refunded"] is False
    assert db.credits(U)["full"] == 1


def test_uses_consume_the_oldest_purchase_first():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_new")
    db.grant_purchase(U, "full", "txn_old")
    _age(db, "txn_old", 30)
    db.use_credit(U, "a1", "R1")
    assert db.refund_purchase("txn_old")["removed"] == 0  # the old one was the one consumed
    assert db.credits(U)["full"] == 1


def test_credits_expire_after_twelve_months():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_old")
    db.grant_purchase(U, "full", "txn_new")
    _age(db, "txn_old", 370)
    assert db.credits(U)["full"] == 1
    assert db.use_credit(U, "a1", "R")["allowed"] and db.use_credit(U, "a2", "R2")["allowed"] is False
    _age(db, "txn_new", 364)
    db2 = MemoryDB()
    db2.grant_purchase(U, "full", "txn_x")
    _age(db2, "txn_x", 364)
    assert db2.credits(U)["full"] == 1


def test_builder_use_never_grants_a_rescan():
    db = MemoryDB()
    db.grant_purchase(U, "hunt", "txn_1")
    assert db.use_credit(U, "builder-x", "builder", purpose="builder")["mode"] == "credit"
    c = db.credits(U)
    assert c["full"] == 4 and c["rescan_roles"] == [] and c["last_use"] is None
    r = db.use_credit(U, "review-1", "Builder")
    assert r["mode"] == "credit" and db.credits(U)["full"] == 3
    r = db.use_credit(U, "builder-y", "Builder", purpose="builder")  # a build never spends a re-scan either
    assert r["mode"] == "credit" and db.credits(U)["rescan_roles"] == ["builder"]
    with pytest.raises(ValueError):
        db.use_credit(U, "z", "R", purpose="other")


def test_rescan_survives_a_review_of_another_role():
    db = MemoryDB()
    db.grant_purchase(U, "hunt", "txn_1")
    db.use_credit(U, "a1", "BA")
    db.use_credit(U, "a2", "Data analyst")
    r = db.use_credit(U, "a3", "BA")
    assert r["mode"] == "rescan" and db.credits(U)["full"] == 3
    assert db.use_credit(U, "a4", "BA")["mode"] == "credit"  # that re-scan is spent now


def test_rescan_not_available_after_its_purchase_is_refunded():
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "a1", "BA")
    db.refund_purchase("txn_1")
    db.grant_purchase(U, "full", "txn_2")
    assert db.use_credit(U, "a2", "BA")["mode"] == "credit"


def test_role_normalization_collapses_inner_whitespace():
    from cvmax.db import norm_role
    assert norm_role("  Data \t  ANALYST\n") == "data analyst"
    db = MemoryDB()
    db.grant_purchase(U, "full", "txn_1")
    db.use_credit(U, "a1", "Data   Analyst")
    assert db.use_credit(U, "a2", " data analyst ")["mode"] == "rescan"


def test_supabase_use_credit_sends_purpose_and_normalized_role():
    from cvmax.db import SupabaseDB
    sent = []

    class S:
        def post(self, url, json=None, headers=None, timeout=None):
            sent.append(json)
            return Resp(200, {"allowed": True})

    db = SupabaseDB("https://x.example.co", "key", "tok", session=S())
    db.use_credit("a@example.com", "b1", "builder", purpose="builder")
    db.use_credit("a@example.com", "r1", "Data   Analyst")
    assert sent[0]["p_purpose"] == "builder" and sent[1]["p_target_role"] == "data analyst"


def test_sql_per_purchase_accounting_and_purpose():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "supabase"
    for name in ("schema.sql", "apply-billing.sql"):
        sql = (root / name).read_text()
        assert "purchase_id" in sql and "p_purpose" in sql and "interval '12 months'" in sql
        assert "next_purchase" in sql and "regexp_replace" in sql
        assert "drop function if exists public.cvmax_use_credit(text, text, text, text);" in sql
        assert "cvmax_use_credit(text, text, text, text, text)" in sql


def test_checkout_custom_data_carries_analysis_id(paddle_env):
    s = Session(Resp(201, {"data": {"id": CTM}}), TXN_OK)
    PaddleClient("k", "sandbox", session=s).create_checkout("full", "ann@example.com", "ann@example.com", "abc123")
    assert s.calls[1]["json"]["custom_data"] == {"user_key": "ann@example.com", "plan": "full", "app": "getcvmax",
                                                 "analysis_id": "abc123"}
    fake = FakePaddle()
    link = fake.create_checkout("full", "ann@example.com", "", "abc123")
    assert billing.transaction_info(fake.get_transaction(link.transaction_id)).analysis_id == "abc123"
    assert billing.RETURN_TXN_RE.match(link.transaction_id)
