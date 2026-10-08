"""База даних у Supabase: юзери, денні ліміти, збережені результати.

Сайт не має прямого доступу до таблиць. Він викликає кілька функцій у базі (див. supabase/schema.sql),
і кожна перевіряє секретний токен CVMAX_DB_TOKEN. Якщо база не налаштована, працює MemoryDB:
ліміти рахуються в пам'яті сервера, а результати не зберігаються.
"""

from __future__ import annotations

import json
import logging
import re
import threading
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
# Види подій, які йдуть у вивантаження для рефлексії (так само, як у SQL).
EXPORT_KINDS = ("analysis_done", "analysis_rated", "edits_decided", "grill_turn",
                "outcome", "rescan", "export", "jobs_shown", "job_applied")


def _check_event(kind: str, payload: dict) -> None:
    """Перевірка до мережі: ті самі межі, що й у cvmax_log_event."""
    if not kind or len(kind) > MAX_KIND_LEN:
        raise ValueError(f"event kind must be 1..{MAX_KIND_LEN} chars")
    if len(json.dumps(payload, ensure_ascii=False, default=str)) > MAX_PAYLOAD_LEN:
        raise ValueError(f"event payload is over {MAX_PAYLOAD_LEN} chars")


def _pacific_day() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


class SupabaseDB:
    persistent = True

    def __init__(self, url: str, key: str, token: str, session: Any = None, timeout: float = 10) -> None:
        self.base = url.rstrip("/") + "/rest/v1/rpc/"
        self.headers = {"apikey": key, "Content-Type": "application/json"}
        if key.count(".") == 2:  # старий JWT anon-ключ іде ще й як Bearer
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
            # Деталі тільки в журнал сервера: юзеру вони не потрібні і можуть розкрити будову бази.
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
        """Анонімний відгук: без email, тільки сторінка, оцінка і текст."""
        self._rpc("cvmax_save_feedback", p_page=page, p_rating=rating, p_message=message)

    def join_waitlist(self, email: str, plan: str, source: str | None) -> None:
        """Список запуску: email, обраний тариф і звідки прийшов."""
        self._rpc("cvmax_join_waitlist", p_email=email, p_plan=plan, p_source=source, p_country=None)

    def log_edit_feedback(self, user_key: str, analysis_id: str, target_role: str, program: str,
                          items: list[dict]) -> int:
        """Які правки юзер прийняв, а які ні. items: source, section, priority, before, after, accepted."""
        return self._rpc("cvmax_log_edit_feedback", p_user_key=user_key, p_analysis_id=analysis_id,
                         p_target_role=target_role, p_program=program, p_items=items) or 0

    def log_event(self, user_key: str, kind: str, payload: dict) -> None:
        """Подія навчання: тільки id, оцінки й рішення, без тексту CV."""
        _check_event(kind, payload)
        self._rpc("cvmax_log_event", p_user_key=user_key, p_kind=kind, p_payload=payload)

    def variant_stats(self, task: str, days: int = 90) -> list[dict]:
        """[{variant, program, successes, failures}] для бандита."""
        return self._rpc("cvmax_variant_stats", p_task=task, p_days=days) or []

    def learning_export(self, days: int = 30) -> dict:
        """Анонімні дані для тижневої рефлексії: {edit_feedback, feedback, events, breadth}.

        breadth: {програма: {"users": int, "analyses": int}} (у MemoryDB завжди {}).
        """
        return self._rpc("cvmax_learning_export", p_days=days) or {"edit_feedback": [], "feedback": [], "events": []}

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


class MemoryDB:
    """Запасний варіант без Supabase: ліміти в пам'яті процесу, нічого не зберігається надовго."""

    persistent = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[tuple[str, str, str], int] = defaultdict(int)
        self._users: dict[str, dict] = {}
        self.feedback: list[dict] = []
        self.waitlist: dict[str, dict] = {}
        self.events: list[dict] = []
        self.vacancies: dict[tuple[str, str, str], dict] = {}
        self.skill_demand_rows: dict[tuple[str, str], list[dict]] = {}

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
        """Та сама логіка нагороди, що й cvmax_variant_stats у SQL."""
        events = self._events_since(days)
        with self._lock:
            all_events = list(self.events)
        done: dict[str, dict] = {}
        for e in events:  # остання analysis_done на analysis_id
            p = e["payload"]
            aid, variant = p.get("analysis_id"), p.get("variant")
            if e["kind"] == "analysis_done" and aid and variant and (p.get("task") or "analysis") == task:
                done[str(aid)] = {"variant": str(variant), "program": str(p.get("program") or "")}
        rated: dict[str, int | None] = {}
        edits: dict[str, tuple[int | None, int | None]] = {}
        outcome: dict[str, object] = {}  # остання відповідь на analysis_id
        for e in all_events:  # події йдуть у порядку додавання, тож останнє значення перемагає
            p = e["payload"]
            aid = p.get("analysis_id")
            if not aid:
                continue
            aid = str(aid)
            if e["kind"] == "analysis_rated":
                r = p.get("rating")  # лише справжній int 0/1: bool і float не рахуються (як текстове порівняння в SQL)
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

    def upsert_vacancies(self, items: list[dict]) -> int:
        count = 0
        for it in items:
            url, title = (it.get("url") or "").strip(), (it.get("title") or "").strip()
            if not url or not title:
                continue
            row = {**it, "url": url, "title": title}
            if row.get("snippet"):
                row["snippet"] = row["snippet"][:600]
            # Ключ як unique (url_hash, role_family, region) у SQL; null -> ''.
            key = (url, row.get("role_family") or "", row.get("region") or "")
            row["role_family"], row["region"] = key[1], key[2]
            old = self.vacancies.get(key)
            if old:  # як ON CONFLICT: оновлюємо, не затираючи наявне порожнім
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
        seen: set[str] = set()  # різні вакансії: той самий url з кількох ролей/регіонів лише раз
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


def _as_count(value: Any) -> int | None:
    """Ціле 0..999999 з payload або None (як ^[0-9]{1,6}$ у SQL). Ніколи не кидає виняток."""
    if type(value) is int:
        return value if 0 <= value <= 999999 else None
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,6}", value):
        return int(value)
    return None
