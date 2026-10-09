"""Safe HTTP layer: timeout, size limit, no redirects, cache and parallelism."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, wait
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from cvmax import config

USER_AGENT = "GetCVmax/1.0 (+https://getcvmax.com)"
ACCEPT = "application/json, application/rss+xml, application/xml;q=0.9, */*;q=0.5"
CACHE_MAX = 200

_cache: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_lock = threading.Lock()
CONNECT_TIMEOUT_S = 3.0

# A shared pool: ThreadPoolExecutor threads are NOT daemon (since Python 3.9), so the size is limited by
# config.JOBS_WORKERS, so "abandoned" threads do not pile up without bound. Each thread
# finishes by itself on the overall deadline in _read_limited (connect 3 s + read timeout).
_executor = ThreadPoolExecutor(max_workers=max(1, config.JOBS_WORKERS), thread_name_prefix="jobs-http")


def _headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": ACCEPT}


def _scheme_ok(url: str) -> bool:
    try:
        return urlsplit(url).scheme in ("http", "https")
    except ValueError:
        return False


def _timeouts(timeout: float, deadline: float) -> tuple[float, float]:
    """(connect, read) for requests: connect 3 s, read no more than the remaining deadline."""
    remaining = max(0.05, deadline - time.monotonic())
    return (min(CONNECT_TIMEOUT_S, remaining), min(timeout, remaining))


def _read_limited(resp: Any, max_bytes: int, timeout: float | None = None,
                  deadline: float | None = None) -> bytes | None:
    """Reads the body in small chunks; None if the status is not 200, the body is too large or the overall deadline has run out."""
    if deadline is None and timeout is not None:
        deadline = time.monotonic() + timeout
    try:
        if resp.status_code != 200:
            return None
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=4096):
            # we check the deadline after every chunk, even an empty one (a slow "dripping" server)
            if deadline is not None and time.monotonic() > deadline:
                return None
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        try:
            resp.close()
        except Exception:
            pass


def fetch(url: str, *, timeout: float = 8.0, max_bytes: int = 2_000_000,
          session: Any = None) -> bytes | None:
    """GET without redirects. Never raises an exception: on any problem returns None."""
    if not _scheme_ok(url):
        return None
    try:
        getter = (session or requests).get
        deadline = time.monotonic() + timeout
        resp = getter(url, headers=_headers(), timeout=_timeouts(timeout, deadline), stream=True,
                      allow_redirects=False)
        return _read_limited(resp, max_bytes, timeout, deadline)
    except Exception:
        return None


def fetch_post(url: str, json_body: Any, *, timeout: float = 8.0,
               max_bytes: int = 2_000_000, session: Any = None) -> bytes | None:
    """POST with a JSON body; the same guarantees as fetch."""
    if not _scheme_ok(url):
        return None
    try:
        poster = (session or requests).post
        deadline = time.monotonic() + timeout
        resp = poster(url, json=json_body, headers=_headers(), timeout=_timeouts(timeout, deadline),
                      stream=True, allow_redirects=False)
        return _read_limited(resp, max_bytes, timeout, deadline)
    except Exception:
        return None


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def cached_fetch(url: str, ttl: float = 1800, *, timeout: float = 8.0,
                 max_bytes: int = 2_000_000, session: Any = None) -> bytes | None:
    """fetch with a TTL cache (up to 200 entries). Failures are not cached."""
    now = time.monotonic()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]
    body = fetch(url, timeout=timeout, max_bytes=max_bytes, session=session)
    if body is not None:
        with _lock:
            _cache[url] = (time.monotonic(), body)
            _cache.move_to_end(url)
            while len(_cache) > CACHE_MAX:
                _cache.popitem(last=False)
    return body


def _safe_call(func: Callable[[], bytes | None]) -> bytes | None:
    try:
        return func()
    except Exception:
        return None


def _run_all(tasks: list[Callable[[], bytes | None]]) -> list[bytes | None]:
    """Runs tasks in the shared pool; waits no longer than JOBS_TIMEOUT_S + 1 s and abandons the rest."""
    futs = [_executor.submit(_safe_call, t) for t in tasks]
    wait(futs, timeout=config.JOBS_TIMEOUT_S + 1)
    out: list[bytes | None] = []
    for f in futs:
        if not f.done():
            f.cancel()  # only what has not started yet is cancelled
            out.append(None)
        else:
            out.append(None if f.cancelled() else f.result())
    return out


def fetch_many(urls: list[str], fetch: Callable[[str], bytes | None] = cached_fetch,
               workers: int = 6) -> dict[str, bytes | None]:
    """Downloads unique addresses in parallel; result: url -> body or None.

    `workers` is kept for compatibility: parallelism is limited by the shared pool (JOBS_WORKERS)."""
    unique = list(dict.fromkeys(urls))
    if not unique:
        return {}
    bodies = _run_all([lambda u=u: fetch(u) for u in unique])
    return dict(zip(unique, bodies))


def fetch_requests(reqs: list[tuple[str, Any]], workers: int = 6) -> list[bytes | None]:
    """Runs a list of (url, json_body | None): GET for None, otherwise POST. Order is preserved."""
    if not reqs:
        return []

    def one(req: tuple[str, Any]) -> bytes | None:
        url, body = req
        if body is None:
            return cached_fetch(url, timeout=config.JOBS_TIMEOUT_S)
        return fetch_post(url, body, timeout=config.JOBS_TIMEOUT_S)

    return _run_all([lambda r=r: one(r) for r in reqs])
