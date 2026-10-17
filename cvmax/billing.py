"""Paid plans and the Paddle client (docs/plan-paywall.md, docs/plan-launch-build.md Part C).

Paddle Billing is the merchant of record. This module has no Streamlit code: the plans, the REST client, the webhook
signature check and the event parser. Rules that matter:

- The plan is always derived from the `price_id` of the first item (`plan_for_price_id`), never from `custom_data`.
- `custom_data["user_key"]` is untrusted until the event signature verified, and a transaction fetched with the API
  must still be matched to the signed-in user by the caller.
- Keys, request bodies and response bodies are never logged. Errors become `BillingError` with a short safe message.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import requests

from cvmax import config

log = logging.getLogger("cvmax")

TIMEOUT_S = 10
BASE_URLS = {"live": "https://api.paddle.com", "sandbox": "https://sandbox-api.paddle.com"}
# Checkout links are followed by the browser: only our own pages and Paddle's are accepted.
CHECKOUT_HOSTS = ("getcvmax.com", "paddle.com")
MAX_BODY_BYTES = 256 * 1024

_TXN_RE = re.compile(r"^txn_[a-z0-9]{1,60}$")
RETURN_TXN_RE = re.compile(r"^txn_[a-z0-9]{10,64}$")  # a transaction id taken from the return URL
ANALYSIS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_CUSTOMER_RE = re.compile(r"ctm_[a-z0-9]{20,40}")
_SAFE_ERROR = "The payment provider is not available right now. Try again a bit later."


class BillingError(RuntimeError):
    """A payment-provider problem. The message is short and safe to show to the user."""


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    credits: int  # full credits granted
    hunt: bool  # also unlocks "Where to apply"
    price_env: str  # environment variable that holds the Paddle price id

    @property
    def price_id(self) -> str:
        return config.paddle_settings()["price_" + self.key]

    @property
    def price(self) -> int:
        """Display price in whole dollars (a label, not the amount charged)."""
        return config.PRICES.get(self.key, config.PRICE_DEFAULTS[self.key])


PLANS: dict[str, Plan] = {
    "full": Plan("full", "Full fix", credits=1, hunt=False, price_env="PADDLE_PRICE_FULL"),
    "hunt": Plan("hunt", "Job hunt", credits=5, hunt=True, price_env="PADDLE_PRICE_HUNT"),
}


def billing_configured() -> bool:
    """True when every Paddle variable is set. The caller also requires sign-in to be configured."""
    s = config.paddle_settings()
    return bool(s["api_key"] and s["price_full"] and s["price_hunt"])


def plan_for_price_id(price_id: str | None) -> str | None:
    """Maps a Paddle price id back to a plan key; unknown, empty or non-string ids give None."""
    if not isinstance(price_id, str) or not price_id:
        return None
    s = config.paddle_settings()
    for key in PLANS:
        configured = s["price_" + key]
        if configured and hmac.compare_digest(configured, price_id):
            return key
    return None


@dataclass(frozen=True)
class CheckoutLink:
    transaction_id: str
    url: str


@dataclass(frozen=True)
class PaddleEvent:
    event_id: str
    type: str
    transaction_id: str
    custom_data: dict  # untrusted until the signature verified
    total_cents: int | None
    currency: str
    action: str  # adjustments only: refund | chargeback | ...
    status: str  # transaction status or adjustment status
    price_id: str = ""  # price id of the first item (transactions only)


@dataclass(frozen=True)
class TransactionInfo:
    transaction_id: str
    status: str
    plan: str | None  # from the price id only
    user_key: str  # from custom_data: the caller must compare it with the signed-in user
    total_cents: int | None
    currency: str
    analysis_id: str = ""  # from custom_data: the review to unlock; the caller must check it belongs to the user


def verify_signature(secret: str, header: str | None, body: bytes, now: float, tolerance: int = 300) -> bool:
    """Checks the `Paddle-Signature` header: `ts=<unix>;h1=<hex>[;h1=<hex>...]`.

    HMAC-SHA256 of `ts:body` with the notification secret. Several `h1` values are allowed (secret rotation): any match
    passes. Timestamps older or newer than `tolerance` seconds fail (replay window). Never raises.
    """
    try:
        if not secret or not header or not isinstance(body, (bytes, bytearray)) or len(body) > MAX_BODY_BYTES:
            return False
        ts, signatures = None, []
        for part in header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "ts" and ts is None:
                ts = value
            elif name == "h1" and value:
                signatures.append(value.lower())
        if ts is None or not re.fullmatch(r"[0-9]{1,12}", ts) or not signatures:
            return False
        if abs(float(now) - int(ts)) > tolerance:
            return False
        expected = hmac.new(secret.encode(), ts.encode() + b":" + bytes(body), hashlib.sha256).hexdigest()
        return any(hmac.compare_digest(expected, s) for s in signatures[:8])
    except Exception:  # a malformed header must never raise into the webhook
        return False


def _cents(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 10**9 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,9}", value):
        return int(value)
    return None


def _text(value: Any, limit: int = 100) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _first_price_id(data: dict) -> str:
    items = data.get("items")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        first = items[0]
        price = first.get("price")
        pid = first.get("price_id") or (price.get("id") if isinstance(price, dict) else "")
        return _text(pid)
    return ""


def _custom_data(data: dict) -> dict:
    raw = data.get("custom_data")
    if not isinstance(raw, dict):
        return {}
    return {_text(k, 40): _text(v, 254) for k, v in list(raw.items())[:20] if isinstance(v, str)}


def parse_event(body: bytes | str) -> PaddleEvent:
    """Parses a webhook body. Raises BillingError for anything that is not a JSON object with an event type."""
    try:
        doc = json.loads(body)
    except (ValueError, TypeError) as e:
        raise BillingError("Invalid event.") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("event_type"), str):
        raise BillingError("Invalid event.")
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    etype = _text(doc["event_type"], 60)
    if etype.startswith("adjustment."):
        transaction_id = _text(data.get("transaction_id"))
        totals = data.get("totals") if isinstance(data.get("totals"), dict) else {}
        total = _cents(totals.get("total"))
    else:
        transaction_id = _text(data.get("id"))
        details = data.get("details") if isinstance(data.get("details"), dict) else {}
        totals = details.get("totals") if isinstance(details.get("totals"), dict) else {}
        total = _cents(totals.get("grand_total", totals.get("total")))
    return PaddleEvent(
        event_id=_text(doc.get("event_id")),
        type=etype,
        transaction_id=transaction_id,
        custom_data=_custom_data(data),
        total_cents=total,
        currency=_text(data.get("currency_code"), 3).upper(),
        action=_text(data.get("action"), 30),
        status=_text(data.get("status"), 30),
        price_id=_first_price_id(data),
    )


def transaction_info(tx: dict) -> TransactionInfo:
    """Reads a transaction returned by `get_transaction`. The plan comes from the price id only."""
    ev = parse_event(json.dumps({"event_type": "transaction.fetched", "data": tx if isinstance(tx, dict) else {}}))
    return TransactionInfo(ev.transaction_id, ev.status, plan_for_price_id(ev.price_id),
                           ev.custom_data.get("user_key", ""), ev.total_cents, ev.currency,
                           ev.custom_data.get("analysis_id", ""))


def _checkout_url_ok(url: Any) -> bool:
    if not isinstance(url, str) or len(url) > 2000:
        return False
    p = urlparse(url)
    host = (p.hostname or "").lower()
    return p.scheme == "https" and any(host == h or host.endswith("." + h) for h in CHECKOUT_HOSTS)


def _checkout_custom_data(plan: str, user_key: str, analysis_id: str = "") -> dict:
    """What we attach to the transaction. `analysis_id` lets the return hook unlock the right review from any tab."""
    data = {"user_key": user_key[:254], "plan": plan, "app": "getcvmax"}
    if isinstance(analysis_id, str) and ANALYSIS_ID_RE.match(analysis_id):
        data["analysis_id"] = analysis_id
    return data


class PaddleClient:
    """A small Paddle Billing REST client (create a transaction, read a transaction)."""

    def __init__(self, api_key: str, env: str = "sandbox", session: Any = None, timeout: float = TIMEOUT_S) -> None:
        if not api_key:
            raise BillingError("Payments are not configured.")
        self.base = BASE_URLS["live" if env == "live" else "sandbox"]
        self._headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        self.http = session or requests.Session()
        self.timeout = timeout

    def __repr__(self) -> str:  # never show the key
        return f"PaddleClient(base={self.base!r})"

    def _request(self, method: str, path: str, *, json_body: dict | None = None,
                 params: dict | None = None, ok_codes: tuple[int, ...] = ()) -> tuple[int, dict]:
        """Returns (status, parsed JSON). Statuses in `ok_codes` are returned instead of raised."""
        try:
            r = self.http.request(method, self.base + path, json=json_body, params=params,
                                  headers=self._headers, timeout=self.timeout)
        except requests.RequestException as e:
            log.warning("paddle %s %s: %s", method, path.split("/")[1], type(e).__name__)
            raise BillingError(_SAFE_ERROR) from e
        try:
            doc = r.json() if r.content else {}
        except ValueError:
            doc = {}
        if not isinstance(doc, dict):
            doc = {}
        if r.status_code >= 400 and r.status_code not in ok_codes:
            err = doc.get("error") if isinstance(doc.get("error"), dict) else {}
            log.warning("paddle %s %s -> %s %s", method, path.split("/")[1], r.status_code, _text(err.get("code"), 60))
            raise BillingError(_SAFE_ERROR)
        return r.status_code, doc

    def _customer_id(self, email: str) -> str | None:
        """Finds or creates a customer so the checkout is prefilled. Any failure means None (no prefill)."""
        if not email or "@" not in email or len(email) > 254:
            return None
        try:
            status, doc = self._request("POST", "/customers", json_body={"email": email}, ok_codes=(409,))
            if status == 409:
                err = doc.get("error") if isinstance(doc.get("error"), dict) else {}
                found = _CUSTOMER_RE.search(_text(err.get("detail"), 500))
                if found:
                    return found.group(0)
                _, doc = self._request("GET", "/customers", params={"email": email})
                rows = doc.get("data")
                first = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
                cid = first.get("id")
            else:
                data = doc.get("data")
                cid = data.get("id") if isinstance(data, dict) else None
            return cid if isinstance(cid, str) and _CUSTOMER_RE.fullmatch(cid) else None
        except BillingError:
            return None

    def create_checkout(self, plan: str, user_key: str, email: str = "", analysis_id: str = "") -> CheckoutLink:
        spec = PLANS.get(plan)
        if spec is None or not spec.price_id:
            raise BillingError("This plan is not available.")
        body: dict[str, Any] = {
            "items": [{"price_id": spec.price_id, "quantity": 1}],
            "custom_data": _checkout_custom_data(plan, user_key, analysis_id),
        }
        customer = self._customer_id(email)
        if customer:
            body["customer_id"] = customer
        _, doc = self._request("POST", "/transactions", json_body=body)
        data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
        checkout = data.get("checkout") if isinstance(data.get("checkout"), dict) else {}
        txn_id, url = data.get("id"), checkout.get("url")
        if not isinstance(txn_id, str) or not _TXN_RE.match(txn_id) or not _checkout_url_ok(url):
            log.warning("paddle: unexpected checkout response")
            raise BillingError(_SAFE_ERROR)
        return CheckoutLink(txn_id, url)

    def get_transaction(self, transaction_id: str) -> dict:
        if not isinstance(transaction_id, str) or not _TXN_RE.match(transaction_id):
            raise BillingError("Unknown transaction.")
        _, doc = self._request("GET", f"/transactions/{transaction_id}")
        data = doc.get("data")
        if not isinstance(data, dict):
            raise BillingError(_SAFE_ERROR)
        return data


@dataclass
class FakePaddle:
    """The same interface as PaddleClient, in memory, for tests and demo mode. No network."""

    auto_complete: bool = True  # get_transaction reports "completed" right away
    price_ids: dict = field(default_factory=lambda: {"full": "pri_fakefull", "hunt": "pri_fakehunt"})
    transactions: dict = field(default_factory=dict)
    _ids: Any = field(default_factory=lambda: itertools.count(1), repr=False)

    def create_checkout(self, plan: str, user_key: str, email: str = "", analysis_id: str = "") -> CheckoutLink:
        spec = PLANS.get(plan)
        if spec is None:
            raise BillingError("This plan is not available.")
        txn_id = f"txn_fake{next(self._ids):06d}"
        self.transactions[txn_id] = {
            "id": txn_id,
            "status": "completed" if self.auto_complete else "ready",
            "items": [{"price": {"id": self.price_ids[plan]}, "quantity": 1}],
            "custom_data": _checkout_custom_data(plan, user_key, analysis_id),
            "currency_code": "USD",
            "details": {"totals": {"grand_total": str(spec.price * 100)}},
        }
        return CheckoutLink(txn_id, f"https://getcvmax.com/pay?_ptxn={txn_id}")

    def complete(self, transaction_id: str) -> None:
        self.transactions[transaction_id]["status"] = "completed"

    def get_transaction(self, transaction_id: str) -> dict:
        tx = self.transactions.get(transaction_id)
        if tx is None:
            raise BillingError("Unknown transaction.")
        return json.loads(json.dumps(tx))
