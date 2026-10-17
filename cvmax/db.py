"""Supabase database: users, daily limits, saved results.

The site has no direct access to the tables. It calls a few functions in the database (see supabase/schema.sql),
and each one checks the secret token CVMAX_DB_TOKEN. If the database is not configured, MemoryDB is used:
limits are counted in the server memory, and results are not saved.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("cvmax")


class DBError(RuntimeError):
    pass


MAX_KIND_LEN = 40
MAX_PAYLOAD_LEN = 4000
# Event kinds that go into the export for reflection (the same as in SQL).
EXPORT_KINDS = ("analysis_done", "analysis_rated", "edits_decided", "grill_turn",
                "outcome", "rescan", "export", "jobs_shown", "job_applied")


def _check_event(kind: str, payload: dict) -> None:
    """Check before the network: the same bounds as in cvmax_log_event."""
    if not kind or len(kind) > MAX_KIND_LEN:
        raise ValueError(f"event kind must be 1..{MAX_KIND_LEN} chars")
    if len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_PAYLOAD_LEN:
        raise ValueError(f"event payload is over {MAX_PAYLOAD_LEN} chars")


MAX_SAMPLE_CV_CHARS = 40_000
MAX_SAMPLE_ANALYSIS_CHARS = 300_000
MAX_SAMPLE_META_CHARS = 4_000
MAX_JUDGE_CHARS = 100_000
JUDGE_BATCH_MAX = 50
JUDGED_MAX = 200
SAMPLE_RETENTION_DAYS = 365


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _check_sample(consent_version: str, meta: dict, cv_text: str, analysis: dict) -> None:
    """Check before the network: the same bounds as in cvmax_save_training_sample."""
    if not consent_version or len(consent_version) > 40:
        raise ValueError("consent version must be 1..40 chars")
    if not cv_text or not cv_text.strip() or len(cv_text) > MAX_SAMPLE_CV_CHARS:
        raise ValueError(f"cv text must be 1..{MAX_SAMPLE_CV_CHARS} chars")
    if not isinstance(meta, dict) or _json_len(meta) > MAX_SAMPLE_META_CHARS:
        raise ValueError("meta must be an object under 4000 chars")
    if _json_len(analysis) > MAX_SAMPLE_ANALYSIS_CHARS:
        raise ValueError(f"analysis is over {MAX_SAMPLE_ANALYSIS_CHARS} chars")


# Billing (docs/plan-paywall.md): credits granted per plan, the same as in cvmax_grant_purchase.
PLAN_CREDITS = {"full": 1, "hunt": 5}
MAX_ID_LEN = 100


def norm_role(role: str | None) -> str:
    """Lower-case, trimmed, inner whitespace collapsed, at most 200 chars. The SQL functions do the same."""
    return " ".join((role or "").lower().split())[:200]


PURPOSES = ("review", "builder")
CREDIT_MONTHS = 12


def _expiry_cutoff(now: datetime | None = None) -> datetime:
    """Purchases created before this moment have expired (12 months, like `now() - interval '12 months'`)."""
    now = now or datetime.now(timezone.utc)
    day = now.day
    while True:
        try:
            return now.replace(year=now.year - 1, day=day)
        except ValueError:  # 29 February
            day -= 1


def _check_billing_ids(**ids: str) -> None:
    for name, value in ids.items():
        if not isinstance(value, str) or not value.strip() or len(value) > (254 if name == "user_key" else MAX_ID_LEN):
            raise ValueError(f"bad {name}")


def _pacific_day() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


class SupabaseDB:
    persistent = True

    def __init__(self, url: str, key: str, token: str, session: Any = None, timeout: float = 10) -> None:
        self.base = url.rstrip("/") + "/rest/v1/rpc/"
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.count(".") == 2:  # the old JWT anon key is also sent as a Bearer
            self.headers["Authorization"] = f"Bearer {key}"
        self.token = token
        self.http = session or requests.Session()
        self.timeout = timeout

    def _rpc(self, name: str, **params: Any) -> Any:
        try:
            r = self.http.post(self.base + name, json={"p_token": self.token, **params},
                               headers=self.headers, timeout=self.timeout)
        except requests.RequestException as e:
            log.warning("supabase %s: %s", name, type(e).__name__)
            raise DBError("Cannot reach the database. Try again a bit later.") from e
        if r.status_code >= 400:
            # Details go only to the server log: the user does not need them and they could reveal the database structure.
            log.warning("supabase %s -> %s: %s", name, r.status_code, r.text[:200])
            raise DBError("The database is temporarily unavailable. Try again a bit later.")
        return r.json() if r.content else None

    def touch_user(self, email: str, name: str | None) -> dict:
        return self._rpc("cvmax_touch_user", p_email=email, p_name=name)

    def save_profile(self, email: str, program: str, status: str, background: str, goal: str) -> None:
        self._rpc("cvmax_save_profile", p_email=email, p_program=program, p_status=status,
                  p_background=background, p_goal=goal)

    def consume(self, user_key: str, kind: str, user_limit: int | None, global_limit: int | None) -> dict:
        return self._rpc("cvmax_consume", p_user_key=user_key, p_kind=kind,
                         p_user_limit=user_limit, p_global_limit=global_limit)

    def save_result(self, email: str, kind: str, title: str, payload: dict) -> str:
        return self._rpc("cvmax_save_result", p_email=email, p_kind=kind, p_title=title, p_payload=payload)

    def list_results(self, email: str, limit: int = 50) -> list[dict]:
        return self._rpc("cvmax_list_results", p_email=email, p_limit=limit) or []

    def get_result(self, email: str, result_id: str) -> dict | None:
        return self._rpc("cvmax_get_result", p_email=email, p_id=result_id)

    def delete_my_data(self, email: str) -> None:
        self._rpc("cvmax_delete_my_data", p_email=email)

    def save_feedback(self, page: str, rating: int | None, message: str) -> None:
        """Anonymous feedback: no email, only the page, a rating and text."""
        self._rpc("cvmax_save_feedback", p_page=page, p_rating=rating, p_message=message)

    def join_waitlist(self, email: str, plan: str, source: str | None) -> None:
        """Waitlist: email, the chosen plan and where the person came from."""
        self._rpc("cvmax_join_waitlist", p_email=email, p_plan=plan, p_source=source, p_country=None)

    def log_edit_feedback(self, user_key: str, analysis_id: str, target_role: str, program: str,
                          items: list[dict]) -> int:
        """Which edits the user accepted and which not. items: source, section, priority, before, after, accepted."""
        return self._rpc("cvmax_log_edit_feedback", p_user_key=user_key, p_analysis_id=analysis_id,
                         p_target_role=target_role, p_program=program, p_items=items) or 0

    def log_event(self, user_key: str, kind: str, payload: dict) -> None:
        """Learning event: only ids, scores and decisions, no CV text."""
        _check_event(kind, payload)
        self._rpc("cvmax_log_event", p_user_key=user_key, p_kind=kind, p_payload=payload)

    def variant_stats(self, task: str, days: int = 90) -> list[dict]:
        """[{variant, program, successes, failures}] for the bandit."""
        return self._rpc("cvmax_variant_stats", p_task=task, p_days=days) or []

    def learning_export(self, days: int = 30) -> dict:
        """Anonymous data for the weekly reflection: {edit_feedback, feedback, events, breadth}.

        breadth: {program: {"users": int, "analyses": int}} (always {} in MemoryDB).
        """
        return self._rpc("cvmax_learning_export", p_days=days) or {"edit_feedback": [], "feedback": [], "events": []}

    def save_training_sample(self, email: str, consent_version: str, meta: dict, cv_text: str,
                             analysis: dict) -> str:
        """An anonymised copy of a CV and its review, saved only with the user's opt-in. Returns the sample id."""
        _check_sample(consent_version, meta, cv_text, analysis)
        return str(self._rpc("cvmax_save_training_sample", p_email=email, p_consent_version=consent_version,
                             p_meta=meta, p_cv_text=cv_text, p_analysis=analysis) or "")

    def training_samples_for_judge(self, days: int = 7, limit: int = 20) -> list[dict]:
        """Unjudged samples of the last `days`, newest first: [{id, meta, cv_text, analysis}]. Learning token."""
        return self._rpc("cvmax_training_samples_for_judge", p_days=days, p_limit=limit) or []

    def save_judgement(self, sample_id: str, judge: dict) -> None:
        if _json_len(judge) > MAX_JUDGE_CHARS:
            raise ValueError(f"judgement is over {MAX_JUDGE_CHARS} chars")
        self._rpc("cvmax_save_judgement", p_id=sample_id, p_judge=judge)

    def judged_samples(self, days: int = 90, limit: int = 200) -> list[dict]:
        """Judged samples: [{id, meta, cv_text, analysis, judge}]. Learning token."""
        return self._rpc("cvmax_judged_samples", p_days=days, p_limit=limit) or []

    def purge_training_samples(self) -> int:
        """Deletes samples older than 12 months; returns how many."""
        return self._rpc("cvmax_purge_training_samples") or 0

    def upsert_vacancies(self, items: list[dict]) -> int:
        return self._rpc("cvmax_upsert_vacancies", p_items=items) or 0

    def recent_vacancies(self, role_family: str | None, region: str | None, days: int = 30,
                         limit: int = 300) -> list[dict]:
        return self._rpc("cvmax_recent_vacancies", p_role_family=role_family, p_region=region,
                         p_days=days, p_limit=limit) or []

    def save_skill_demand(self, items: list[dict]) -> int:
        return self._rpc("cvmax_save_skill_demand", p_items=items) or 0

    def skill_demand(self, program: str, region: str) -> list[dict]:
        return self._rpc("cvmax_skill_demand", p_program=program, p_region=region) or []

    # --- Billing (docs/plan-paywall.md). grant/refund_purchase also work with the 'billing' scope token. ---

    def grant_purchase(self, user_key: str, plan: str, transaction_id: str, amount_cents: int | None = None,
                       currency: str | None = None) -> dict:
        """Idempotent on transaction_id. {"granted": True, ...} or {"granted": False, "reason": "duplicate"}."""
        if plan not in PLAN_CREDITS:
            raise ValueError("bad plan")
        _check_billing_ids(user_key=user_key, transaction_id=transaction_id)
        return self._rpc("cvmax_grant_purchase", p_user_key=user_key.lower(), p_plan=plan,
                         p_transaction_id=transaction_id, p_amount_cents=amount_cents, p_currency=currency) or {}

    def refund_purchase(self, transaction_id: str) -> dict:
        """Marks the purchase refunded and removes its unconsumed credits: {"refunded": bool, "removed": n}."""
        _check_billing_ids(transaction_id=transaction_id)
        return self._rpc("cvmax_refund_purchase", p_transaction_id=transaction_id) or {}

    def credits(self, user_key: str) -> dict:
        """{"full": n, "has_hunt": bool, "last_use": {...} | None, "rescan_roles": [normalized roles]}."""
        _check_billing_ids(user_key=user_key)
        return self._rpc("cvmax_credits", p_user_key=user_key.lower()) or {
            "full": 0, "has_hunt": False, "last_use": None, "rescan_roles": []}

    def use_credit(self, user_key: str, analysis_id: str, target_role: str, purpose: str = "review") -> dict:
        """Spends one credit on this analysis (once per analysis_id), or a free re-scan of the same target role.

        purpose: "review" (a CV review, may be re-scanned once) or "builder" (a CV build: never re-scans).
        {"allowed": True, "mode": "credit" | "rescan", "duplicate": bool} or {"allowed": False, "reason": ...}.
        """
        if purpose not in PURPOSES:
            raise ValueError("bad purpose")
        _check_billing_ids(user_key=user_key, analysis_id=analysis_id)
        return self._rpc("cvmax_use_credit", p_user_key=user_key.lower(), p_analysis_id=analysis_id,
                         p_target_role=norm_role(target_role), p_purpose=purpose) or {"allowed": False, "reason": "error"}

    def refund_credit(self, user_key: str, analysis_id: str) -> dict:
        """Gives the credit (or the re-scan) back after a model failure: {"refunded": bool}."""
        _check_billing_ids(user_key=user_key, analysis_id=analysis_id)
        return self._rpc("cvmax_refund_credit", p_user_key=user_key.lower(), p_analysis_id=analysis_id) or {}


class MemoryDB:
    """A fallback without Supabase: limits in process memory, nothing is stored long-term."""

    persistent = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[tuple[str, str, str], int] = defaultdict(int)
        self._users: dict[str, dict] = {}
        self.feedback: list[dict] = []
        self.waitlist: dict[str, dict] = {}
        self.events: list[dict] = []
        self.training_samples: list[dict] = []
        self.vacancies: dict[tuple[str, str, str], dict] = {}
        self.skill_demand_rows: dict[tuple[str, str], list[dict]] = {}
        self.purchases: list[dict] = []
        self.credit_uses: list[dict] = []

    def touch_user(self, email: str, name: str | None) -> dict:
        return self._users.setdefault(email.lower(), {"email": email.lower(), "name": name, "onboarded_at": None})

    def save_profile(self, email: str, program: str, status: str, background: str, goal: str) -> None:
        user = self.touch_user(email, None)
        user.update(program=program, status=status, background=background, goal=goal, onboarded_at="now")

    def consume(self, user_key: str, kind: str, user_limit: int | None, global_limit: int | None) -> dict:
        day = _pacific_day()
        with self._lock:
            used = self._usage[(day, kind, user_key)]
            total = sum(v for (d, k, _), v in self._usage.items() if d == day and k == kind)
            if user_limit is not None and used >= user_limit:
                return {"allowed": False, "reason": "user", "used": used, "limit": user_limit}
            if global_limit is not None and total >= global_limit:
                return {"allowed": False, "reason": "global", "used": total, "limit": global_limit}
            self._usage[(day, kind, user_key)] += 1
            return {"allowed": True, "reason": None, "used": used + 1, "limit": user_limit}

    def save_result(self, email: str, kind: str, title: str, payload: dict) -> str:
        return ""

    def list_results(self, email: str, limit: int = 50) -> list[dict]:
        return []

    def get_result(self, email: str, result_id: str) -> dict | None:
        return None

    def delete_my_data(self, email: str) -> None:
        self._users.pop(email.lower(), None)
        key = email.lower()
        with self._lock:
            self.events = [e for e in self.events if e["user_key"] != key]
            self.training_samples = [x for x in self.training_samples if x["user_key"] != key]
            # Credit uses are deleted. Purchases are accounting records: kept, with the owner replaced by a hash.
            self.credit_uses = [u for u in self.credit_uses if u["user_key"] != key]
            for p in self.purchases:
                if p["user_key"] == key:
                    p["user_key"] = "deleted:" + hashlib.md5(key.encode()).hexdigest()  # noqa: S324 (not a secret)

    def log_edit_feedback(self, user_key: str, analysis_id: str, target_role: str, program: str,
                          items: list[dict]) -> int:
        return 0

    def save_feedback(self, page: str, rating: int | None, message: str) -> None:
        self.feedback.append({"page": page, "rating": rating, "message": message})

    def join_waitlist(self, email: str, plan: str, source: str | None) -> None:
        self.waitlist[email.lower()] = {"plan": plan, "source": source}

    def log_event(self, user_key: str, kind: str, payload: dict) -> None:
        _check_event(kind, payload)
        with self._lock:
            self.events.append({"user_key": user_key, "kind": kind, "payload": dict(payload),
                                "created_at": datetime.now(timezone.utc)})

    def _events_since(self, days: int) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
        with self._lock:
            return [e for e in self.events if e["created_at"] >= since]

    def variant_stats(self, task: str, days: int = 90) -> list[dict]:
        """The same reward logic as cvmax_variant_stats in SQL."""
        events = self._events_since(days)
        with self._lock:
            all_events = list(self.events)
        done: dict[str, dict] = {}
        for e in events:  # the last analysis_done per analysis_id
            p = e["payload"]
            aid, variant = p.get("analysis_id"), p.get("variant")
            if e["kind"] == "analysis_done" and aid and variant and (p.get("task") or "analysis") == task:
                done[str(aid)] = {"variant": str(variant), "program": str(p.get("program") or "")}
        rated: dict[str, int | None] = {}
        edits: dict[str, tuple[int | None, int | None]] = {}
        outcome: dict[str, object] = {}  # the last rating per analysis_id
        for e in all_events:  # events come in insertion order, so the last value wins
            p = e["payload"]
            aid = p.get("analysis_id")
            if not aid:
                continue
            aid = str(aid)
            if e["kind"] == "analysis_rated":
                r = p.get("rating")  # only a real int 0/1: bool and float do not count (like the text comparison in SQL)
                rated[aid] = r if type(r) is int and r in (0, 1) else None
            elif e["kind"] == "edits_decided":
                edits[aid] = (_as_count(p.get("accepted")), _as_count(p.get("total")))
            elif e["kind"] == "outcome":
                outcome[aid] = p.get("answer")
        groups: dict[tuple[str, str], list[int]] = {}
        for aid, d in done.items():
            rating = rated.get(aid)
            accepted, total = edits.get(aid, (None, None))
            enough = accepted is not None and total is not None and total >= 2
            success = rating == 1 or (rating is None and enough and accepted * 2 >= total)
            failure = rating == 0 or (rating is None and enough and accepted * 2 < total)
            wins = int(success) + (2 if outcome.get(aid) == "yes" else 0)
            if wins + int(failure) == 0:
                continue
            g = groups.setdefault((d["variant"], d["program"]), [0, 0])
            g[0] += wins
            g[1] += int(failure)
        return [{"variant": v, "program": p, "successes": s, "failures": f}
                for (v, p), (s, f) in sorted(groups.items())]

    def learning_export(self, days: int = 30) -> dict:
        events = [e for e in self._events_since(days) if e["kind"] in EXPORT_KINDS]
        events.sort(key=lambda e: e["created_at"], reverse=True)
        return {
            "edit_feedback": [],
            "breadth": {},
            "feedback": [{"page": f.get("page"), "rating": f.get("rating"),
                          "created_at": f.get("created_at")} for f in self.feedback[-500:][::-1]],
            "events": [{"kind": e["kind"], "payload": dict(e["payload"]), "created_at": e["created_at"].isoformat()}
                       for e in events[:5000]],
        }

    def save_training_sample(self, email: str, consent_version: str, meta: dict, cv_text: str,
                             analysis: dict) -> str:
        _check_sample(consent_version, meta, cv_text, analysis)
        if not email or len(email) > 254:
            raise ValueError("bad email")
        sample_id = str(uuid.uuid4())
        row = {"id": sample_id, "created_at": datetime.now(timezone.utc), "user_key": email.lower(),
               "consent_version": consent_version, "meta": copy.deepcopy(meta), "cv_text": cv_text,
               "analysis": copy.deepcopy(analysis), "judged_at": None, "judge": None}
        with self._lock:
            self.training_samples.append(row)
        return sample_id

    def training_samples_for_judge(self, days: int = 7, limit: int = 20) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
        with self._lock:
            rows = [x for x in self.training_samples if x["judged_at"] is None and x["created_at"] >= since]
        rows.sort(key=lambda x: x["created_at"], reverse=True)
        return [{k: copy.deepcopy(x[k]) for k in ("id", "meta", "cv_text", "analysis")}
                for x in rows[:max(min(limit, JUDGE_BATCH_MAX), 1)]]

    def save_judgement(self, sample_id: str, judge: dict) -> None:
        if _json_len(judge) > MAX_JUDGE_CHARS:
            raise ValueError(f"judgement is over {MAX_JUDGE_CHARS} chars")
        with self._lock:
            for x in self.training_samples:
                if x["id"] == str(sample_id):
                    x["judge"], x["judged_at"] = copy.deepcopy(judge), datetime.now(timezone.utc)

    def judged_samples(self, days: int = 90, limit: int = 200) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
        with self._lock:
            rows = [x for x in self.training_samples if x["judged_at"] is not None and x["judged_at"] >= since]
        rows.sort(key=lambda x: x["judged_at"], reverse=True)
        return [{k: copy.deepcopy(x[k]) for k in ("id", "meta", "cv_text", "analysis", "judge")}
                for x in rows[:max(min(limit, JUDGED_MAX), 1)]]

    def purge_training_samples(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=SAMPLE_RETENTION_DAYS)
        with self._lock:
            kept = [x for x in self.training_samples if x["created_at"] >= cutoff]
            removed = len(self.training_samples) - len(kept)
            self.training_samples = kept
        return removed

    def upsert_vacancies(self, items: list[dict]) -> int:
        count = 0
        for it in items:
            url, title = (it.get("url") or "").strip(), (it.get("title") or "").strip()
            if not url or not title:
                continue
            row = {**it, "url": url, "title": title}
            if row.get("snippet"):
                row["snippet"] = row["snippet"][:600]
            # The key is unique (url_hash, role_family, region) in SQL; null -> ''.
            key = (url, row.get("role_family") or "", row.get("region") or "")
            row["role_family"], row["region"] = key[1], key[2]
            old = self.vacancies.get(key)
            if old:  # like ON CONFLICT: update without overwriting existing values with empty ones
                row = {**old, **{k: v for k, v in row.items() if v is not None}}
            self.vacancies[key] = {**row, "last_seen_at": datetime.now(timezone.utc)}
            count += 1
        return count

    def recent_vacancies(self, role_family: str | None, region: str | None, days: int = 30,
                         limit: int = 300) -> list[dict]:
        since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))
        rows = [v for v in self.vacancies.values() if v["last_seen_at"] >= since
                and (role_family is None or v.get("role_family") == role_family)
                and (region is None or v.get("region") == region)]
        rows.sort(key=lambda v: v["last_seen_at"], reverse=True)
        seen: set[str] = set()  # different postings: the same url from several roles/regions only once
        rows = [v for v in rows if not (v["url"] in seen or seen.add(v["url"]))]
        keys = ("url", "source", "title", "company", "location", "posted_at", "salary", "snippet", "remote")
        return [{"id": i + 1, **{k: v.get(k) for k in keys}} for i, v in enumerate(rows[:max(min(limit, 1000), 1)])]

    def save_skill_demand(self, items: list[dict]) -> int:
        valid = [i for i in items if i.get("program") and i.get("region") and i.get("skill")]
        for key in {(i["program"], i["region"]) for i in valid}:
            self.skill_demand_rows[key] = []
        now = datetime.now(timezone.utc).isoformat()
        for i in valid:
            self.skill_demand_rows[(i["program"], i["region"])].append({
                "skill": i["skill"], "postings": int(i.get("postings") or 0),
                "total_postings": int(i.get("total_postings") or 0),
                "window_days": int(i.get("window_days") or 30), "computed_at": now})
        return len(valid)

    def skill_demand(self, program: str, region: str) -> list[dict]:
        rows = self.skill_demand_rows.get((program, region), [])
        return sorted(rows, key=lambda r: (-r["postings"], r["skill"]))[:20]

    # --- Billing: the same semantics as the cvmax_* billing functions in supabase/schema.sql ---
    # Every credit use is linked to the purchase it consumed (oldest first). Balance = per purchase that is not
    # refunded and younger than 12 months: credits minus the live uses linked to it.

    def _live(self, p: dict) -> bool:
        return p["status"] == "paid" and p["created_at"] > _expiry_cutoff()

    def _remaining(self, p: dict) -> int:
        used = sum(1 for u in self.credit_uses
                   if u["purchase"] is p and u["mode"] == "credit" and not u["refunded"])
        return max(p["full_credits"] - used, 0)

    def _balance(self, user_key: str) -> int:
        return sum(self._remaining(p) for p in self.purchases if p["user_key"] == user_key and self._live(p))

    def _next_purchase(self, user_key: str) -> dict | None:
        rows = [p for p in self.purchases if p["user_key"] == user_key and self._live(p) and self._remaining(p) > 0]
        return min(rows, key=lambda p: p["created_at"]) if rows else None  # min() keeps the first of equal dates

    def _review_uses(self, user_key: str) -> list[dict]:
        return [u for u in self.credit_uses
                if u["user_key"] == user_key and u["mode"] == "credit" and u["purpose"] == "review"
                and not u["refunded"]]

    def _rescannable(self, user_key: str, role: str | None = None) -> list[dict]:
        return [u for u in self._review_uses(user_key)
                if u["rescans_left"] > 0 and u["purchase"]["status"] == "paid"
                and (role is None or u["target_role"] == role)]

    def grant_purchase(self, user_key: str, plan: str, transaction_id: str, amount_cents: int | None = None,
                       currency: str | None = None) -> dict:
        if plan not in PLAN_CREDITS:
            raise ValueError("bad plan")
        _check_billing_ids(user_key=user_key, transaction_id=transaction_id)
        with self._lock:
            if any(p["transaction_id"] == transaction_id for p in self.purchases):
                return {"granted": False, "reason": "duplicate"}
            self.purchases.append({"user_key": user_key.lower(), "plan": plan, "transaction_id": transaction_id,
                                   "full_credits": PLAN_CREDITS[plan], "amount_cents": amount_cents,
                                   "currency": currency, "status": "paid",
                                   "created_at": datetime.now(timezone.utc)})
        return {"granted": True, "plan": plan, "full_credits": PLAN_CREDITS[plan]}

    def refund_purchase(self, transaction_id: str) -> dict:
        _check_billing_ids(transaction_id=transaction_id)
        with self._lock:
            row = next((p for p in self.purchases if p["transaction_id"] == transaction_id), None)
            if row is None:
                return {"refunded": False, "reason": "unknown"}
            if row["status"] == "refunded":
                return {"refunded": False, "reason": "already_refunded"}
            before = self._balance(row["user_key"])
            row["status"] = "refunded"  # only this purchase's unspent credits go; uses linked to it stay consumed
            return {"refunded": True, "plan": row["plan"], "removed": before - self._balance(row["user_key"])}

    def credits(self, user_key: str) -> dict:
        _check_billing_ids(user_key=user_key)
        key = user_key.lower()
        with self._lock:
            reviews = self._review_uses(key)
            last = reviews[-1] if reviews else None
            return {"full": self._balance(key),
                    "has_hunt": any(p["user_key"] == key and p["plan"] == "hunt" and p["status"] == "paid"
                                    for p in self.purchases),
                    "rescan_roles": sorted({u["target_role"] for u in self._rescannable(key)}),
                    "last_use": None if last is None else {
                        "analysis_id": last["analysis_id"], "target_role": last["target_role"],
                        "rescans_left": last["rescans_left"], "created_at": last["created_at"].isoformat()}}

    def use_credit(self, user_key: str, analysis_id: str, target_role: str, purpose: str = "review") -> dict:
        if purpose not in PURPOSES:
            raise ValueError("bad purpose")
        _check_billing_ids(user_key=user_key, analysis_id=analysis_id)
        key, role = user_key.lower(), norm_role(target_role)
        with self._lock:
            existing = next((u for u in self.credit_uses if u["analysis_id"] == analysis_id), None)
            if existing is not None:
                if existing["user_key"] != key:
                    return {"allowed": False, "reason": "conflict"}
                if not existing["refunded"]:
                    return {"allowed": True, "mode": existing["mode"], "duplicate": True}
                self.credit_uses.remove(existing)  # refunded earlier (model failure): this retry spends again
            now = datetime.now(timezone.utc)
            if purpose == "review":
                # A free re-scan: an earlier review use with the same role that still has one and is not refunded.
                earlier = self._rescannable(key, role)
                if earlier:
                    parent = earlier[-1]
                    parent["rescans_left"] -= 1
                    self.credit_uses.append({"user_key": key, "analysis_id": analysis_id, "mode": "rescan",
                                             "purpose": "review", "target_role": role, "rescans_left": 0,
                                             "parent": parent, "purchase": None, "refunded": False,
                                             "created_at": now})
                    return {"allowed": True, "mode": "rescan", "duplicate": False}
            purchase = self._next_purchase(key)
            if purchase is None:
                return {"allowed": False, "reason": "no_credit"}
            self.credit_uses.append({"user_key": key, "analysis_id": analysis_id, "mode": "credit",
                                     "purpose": purpose, "target_role": role,
                                     "rescans_left": 1 if purpose == "review" else 0, "parent": None,
                                     "purchase": purchase, "refunded": False, "created_at": now})
            return {"allowed": True, "mode": "credit", "duplicate": False}

    def refund_credit(self, user_key: str, analysis_id: str) -> dict:
        _check_billing_ids(user_key=user_key, analysis_id=analysis_id)
        key = user_key.lower()
        with self._lock:
            row = next((u for u in self.credit_uses if u["analysis_id"] == analysis_id and u["user_key"] == key), None)
            if row is None or row["refunded"]:
                return {"refunded": False}
            row["refunded"] = True
            if row["mode"] == "rescan" and row["parent"] is not None:
                row["parent"]["rescans_left"] += 1
            return {"refunded": True, "mode": row["mode"]}


def _as_count(value: Any) -> int | None:
    """An integer 0..999999 from the payload or None (like ^[0-9]{1,6}$ in SQL). Never raises an exception."""
    if type(value) is int:
        return value if 0 <= value <= 999999 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        return int(value)
    return None
