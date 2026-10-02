"""База даних у Supabase: юзери, денні ліміти, збережені результати.

Сайт не має прямого доступу до таблиць. Він викликає кілька функцій у базі (див. supabase/schema.sql),
і кожна перевіряє секретний токен CVMAX_DB_TOKEN. Якщо база не налаштована, працює MemoryDB:
ліміти рахуються в пам'яті сервера, а результати не зберігаються.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("cvmax")


class DBError(RuntimeError):
    pass


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

    def log_edit_feedback(self, user_key: str, analysis_id: str, target_role: str, program: str,
                          items: list[dict]) -> int:
        """Які правки юзер прийняв, а які ні. items: source, section, priority, before, after, accepted."""
        return self._rpc("cvmax_log_edit_feedback", p_user_key=user_key, p_analysis_id=analysis_id,
                         p_target_role=target_role, p_program=program, p_items=items) or 0


class MemoryDB:
    """Запасний варіант без Supabase: ліміти в пам'яті процесу, нічого не зберігається надовго."""

    persistent = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._usage: dict[tuple[str, str, str], int] = defaultdict(int)
        self._users: dict[str, dict] = {}
        self.feedback: list[dict] = []

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

    def log_edit_feedback(self, user_key: str, analysis_id: str, target_role: str, program: str,
                          items: list[dict]) -> int:
        return 0

    def save_feedback(self, page: str, rating: int | None, message: str) -> None:
        self.feedback.append({"page": page, "rating": rating, "message": message})

