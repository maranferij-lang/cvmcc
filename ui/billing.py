"""Paywall UI: plan cards, checkout start, the return from Paddle, unlocking a review (docs/plan-paywall.md).

Billing is on only when the Paddle variables are set and sign-in is configured. Off: nothing here changes the app.
Rules that matter:

- A transaction fetched on return must belong to the signed-in user (`custom_data.user_key`) before anything is granted.
- Any billing or database error shows a soft message; only exception types are logged.
- Locked content is never rendered (the pages decide with `is_locked`); the free scan shows `FREE_EDITS` edits.
"""

from __future__ import annotations

import hmac
import logging

import streamlit as st

import ui.account as acct
from cvmax import billing as core
from cvmax.billing import PLANS, RETURN_TXN_RE, BillingError, FakePaddle, PaddleClient, transaction_info
from cvmax.db import norm_role
from ui.common import card, is_demo

log = logging.getLogger("cvmax")

FREE_EDITS = 3
PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
SOFT = "Payments are not available right now. Try again a bit later."
PLAN_TEXT = {
    "full": "All edits, the Q&A, the skills plan and the final CV, plus one free re-scan of the same target role.",
    "hunt": "5 full credits and the Where to apply page.",
}
NOTICES = {
    "lost": "Payment received? Open My account: your credits show up there, and you can unlock your review from it.",
    "processing": "Your payment is still processing. Refresh in a minute and your credit will appear.",
    "mismatch": "This payment belongs to another account, so nothing was added. Sign in with the account that paid.",
    "error": "We could not confirm the payment right now. Open My account in a minute: credits show up there.",
}


def billing_enabled() -> bool:
    """Paddle is configured and sign-in is configured (billing_configured() checks only the Paddle variables)."""
    return core.billing_configured() and acct.auth_configured()


def is_locked(s) -> bool:
    """True when this result must show only the free scan."""
    return billing_enabled() and s.get("tier") != "full"


def free_edits(edits: list) -> list:
    """The FREE_EDITS highest-priority edits, most important first (stable within a priority)."""
    return sorted(edits, key=lambda e: PRIORITY_RANK.get(e.priority, 3))[:FREE_EDITS]


def get_paddle():
    """FakePaddle in demo mode, the real client otherwise. Raises BillingError when payments are not configured."""
    try:
        return _paddle()
    except BillingError:
        raise
    except Exception as e:
        log.warning("paddle client failed: %s", type(e).__name__)
        raise BillingError(SOFT) from e


@st.cache_resource
def _paddle():
    if is_demo():
        return FakePaddle()
    settings = core.config.paddle_settings()
    return PaddleClient(settings["api_key"], settings["env"])


def unlocked_ids() -> set:
    return st.session_state.setdefault("unlocked_ids", set())


def credits() -> dict | None:
    """The signed-in user's credits, or None when they cannot be read (the page then shows a soft message)."""
    email = acct.current_email()
    if not email:
        return None
    try:
        return acct.get_db().credits(email)
    except Exception as e:
        log.warning("credits failed: %s", type(e).__name__)
        return None


def rescan_available(cr: dict | None, target_role: str) -> bool:
    """True when an earlier review of this user with the same normalized role still has a free re-scan."""
    return norm_role(target_role) in [norm_role(r) for r in (cr or {}).get("rescan_roles") or []]


def spend(analysis_id: str, target_role: str, purpose: str = "review") -> str:
    """Spends a credit (or the free re-scan) on this analysis. Returns "ok", "no_credit" or "error".

    purpose "builder": a CV build, it never re-scans and a review never re-scans off it."""
    email = acct.current_email()
    if not email or not analysis_id:
        return "error"
    try:
        res = acct.get_db().use_credit(email, analysis_id, target_role or "", purpose)
    except Exception as e:
        log.warning("use_credit failed: %s", type(e).__name__)
        return "error"
    if res.get("allowed"):
        if purpose == "review":
            unlocked_ids().add(analysis_id)
        return "ok"
    return "no_credit" if res.get("reason") == "no_credit" else "error"


def refund(analysis_id: str) -> None:
    """Gives a credit back after a model failure."""
    email = acct.current_email()
    if not email:
        return
    try:
        acct.get_db().refund_credit(email, analysis_id)
        unlocked_ids().discard(analysis_id)
    except Exception as e:
        log.warning("refund_credit failed: %s", type(e).__name__)


def start_checkout(plan: str, source: str, analysis_id: str = "") -> None:
    """Creates a Paddle transaction, remembers it in the session and shows the checkout link."""
    email = acct.current_email()
    if not email or plan not in PLANS:
        st.info(SOFT)
        return
    try:
        link = get_paddle().create_checkout(plan, email, email, analysis_id)
    except BillingError as e:
        log.warning("checkout failed: %s", type(e).__name__)
        st.info(str(e) or SOFT)
        return
    except Exception as e:
        log.warning("checkout failed: %s", type(e).__name__)
        st.info(SOFT)
        return
    s = st.session_state
    s["pending_txn"], s["pending_plan"], s["checkout_url"] = link.transaction_id, plan, link.url
    s["pending_analysis"] = analysis_id
    acct.log_event("checkout_started", plan=plan, source=source)


def plan_cards(source: str, analysis_id: str = "", only: tuple = ("full", "hunt")) -> None:
    """The plan cards. Clicking one creates the checkout; the link button appears right under the cards."""
    plans = [PLANS[k] for k in only]
    for col, plan in zip(st.columns(len(plans)), plans):
        with col, card(f"plan-{plan.key}-{source}"):
            st.markdown(f"**{plan.label}** · ${plan.price}")
            st.caption(PLAN_TEXT[plan.key])
            if st.button(f"Get {plan.label}", key=f"buy_{plan.key}_{source}", type="primary", width="stretch"):
                start_checkout(plan.key, source, analysis_id)
    url = st.session_state.get("checkout_url")
    if url and st.session_state.get("pending_txn"):
        st.link_button("Continue to secure checkout", url, icon=":material/lock:")
        st.caption("Payment is handled by Paddle. You come back here when it is done.")


def unlock_panel(s, n_locked: int, source: str = "analyze") -> bool:
    """The one 'Unlock the full fix' panel of a scan. Returns True when the review was unlocked in this run."""
    unlocked = False
    with card(f"unlock-{source}"):
        st.subheader("Unlock the full fix")
        if n_locked > 0:
            st.markdown(f"**{n_locked} more {'edit is' if n_locked == 1 else 'edits are'} locked.**")
        st.caption("The full fix adds every edit, the Q&A, the skills plan and the final CV (PDF and DOCX) for this review.")
        cr = credits()
        if cr is None:
            st.info("We could not check your credits right now. You can still buy a plan below.")
        has_credit = bool(cr) and int(cr.get("full") or 0) > 0
        rescan = rescan_available(cr, s.profile.target_role if s.get("profile") else "")
        # The DB is the judge: if it said no_credit although we expected a credit or re-scan, show the plans.
        show_plans = not (has_credit or rescan) or s.get("no_credit_for") == s.analysis_id
        if has_credit or rescan:
            label = "Unlock with my free re-scan" if (rescan and not has_credit) else "Unlock with 1 credit"
            if st.button(label, type="primary", key=f"unlock_btn_{source}"):
                outcome = spend(s.analysis_id, s.profile.target_role)
                if outcome == "ok":
                    unlocked = True
                elif outcome == "no_credit":
                    s["no_credit_for"] = s.analysis_id
                    show_plans = True
                    st.info("No credit left. Pick a plan below.")
                else:
                    st.info("We could not unlock the review right now. Try again in a minute.")
        if show_plans and not unlocked:
            plan_cards(source, s.analysis_id)
    return unlocked


def locked_note(what: str) -> None:
    st.info(f"{what} is part of the full fix. Use the Unlock panel above.", icon=":material/lock:")


def hunt_gate(source: str) -> None:
    """The 'Where to apply' page without the hunt plan: explanation and the Job hunt card only."""
    with card(f"hunt-gate-{source}"):
        st.subheader("Where to apply is part of Job hunt")
        st.write("Job hunt gives you 5 full credits and the Where to apply page.")
        plan_cards(source, only=("hunt",))


def show_notice() -> None:
    """A message left by the return hook (shown once, on the page the user lands on)."""
    key = st.session_state.pop("payment_notice", None)
    if key in NOTICES:
        st.info(NOTICES[key], icon=":material/receipt_long:")
        if key == "processing":
            st.button("Refresh", key="payment_refresh", icon=":material/refresh:")  # a click reruns the return hook
        st.page_link("views/account.py", label="Open My account", icon=":material/account_circle:")


def _return_txn(qp) -> str:
    """The transaction id from `?txn=` if it has the expected shape, else an empty string."""
    raw = qp.get("txn")
    return raw if isinstance(raw, str) and RETURN_TXN_RE.match(raw) else ""


def owned_analysis_role(email: str, analysis_id: str) -> str | None:
    """The target role of an analysis that belongs to this user (this session's own, or one of their saved results).

    None when the analysis is not theirs or cannot be checked: then nothing is unlocked."""
    s = st.session_state
    if not analysis_id:
        return None
    if s.get("analysis_id") == analysis_id and s.get("profile"):
        return s["profile"].target_role
    try:
        db = acct.get_db()
        for item in db.list_results(email, 50):
            if item.get("kind") != "analysis":
                continue
            payload = (db.get_result(email, item["id"]) or {}).get("payload") or {}
            if str(payload.get("analysis_id") or "") == analysis_id:
                return str(payload.get("role") or "")
    except Exception as e:
        log.warning("owned analysis check failed: %s", type(e).__name__)
    return None


def _clear_return_params(qp) -> None:
    for k in ("paid", "txn"):
        if k in qp:
            del qp[k]


def on_return() -> None:
    """Handles `?paid=1[&txn=<id>]`: verify the transaction, grant (idempotent), unlock the paid-for review.

    The transaction id comes from the URL (set by site/pay.js, so it works from any tab or browser) or, as a
    fallback, from this session's pending checkout. Only a completed transaction whose custom_data.user_key is the
    signed-in user is granted, and only an analysis that belongs to that user is unlocked."""
    s = st.session_state
    qp = st.query_params
    if qp.get("paid") != "1" or not billing_enabled() or not acct.is_logged_in():
        return
    email = acct.current_email()
    if not email:
        return
    from_url = _return_txn(qp)
    txn_id = from_url or s.get("pending_txn")
    if not txn_id:
        s["payment_notice"] = "lost"
        _clear_return_params(qp)
        return
    try:
        info = transaction_info(get_paddle().get_transaction(txn_id))
        if info.status != "completed":
            s["payment_notice"] = "processing"  # keep the query and the pending id: a refresh tries again
            return
        if info.plan is None or not info.user_key or not hmac.compare_digest(info.user_key.lower(), email.lower()):
            log.warning("paid return: transaction does not belong to the signed-in user")
            s["payment_notice"] = "mismatch"
            _clear_pending(s)
            _clear_return_params(qp)
            return
        acct.get_db().grant_purchase(email, info.plan, info.transaction_id, info.total_cents, info.currency or None)
        target = info.analysis_id if from_url else (s.get("pending_analysis") or info.analysis_id)
        _clear_pending(s)
        _clear_return_params(qp)
        role = owned_analysis_role(email, target) if target else None
        if role is not None and spend(target, role) == "ok":
            st.toast("Payment confirmed. Your review is unlocked.", icon=":material/lock_open:")
            return
        st.toast("Payment confirmed. Your credits are in My account.", icon=":material/check_circle:")
    except Exception as e:  # BillingError, DBError, anything: the page must keep working
        log.warning("paid return failed: %s", type(e).__name__)
        s["payment_notice"] = "error"
        _clear_return_params(qp)  # do not call the provider again on every rerun


def _clear_pending(s) -> None:
    for k in ("pending_txn", "pending_plan", "pending_analysis", "checkout_url"):
        s.pop(k, None)
