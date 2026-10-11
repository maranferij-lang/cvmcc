"""Weekly link health check for the vetted learning resources.

Run:  python scripts/check_links.py                 (check everything, update cvmax/resources/link_status.json)
      python scripts/check_links.py --dry-run       (print the table, write nothing)
      python scripts/check_links.py --only sql-xyz  (one resource id; repeatable)

Every catalog resource that is not marked dead is checked: its url and each evidence url. Classification:
  2xx, or a 3xx that resolved                -> ok
  404, 410, DNS or connection failure        -> fail
  401, 403, 429, other 4xx, 5xx, timeouts    -> unknown (bot protection and outages are not proof of death)
A redirect to another registrable domain is not followed; it is recorded as "moved" (moved_to) and does not count
as a failure. link_status.json holds {id: {"ok", "code", "checked", "fails", "moved_to", "evidence_fails"}}:
fails goes up by one on a fail and back to 0 on ok; unknown leaves the previous entry as it is (only
evidence_fails is refreshed). The app hides a resource when ok is False and fails >= 2 (visible_resources).
If the entry is identical to the previous one except for the date, the old "checked" date is kept so that a quiet
week produces no diff and no pull request.

Exit codes: 0 normally, 2 for a bad argument or an unreadable catalog, 3 when more than 20 percent of the checked
resources would become hidden in this run (a sign of a broken checker or network); then nothing is written.
No secrets are needed. The checker is polite: one request per host at a time with a 1 s pause, at most 4 hosts in
parallel, 20 minutes in total (a hard wall-clock cap, see check_urls) and at most 30 s for one url.

Safety (the urls come from data files, so they are untrusted):
  - a url that cvmax.resources.catalog.url_error rejects (IP literal, localhost, internal name, credentials, plain
    http, tracking query) is never fetched; it is reported as skipped and counts as unknown;
  - the real fetcher resolves the host with socket.getaddrinfo before every request and every redirect hop and refuses
    private, loopback, link-local, multicast, reserved, unspecified and otherwise non-global addresses (IPv6 and
    IPv4-mapped forms included); such a url is unknown, not fail;
  - no cookies, no netrc or other credentials are sent, and a redirect to a non-https url is never followed.
"""
from __future__ import annotations

import argparse
import functools
import http.cookiejar
import inspect
import ipaddress
import json
import os
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.resources.catalog import (  # noqa: E402
    CATALOG_DIR, LINK_STATUS_PATH, CatalogError, load_catalog, load_link_status, url_error, visible_resources,
)
from cvmax.resources.models import Resource  # noqa: E402

USER_AGENT = "GetCVmaxLinkCheck/1.0 (+https://getcvmax.com)"
DEFAULT_TIMEOUT = 10.0
MAX_REDIRECTS = 5
HOST_PAUSE = 1.0
MAX_PARALLEL_HOSTS = 4
TIME_CAP_SECONDS = 20 * 60
PER_URL_SECONDS = 30.0
HIDDEN_SHARE_LIMIT = 0.20
READ_BYTES = 1024
# One byte per read: a read of N bytes blocks until N bytes arrive, so a server that drips its body could hold a
# bigger chunk for N socket timeouts. With one byte the deadline is checked after every byte that arrives.
READ_CHUNK = 1

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GUARD = 3

# Public suffixes with more than one label, so the registrable domain keeps one more label.
_MULTI_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "edu.au", "org.au", "co.nz", "co.jp", "co.in", "com.br",
    "com.cn", "com.ua", "org.ua", "edu.ua", "net.ua", "in.ua", "kiev.ua", "gov.ua", "com.pl", "co.za",
    "github.io", "gitlab.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev", "web.app",
    "firebaseapp.com", "herokuapp.com", "blogspot.com", "wordpress.com", "substack.com", "notion.site",
})


@dataclass(frozen=True)
class FetchResult:
    """What a fetcher returns for one url. code is the final HTTP status (None when no response arrived).

    error is one of "timeout", "connection" (DNS or connect failure), "ssl", "insecure_redirect",
    "too_many_redirects", "other". moved_to is the absolute target of a refused cross-domain redirect.
    """
    code: int | None
    error: str | None = None
    moved_to: str | None = None


Fetcher = Callable[[str, float], FetchResult]
# A fetcher is called as fetcher(url, timeout). One that also declares a `budget` parameter (fetch_url does) is called
# as fetcher(url, timeout, budget=seconds) with the wall-clock seconds this single url may take in total.
DnsCheck = Callable[[str], None]
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


class UnsafeAddress(Exception):
    """A host resolves to an address that must not be contacted (private, loopback, link-local, and so on)."""


def registrable_domain(host: str) -> str:
    """Approximate registrable domain (no public suffix list: a short table of multi-label suffixes)."""
    labels = [p for p in (host or "").lower().rstrip(".").split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _MULTI_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _domain_of(url: str) -> str:
    try:
        return registrable_domain(urlsplit(url).hostname or "")
    except ValueError:
        return ""


def is_public_address(ip: str) -> bool:
    """True only for a globally routable address. IPv4-mapped, 6to4, Teredo, NAT64 and IPv4-compatible IPv6 forms
    are judged by the IPv4 address they carry. Anything that does not parse is refused."""
    try:
        addr = ipaddress.ip_address(str(ip).split("%", 1)[0].strip("[]"))
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        embedded: list[ipaddress.IPv4Address] = []
        if addr.ipv4_mapped is not None:
            embedded.append(addr.ipv4_mapped)
        if addr.sixtofour is not None:
            embedded.append(addr.sixtofour)
        if addr.teredo is not None:
            embedded.extend(addr.teredo)
        raw = int(addr)
        if addr in _NAT64 or raw >> 32 == 0:  # 64:ff9b::/96, or ::a.b.c.d (deprecated IPv4-compatible, also ::, ::1)
            embedded.append(ipaddress.IPv4Address(raw & 0xFFFFFFFF))
        if any(not is_public_address(str(v4)) for v4 in embedded):
            return False
        if addr.is_site_local:  # fec0::/10, deprecated but still not public
            return False
    return not (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
                or addr.is_reserved or addr.is_unspecified) and addr.is_global


def check_host_is_public(host: str) -> None:
    """The default DNS check: resolves host with socket.getaddrinfo and raises UnsafeAddress when any answer is not a
    public address. A resolution failure (socket.gaierror) propagates: the fetcher reports it as a connection error."""
    infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not infos:
        raise socket.gaierror(socket.EAI_NONAME, "no address")
    for info in infos:
        ip = str(info[4][0])
        if not is_public_address(ip):
            raise UnsafeAddress(f"{host} resolves to {ip}")


def unsafe_url_reason(url: str, *, check_query: bool = True) -> str | None:
    """Why a url must not be fetched at all (the catalog url rules of url_error), or None. Fails closed."""
    try:
        return url_error(url, check_query=check_query)
    except Exception as e:  # noqa: BLE001 - fail closed
        return f"url could not be checked ({type(e).__name__})"


def _no_auth(request):  # a callable auth stops requests from adding credentials from ~/.netrc
    return request


def fetch_url(url: str, timeout: float = DEFAULT_TIMEOUT, budget: float = PER_URL_SECONDS, *,
              dns_check: DnsCheck | None = None, clock: Callable[[], float] = time.monotonic) -> FetchResult:
    """Real fetcher: GET with stream and a small Range, https only, up to 5 same-domain redirects.

    budget is the total wall-clock time for this url (all hops and the body read); exceeding it is a "timeout".
    Before the first request and before every redirect hop the url must pass url_error (query rules aside) and the
    host must resolve to public addresses only (dns_check, default check_host_is_public); otherwise the result is
    error "blocked_address" and no request is sent. No cookies and no credentials are ever sent.
    """
    import requests  # imported lazily so tests with a fake fetcher need no network stack

    check = dns_check or check_host_is_public
    deadline = clock() + max(budget, 0.0)
    origin = _domain_of(url)
    current = url
    session = requests.Session()
    session.trust_env = True  # proxies from the environment; credentials are still blocked by _no_auth
    jar = getattr(session, "cookies", None)
    if jar is not None and hasattr(jar, "set_policy"):
        jar.set_policy(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))  # accept and send no cookie
    headers = {"User-Agent": USER_AGENT, "Range": f"bytes=0-{READ_BYTES - 1}", "Accept": "*/*"}
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not current.lower().startswith("https://"):
                return FetchResult(None, error="insecure_redirect")
            if unsafe_url_reason(current, check_query=False):  # redirect hops may carry tracking queries
                return FetchResult(None, error="blocked_address")
            remaining = deadline - clock()
            if remaining <= 0:
                return FetchResult(None, error="timeout")
            host = urlsplit(current).hostname or ""
            try:
                check(host)
            except UnsafeAddress:
                return FetchResult(None, error="blocked_address")
            except socket.gaierror:
                return FetchResult(None, error="connection")
            remaining = deadline - clock()
            if remaining <= 0:
                return FetchResult(None, error="timeout")
            resp = session.get(current, headers=headers, timeout=max(min(timeout, remaining), 0.05), stream=True,
                               allow_redirects=False, auth=_no_auth)
            try:
                code = resp.status_code
                location = resp.headers.get("Location")
                if 300 <= code < 400 and location:
                    target = urljoin(current, location)
                    if not target.lower().startswith("https://"):
                        return FetchResult(code, error="insecure_redirect")
                    if _domain_of(target) != origin:
                        return FetchResult(code, moved_to=target)
                    current = target
                    continue
                if 200 <= code < 300:
                    got = 0
                    try:
                        for chunk in resp.iter_content(READ_CHUNK):  # a small read proves the body arrives
                            got += len(chunk or b"")
                            if got >= READ_BYTES:
                                break
                            if clock() >= deadline:
                                return FetchResult(None, error="timeout")
                    except (requests.RequestException, OSError):
                        pass
                    if clock() >= deadline:
                        return FetchResult(None, error="timeout")
                return FetchResult(code)
            finally:
                resp.close()
        return FetchResult(None, error="too_many_redirects")
    except requests.Timeout:
        return FetchResult(None, error="timeout")
    except requests.exceptions.SSLError:
        return FetchResult(None, error="ssl")
    except requests.ConnectionError:
        return FetchResult(None, error="connection")
    except (requests.RequestException, ValueError, OSError):
        return FetchResult(None, error="other")
    finally:
        session.close()


def classify(result: FetchResult | None) -> str:
    """Returns "ok", "fail", "unknown" or "moved". None (not checked in time) is unknown."""
    if result is None:
        return "unknown"
    if result.moved_to:
        return "moved"
    code = result.code
    if result.error and result.error != "connection":
        return "unknown"  # a refused downgrade, a loop, a timeout: not proof of death
    if code is None:
        return "fail" if result.error == "connection" else "unknown"
    if code in (404, 410):
        return "fail"
    if 200 <= code < 400:
        return "ok"
    return "unknown"


def _takes_budget(fetcher: Callable) -> bool:
    try:
        params = inspect.signature(fetcher).parameters
    except (TypeError, ValueError):
        return False
    return "budget" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _safe_fetch(fetcher: Fetcher, url: str, timeout: float, budget: float | None = None) -> FetchResult:
    try:
        if budget is not None and _takes_budget(fetcher):
            res = fetcher(url, timeout, budget=budget)  # type: ignore[call-arg]
        else:
            res = fetcher(url, timeout)
    except Exception as e:  # noqa: BLE001 - a broken fetcher must not stop the run; it counts as unknown
        return FetchResult(None, error=f"other:{type(e).__name__}")
    return res if isinstance(res, FetchResult) else FetchResult(None, error="other")


def check_urls(urls: Iterable[str], fetcher: Fetcher, *, timeout: float = DEFAULT_TIMEOUT,
               sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
               pause: float = HOST_PAUSE, max_hosts: int = MAX_PARALLEL_HOSTS,
               time_cap_seconds: float | None = None, per_url_seconds: float = PER_URL_SECONDS,
               time_cap: float | None = None) -> dict[str, FetchResult | None]:
    """Fetches every distinct url. Sequential per host with a pause, hosts in parallel (at most max_hosts).

    A url that url_error rejects is never fetched (a note is printed). A url not reached before the time cap maps to
    None. The cap is hard: the call returns when time_cap_seconds of wall-clock time have passed even if fetches
    are still running (pending work is cancelled, running fetches are abandoned and their late results discarded).
    Each fetch gets min(time left, per_url_seconds) as its budget; a fetcher that declares a `budget` parameter
    (fetch_url) enforces it itself. time_cap is the old name of time_cap_seconds.
    """
    cap = time_cap_seconds if time_cap_seconds is not None else (time_cap if time_cap is not None else TIME_CAP_SECONDS)
    by_host: dict[str, list[str]] = {}
    for u in sorted(set(urls)):
        reason = unsafe_url_reason(u)
        if reason:
            print(f"Skipped, not fetched ({reason}): {ascii(u)}")
            continue
        try:
            host = (urlsplit(u).hostname or "").lower()
        except ValueError:
            host = ""
        by_host.setdefault(host, []).append(u)
    results: dict[str, FetchResult | None] = {u: None for group in by_host.values() for u in group}
    if not results:
        return results
    deadline = clock() + cap
    lock = threading.Lock()
    stop = threading.Event()

    def work(group: list[str]) -> None:
        for i, u in enumerate(group):
            if stop.is_set() or clock() >= deadline:
                return
            if i:
                sleep(max(0.0, min(pause, deadline - clock())))
                if stop.is_set() or clock() >= deadline:
                    return
            res = _safe_fetch(fetcher, u, timeout, max(0.0, min(deadline - clock(), per_url_seconds)))
            with lock:
                if stop.is_set():
                    return  # the cap passed while this fetch ran: its result is discarded
                results[u] = res

    groups = [by_host[h] for h in sorted(by_host)]
    pool = ThreadPoolExecutor(max_workers=max(1, min(max_hosts, len(groups))))
    futures = [pool.submit(work, g) for g in groups]
    # The wait uses real time on purpose: it is the last line of defence, independent of the injected clock.
    _, pending = wait(futures, timeout=max(cap, 0.0))
    if pending:
        with lock:
            stop.set()
        pool.shutdown(wait=False, cancel_futures=True)
    else:
        pool.shutdown(wait=True)
    with lock:
        stop.set()
        return dict(results)


# --- status updates ---

def _prev_entry(status: dict, rid: str) -> dict:
    prev = status.get(rid)
    return prev if isinstance(prev, dict) else {}


def _prev_fails(prev: dict) -> int:
    f = prev.get("fails")
    return f if isinstance(f, int) and not isinstance(f, bool) and f >= 0 else 0


def new_entry(prev: dict, kind: str, result: FetchResult | None, evidence_fails: int, today: str) -> dict | None:
    """Applies one classification to the previous entry. Returns None when there is nothing to store."""
    if kind == "unknown":
        if not prev:
            return None
        entry = dict(prev)
        entry["evidence_fails"] = evidence_fails
        return entry
    code = result.code if result else None
    if kind == "fail":
        entry = {"ok": False, "code": code, "checked": today, "fails": _prev_fails(prev) + 1, "moved_to": None}
    else:  # ok or moved
        entry = {"ok": True, "code": code, "checked": today, "fails": 0,
                 "moved_to": result.moved_to if kind == "moved" and result else None}
    entry["evidence_fails"] = evidence_fails
    # A quiet week must not change the file: keep the old date when nothing else differs.
    if prev and isinstance(prev.get("checked"), str) and {**entry, "checked": prev["checked"]} == prev:
        entry["checked"] = prev["checked"]
    return entry


@dataclass
class Outcome:
    """The result of a run: the new status dict, the change lists and the guard numbers."""
    status: dict
    newly_failing: list[str]
    newly_dead: list[str]
    recovered: list[str]
    moved: list[tuple[str, str]]
    evidence_failing: list[tuple[str, int]]
    newly_hidden: list[str]
    checked_resources: int
    checked_urls: int
    unreached_urls: int
    skipped_urls: int = 0

    @property
    def hidden_share(self) -> float:
        return len(self.newly_hidden) / self.checked_resources if self.checked_resources else 0.0

    @property
    def guard_tripped(self) -> bool:
        return self.hidden_share > HIDDEN_SHARE_LIMIT


def _hidden_ids(targets: list[Resource], status: dict) -> set[str]:
    """Ids that visible_resources would drop because of the link status (status is forced to active)."""
    active = [replace(r, status="active") for r in targets]
    shown = {r.id for r in visible_resources(active, status)}
    return {r.id for r in targets if r.id not in shown}


def run_checks(targets: list[Resource], old_status: dict, fetcher: Fetcher, *, today: str,
               timeout: float = DEFAULT_TIMEOUT, sleep: Callable[[float], None] = time.sleep,
               clock: Callable[[], float] = time.monotonic, pause: float = HOST_PAUSE,
               time_cap_seconds: float | None = None, per_url_seconds: float = PER_URL_SECONDS,
               time_cap: float | None = None) -> Outcome:
    """Checks the targets and computes the new status without touching any file."""
    urls: list[str] = []
    for r in targets:
        urls.append(r.url)
        urls.extend(ev.url for ev in r.evidence)
    results = check_urls(urls, fetcher, timeout=timeout, sleep=sleep, clock=clock, pause=pause,
                         time_cap_seconds=time_cap_seconds, per_url_seconds=per_url_seconds, time_cap=time_cap)
    skipped = sum(1 for u in set(urls) if unsafe_url_reason(u))  # never fetched, so absent from results: unknown

    new_status = dict(old_status)
    newly_failing: list[str] = []
    newly_dead: list[str] = []
    recovered: list[str] = []
    moved: list[tuple[str, str]] = []
    evidence_failing: list[tuple[str, int]] = []
    for r in sorted(targets, key=lambda x: x.id):
        prev = _prev_entry(old_status, r.id)
        res = results.get(r.url)
        kind = classify(res)
        ev_fails = sum(1 for u in sorted({ev.url for ev in r.evidence}) if classify(results.get(u)) == "fail")
        entry = new_entry(prev, kind, res, ev_fails, today)
        if entry is None:
            continue
        new_status[r.id] = entry
        if ev_fails:
            evidence_failing.append((r.id, ev_fails))
        if kind == "fail":
            if entry["fails"] == 1:
                newly_failing.append(r.id)
        elif kind in ("ok", "moved") and prev.get("ok") is False:
            recovered.append(r.id)
        if kind == "moved" and entry.get("moved_to") and entry["moved_to"] != prev.get("moved_to"):
            moved.append((r.id, entry["moved_to"]))

    hidden_before = _hidden_ids(targets, old_status)
    hidden_after = _hidden_ids(targets, new_status)
    newly_hidden = sorted(hidden_after - hidden_before)
    newly_dead = list(newly_hidden)
    return Outcome(
        status=new_status, newly_failing=newly_failing, newly_dead=newly_dead, recovered=recovered, moved=moved,
        evidence_failing=evidence_failing, newly_hidden=newly_hidden, checked_resources=len(targets),
        checked_urls=len(results) + skipped, unreached_urls=sum(1 for v in results.values() if v is None),
        skipped_urls=skipped,
    )


def format_report(out: Outcome) -> str:
    """A short, deterministic table of what changed."""
    rows: list[tuple[str, str, str]] = []
    for rid in out.newly_failing:
        entry = out.status.get(rid, {})
        rows.append(("newly failing", rid, f"code {entry.get('code')}, fails 1"))
    for rid in out.newly_dead:
        entry = out.status.get(rid, {})
        rows.append(("newly dead", rid, f"code {entry.get('code')}, fails {entry.get('fails')}"))
    for rid in out.recovered:
        rows.append(("recovered", rid, "back to ok"))
    for rid, target in out.moved:
        rows.append(("moved", rid, target))
    for rid, n in out.evidence_failing:
        rows.append(("evidence failing", rid, f"{n} evidence link(s)"))
    lines = [f"Checked {out.checked_resources} resources, {out.checked_urls} urls"
             + (f", {out.unreached_urls} not reached before the time cap" if out.unreached_urls else "")
             + (f", {out.skipped_urls} skipped as unsafe" if out.skipped_urls else "") + "."]
    if not rows:
        lines.append("No changes.")
    else:
        w1 = max(len(r[0]) for r in rows)
        w2 = max(len(r[1]) for r in rows)
        lines.append(f"{'change'.ljust(w1)}  {'resource'.ljust(w2)}  detail")
        lines.extend(f"{a.ljust(w1)}  {b.ljust(w2)}  {c}" for a, b, c in rows)
    return "\n".join(lines)


def write_status(path: Path, status: dict) -> None:
    """Writes the file atomically with sorted keys so the diff is stable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(status, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Check the catalog links and update link_status.json.")
    p.add_argument("--dry-run", action="store_true", help="print the table and write nothing")
    p.add_argument("--only", action="append", default=[], metavar="ID", help="check only this resource id (repeatable)")
    p.add_argument("--catalog-dir", default=str(CATALOG_DIR), help="catalog directory (default: cvmax/resources/catalog)")
    p.add_argument("--status-file", default=str(LINK_STATUS_PATH), help="link status file to read and update")
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="seconds per request (default 10)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, *, fetcher: Fetcher | None = None,
         sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
         today: date | None = None, pause: float = HOST_PAUSE, time_cap_seconds: float | None = None,
         per_url_seconds: float = PER_URL_SECONDS, dns_check: DnsCheck | None = None,
         time_cap: float | None = None) -> int:
    args = parse_args(argv)
    if args.timeout <= 0:
        print("--timeout must be positive", file=sys.stderr)
        return EXIT_USAGE
    try:
        resources = load_catalog(args.catalog_dir)
    except CatalogError as e:
        print(f"Cannot read the catalog: {e}", file=sys.stderr)
        return EXIT_USAGE

    targets = [r for r in resources if r.status != "dead"]
    if args.only:
        wanted = set(args.only)
        unknown = sorted(wanted - {r.id for r in resources})
        if unknown:
            print(f"Unknown resource id(s): {', '.join(unknown)}", file=sys.stderr)
            return EXIT_USAGE
        targets = [r for r in targets if r.id in wanted]

    status_path = Path(args.status_file)
    old_status = load_link_status(status_path)
    day = (today or date.today()).isoformat()
    if fetcher is None:
        fetcher = functools.partial(fetch_url, dns_check=dns_check) if dns_check else fetch_url
    out = run_checks(targets, old_status, fetcher, today=day, timeout=args.timeout, sleep=sleep, clock=clock,
                     pause=pause, time_cap_seconds=time_cap_seconds, per_url_seconds=per_url_seconds,
                     time_cap=time_cap)
    print(format_report(out))

    # A manual --only run is deliberate, so the guard (a sanity check on a full run) does not apply to it.
    if out.guard_tripped and not args.only:
        print(f"ABORT: {len(out.newly_hidden)} of {out.checked_resources} resources "
              f"({out.hidden_share:.0%}) would become hidden, more than {HIDDEN_SHARE_LIMIT:.0%}. "
              "The checker or the network is probably broken; link_status.json was not written.", file=sys.stderr)
        return EXIT_GUARD

    if args.dry_run:
        print("Dry run: nothing written.")
        return EXIT_OK
    if out.status != old_status or not status_path.exists():
        write_status(status_path, out.status)
        print(f"Wrote {status_path}.")
    else:
        print("link_status.json unchanged.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
