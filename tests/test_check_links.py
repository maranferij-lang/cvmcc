"""Tests of the weekly link checker: classification, status bookkeeping, the 20 percent guard, redirects, the workflow,
the SSRF guards (unsafe urls, private addresses, credentials) and the hard time caps."""
from __future__ import annotations

import http.cookiejar
import json
import socket
import sys
import threading
import time
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.resources.catalog import load_catalog, load_link_status, visible_resources  # noqa: E402
from scripts import check_links as C  # noqa: E402
from scripts.check_links import FetchResult  # noqa: E402

TODAY = date(2026, 10, 12)
NEXT_WEEK = date(2026, 10, 19)


# --- helpers ---

def res_dict(i: int, status: str = "active", evidence: tuple[str, ...] | None = None) -> dict:
    ev = evidence if evidence is not None else (f"https://evidence{i}.example.org/proof",)
    return {
        "id": f"course-{i:02d}", "title": f"Course {i}", "provider": "Example", "url": f"https://site{i}.example.com/course",
        "kind": "course", "skills": ["sql"], "level": "beginner", "cost": "free", "price_usd": None, "hours": 10,
        "languages": ["en"], "hands_on": True, "output": "A project", "why_picked": "Short and practical.",
        "evidence": [{"claim": "Looked fine", "url": u, "seen": "2026-10-01"} for u in ev],
        "rating": None, "updated_year": 2026, "verified_at": "2026-10-01", "verification": "fetched", "status": status,
    }


def write_catalog(tmp_path: Path, n: int = 10, **kw) -> Path:
    d = tmp_path / "catalog"
    d.mkdir(exist_ok=True)
    (d / "data.json").write_text(json.dumps({"cluster": "data", "resources": [res_dict(i, **kw) for i in range(n)]}),
                                 encoding="utf-8")
    return d


class Fake:
    """A fake fetcher: url -> FetchResult | int | callable. Unlisted urls answer 200. Records every call."""

    def __init__(self, table: dict | None = None):
        self.table = dict(table or {})
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def __call__(self, url: str, timeout: float) -> FetchResult:
        with self._lock:
            self.calls.append(url)
        v = self.table.get(url, 200)
        if callable(v):
            v = v()
        return v if isinstance(v, FetchResult) else FetchResult(v)


def run(tmp_path: Path, fake: Fake, *extra: str, today: date = TODAY, catalog: Path | None = None) -> int:
    cat = catalog or tmp_path / "catalog"
    return C.main(["--catalog-dir", str(cat), "--status-file", str(tmp_path / "status.json"), *extra],
                  fetcher=fake, sleep=lambda s: None, today=today)


def status(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "status.json").read_text(encoding="utf-8"))


def site(i: int) -> str:
    return f"https://site{i}.example.com/course"


# --- classification ---

@pytest.mark.parametrize("code,expected", [
    (200, "ok"), (206, "ok"), (204, "ok"), (301, "ok"), (302, "ok"), (304, "ok"),
    (404, "fail"), (410, "fail"),
    (401, "unknown"), (403, "unknown"), (429, "unknown"), (400, "unknown"), (416, "unknown"),
    (500, "unknown"), (502, "unknown"), (503, "unknown"), (504, "unknown"),
])
def test_classify_codes(code, expected):
    assert C.classify(FetchResult(code)) == expected


def test_classify_errors():
    assert C.classify(FetchResult(None, error="connection")) == "fail"  # DNS or connect failure
    assert C.classify(FetchResult(None, error="timeout")) == "unknown"
    assert C.classify(FetchResult(None, error="ssl")) == "unknown"
    assert C.classify(FetchResult(None, error="insecure_redirect")) == "unknown"
    assert C.classify(FetchResult(None, error="too_many_redirects")) == "unknown"
    assert C.classify(FetchResult(301, moved_to="https://elsewhere.org/x")) == "moved"
    assert C.classify(None) == "unknown"


def test_registrable_domain():
    assert C.registrable_domain("www.coursera.org") == "coursera.org"
    assert C.registrable_domain("coursera.org") == "coursera.org"
    assert C.registrable_domain("learn.example.co.uk") == "example.co.uk"
    assert C.registrable_domain("prometheus.org.ua") == "prometheus.org.ua"
    assert C.registrable_domain("alice.github.io") == "alice.github.io"
    assert C.registrable_domain("Docs.Python.ORG.") == "python.org"


def test_user_agent_and_limits():
    assert C.USER_AGENT == "GetCVmaxLinkCheck/1.0 (+https://getcvmax.com)"
    assert C.MAX_REDIRECTS == 5 and C.MAX_PARALLEL_HOSTS == 4 and C.HOST_PAUSE == 1.0
    assert C.TIME_CAP_SECONDS == 20 * 60 and C.DEFAULT_TIMEOUT == 10.0 and C.PER_URL_SECONDS == 30.0


# --- status bookkeeping ---

def test_ok_writes_full_entry(tmp_path):
    write_catalog(tmp_path, 3)
    assert run(tmp_path, Fake()) == 0
    st = status(tmp_path)
    assert sorted(st) == ["course-00", "course-01", "course-02"]
    assert st["course-00"] == {"ok": True, "code": 200, "checked": "2026-10-12", "fails": 0, "moved_to": None,
                               "evidence_fails": 0}


def test_two_consecutive_fails_hide_the_resource(tmp_path):
    cat = write_catalog(tmp_path, 10)
    fake = Fake({site(3): 404})
    assert run(tmp_path, fake) == 0
    st = status(tmp_path)
    assert st["course-03"]["ok"] is False and st["course-03"]["fails"] == 1 and st["course-03"]["code"] == 404
    resources = load_catalog(cat)
    assert "course-03" in {r.id for r in visible_resources(resources, st)}  # one failure is not enough

    assert run(tmp_path, fake, today=NEXT_WEEK) == 0
    st = status(tmp_path)
    assert st["course-03"]["fails"] == 2
    assert "course-03" not in {r.id for r in visible_resources(resources, st)}
    assert len(visible_resources(resources, st)) == 9


def test_connection_failure_counts_as_fail(tmp_path):
    write_catalog(tmp_path, 10)
    assert run(tmp_path, Fake({site(1): FetchResult(None, error="connection")})) == 0
    entry = status(tmp_path)["course-01"]
    assert entry["ok"] is False and entry["code"] is None and entry["fails"] == 1


@pytest.mark.parametrize("code", [403, 429, 401, 500, 503])
def test_unknown_never_increments_fails(tmp_path, code):
    write_catalog(tmp_path, 10)
    for week in range(4):
        assert run(tmp_path, Fake({site(2): code}), today=date(2026, 10, 12 + 7 * week) if week < 3 else NEXT_WEEK) == 0
    # never any entry for it: nothing was ever learned
    assert "course-02" not in status(tmp_path)


def test_unknown_keeps_previous_values(tmp_path):
    write_catalog(tmp_path, 10)
    (tmp_path / "status.json").write_text(json.dumps({
        "course-04": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None, "evidence_fails": 0},
    }), encoding="utf-8")
    assert run(tmp_path, Fake({site(4): 403})) == 0
    entry = status(tmp_path)["course-04"]
    assert entry == {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None, "evidence_fails": 0}


def test_timeout_is_unknown(tmp_path):
    write_catalog(tmp_path, 10)
    (tmp_path / "status.json").write_text(json.dumps({
        "course-05": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None, "evidence_fails": 0},
    }), encoding="utf-8")
    assert run(tmp_path, Fake({site(5): FetchResult(None, error="timeout")})) == 0
    assert status(tmp_path)["course-05"]["fails"] == 1


def test_recovery_resets_fails(tmp_path):
    cat = write_catalog(tmp_path, 10)
    (tmp_path / "status.json").write_text(json.dumps({
        "course-06": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 2, "moved_to": None, "evidence_fails": 0},
    }), encoding="utf-8")
    resources = load_catalog(cat)
    assert "course-06" not in {r.id for r in visible_resources(resources, load_link_status(tmp_path / "status.json"))}
    assert run(tmp_path, Fake()) == 0
    st = status(tmp_path)
    assert st["course-06"]["ok"] is True and st["course-06"]["fails"] == 0 and st["course-06"]["checked"] == "2026-10-12"
    assert "course-06" in {r.id for r in visible_resources(resources, st)}


def test_report_lists_every_change_kind(tmp_path, capsys):
    write_catalog(tmp_path, 12)
    (tmp_path / "status.json").write_text(json.dumps({
        "course-01": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None, "evidence_fails": 0},
        "course-02": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 2, "moved_to": None, "evidence_fails": 0},
    }), encoding="utf-8")
    fake = Fake({
        site(0): 404,                                            # newly failing
        site(1): 404,                                            # newly dead
        site(3): FetchResult(301, moved_to="https://new.example.net/c"),  # moved
        "https://evidence4.example.org/proof": 404,              # evidence failing
    })
    assert run(tmp_path, fake) == 0
    out = capsys.readouterr().out
    for needle in ("newly failing", "course-00", "newly dead", "course-01", "recovered", "course-02", "moved",
                   "https://new.example.net/c", "evidence failing", "course-04"):
        assert needle in out
    assert "course-05" not in out  # unchanged resources are not listed


# --- guard ---

def test_guard_exits_3_and_leaves_file_untouched(tmp_path, capsys):
    write_catalog(tmp_path, 10)
    previous = {f"course-{i:02d}": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None,
                                    "evidence_fails": 0} for i in range(3)}
    path = tmp_path / "status.json"
    path.write_text(json.dumps(previous, indent=1), encoding="utf-8")
    before = path.read_bytes()
    fake = Fake({site(0): 404, site(1): 404, site(2): 404})  # 3 of 10 would become hidden = 30 percent
    assert run(tmp_path, fake) == 3
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    assert "ABORT" in capsys.readouterr().err


def test_guard_boundary_exactly_20_percent_passes(tmp_path):
    write_catalog(tmp_path, 10)
    previous = {f"course-{i:02d}": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None,
                                    "evidence_fails": 0} for i in range(2)}
    (tmp_path / "status.json").write_text(json.dumps(previous), encoding="utf-8")
    assert run(tmp_path, Fake({site(0): 404, site(1): 404})) == 0  # 2 of 10 = 20 percent, not more than
    assert status(tmp_path)["course-00"]["fails"] == 2


def test_guard_does_not_apply_to_first_failures(tmp_path):
    write_catalog(tmp_path, 10)
    fake = Fake({site(i): 404 for i in range(10)})  # a first failure hides nothing
    assert run(tmp_path, fake) == 0
    assert all(e["fails"] == 1 for e in status(tmp_path).values())


def test_guard_with_dry_run_still_exits_3_and_writes_nothing(tmp_path):
    write_catalog(tmp_path, 5)
    previous = {f"course-{i:02d}": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None,
                                    "evidence_fails": 0} for i in range(5)}
    path = tmp_path / "status.json"
    path.write_text(json.dumps(previous), encoding="utf-8")
    before = path.read_bytes()
    assert run(tmp_path, Fake({site(i): 404 for i in range(5)}), "--dry-run") == 3
    assert path.read_bytes() == before


def test_dry_run_writes_nothing(tmp_path):
    write_catalog(tmp_path, 3)
    assert run(tmp_path, Fake(), "--dry-run") == 0
    assert not (tmp_path / "status.json").exists()


# --- redirects and moved ---

def test_redirect_to_another_domain_is_recorded_as_moved(tmp_path):
    write_catalog(tmp_path, 10)
    target = "https://new-home.example.net/course"
    assert run(tmp_path, Fake({site(7): FetchResult(301, moved_to=target)})) == 0
    entry = status(tmp_path)["course-07"]
    assert entry["moved_to"] == target and entry["ok"] is True and entry["fails"] == 0 and entry["code"] == 301


def test_moved_does_not_count_as_failure_and_clears_when_resolved(tmp_path):
    write_catalog(tmp_path, 10)
    target = "https://new-home.example.net/course"
    run(tmp_path, Fake({site(7): FetchResult(301, moved_to=target)}))
    run(tmp_path, Fake(), today=NEXT_WEEK)
    entry = status(tmp_path)["course-07"]
    assert entry["moved_to"] is None and entry["fails"] == 0


class FakeResponse:
    def __init__(self, code, location=None):
        self.status_code = code
        self.headers = {"Location": location} if location else {}
        self.closed = False

    def iter_content(self, n):
        yield b"x"

    def close(self):
        self.closed = True


class FakeSession:
    script: dict = {}
    seen: list = []
    kwargs: list = []

    def __init__(self):
        self.trust_env = True

    def get(self, url, **kw):
        FakeSession.seen.append(url)
        FakeSession.kwargs.append(kw)
        v = FakeSession.script[url]
        if isinstance(v, Exception):
            raise v
        return FakeResponse(*v) if isinstance(v, tuple) else FakeResponse(v)

    def close(self):
        pass


@pytest.fixture
def fake_session(monkeypatch):
    import requests
    FakeSession.script, FakeSession.seen, FakeSession.kwargs = {}, [], []
    monkeypatch.setattr(requests, "Session", FakeSession)
    monkeypatch.setattr(C, "check_host_is_public", lambda host: None)  # no real DNS in tests
    return FakeSession


def test_fetch_url_request_shape(fake_session):
    fake_session.script = {"https://a.example.com/x": 200}
    assert C.fetch_url("https://a.example.com/x", 7) == FetchResult(200)
    kw = fake_session.kwargs[0]
    assert kw["timeout"] == 7 and kw["stream"] is True and kw["allow_redirects"] is False
    assert kw["headers"]["User-Agent"] == "GetCVmaxLinkCheck/1.0 (+https://getcvmax.com)"
    assert kw["headers"]["Range"].startswith("bytes=0-")


def test_fetch_url_follows_same_domain_redirects(fake_session):
    fake_session.script = {
        "https://example.com/a": (301, "https://www.example.com/b"),
        "https://www.example.com/b": (302, "/c"),
        "https://www.example.com/c": 200,
    }
    assert C.fetch_url("https://example.com/a") == FetchResult(200)
    assert fake_session.seen == ["https://example.com/a", "https://www.example.com/b", "https://www.example.com/c"]


def test_fetch_url_refuses_cross_domain_redirect(fake_session):
    fake_session.script = {"https://example.com/a": (301, "https://other.org/z"), "https://other.org/z": 200}
    got = C.fetch_url("https://example.com/a")
    assert got == FetchResult(301, moved_to="https://other.org/z")
    assert fake_session.seen == ["https://example.com/a"]  # never contacted the other domain


def test_fetch_url_refuses_http_downgrade(fake_session):
    fake_session.script = {"https://example.com/a": (301, "http://example.com/a")}
    got = C.fetch_url("https://example.com/a")
    assert got.error == "insecure_redirect" and C.classify(got) == "unknown"


def test_fetch_url_gives_up_after_five_redirects(fake_session):
    fake_session.script = {f"https://example.com/{i}": (302, f"https://example.com/{i + 1}") for i in range(10)}
    got = C.fetch_url("https://example.com/0")
    assert got.error == "too_many_redirects"
    assert len(fake_session.seen) == C.MAX_REDIRECTS + 1


def test_fetch_url_maps_exceptions(fake_session):
    import requests
    fake_session.script = {
        "https://t.example.com/": requests.exceptions.ReadTimeout(),
        "https://c.example.com/": requests.exceptions.ConnectionError(),
        "https://s.example.com/": requests.exceptions.SSLError(),
        "https://o.example.com/": requests.exceptions.InvalidURL(),
    }
    assert C.fetch_url("https://t.example.com/").error == "timeout"
    assert C.fetch_url("https://c.example.com/").error == "connection"
    assert C.fetch_url("https://s.example.com/").error == "ssl"
    assert C.fetch_url("https://o.example.com/").error == "other"
    assert C.fetch_url("http://plain.example.com/").error == "insecure_redirect"


# --- SSRF guards ---

UNSAFE_URLS = [
    "https://127.1/", "https://0177.0.0.1/", "https://localhost/", "https://metadata.google.internal/",
    "https://[::1]/", "https://[::ffff:127.0.0.1]/", "https://10.0.0.5/", "http://plain.example.com/",
    "https://user:pw@example.com/",
]


@pytest.mark.parametrize("ip,public", [
    ("8.8.8.8", True), ("93.184.216.34", True), ("2001:4860:4860::8888", True),
    ("127.0.0.1", False), ("127.1.2.3", False), ("10.1.2.3", False), ("172.16.0.1", False), ("192.168.1.1", False),
    ("169.254.169.254", False), ("100.64.0.1", False), ("0.0.0.0", False), ("224.0.0.1", False),
    ("240.0.0.1", False), ("255.255.255.255", False), ("192.0.2.1", False),
    ("::1", False), ("::", False), ("fe80::1", False), ("fe80::1%eth0", False), ("fc00::1", False),
    ("fd00:ec2::254", False), ("ff02::1", False), ("fec0::1", False),
    ("::ffff:127.0.0.1", False), ("::ffff:10.0.0.1", False), ("::ffff:169.254.169.254", False),
    ("::ffff:8.8.8.8", True), ("::127.0.0.1", False), ("64:ff9b::7f00:1", False),
    ("2002:7f00:1::", False), ("not-an-ip", False), ("", False),
])
def test_is_public_address(ip, public):
    assert C.is_public_address(ip) is public


def test_check_host_is_public_uses_getaddrinfo(monkeypatch):
    def answer(*ips):
        return lambda host, port, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in ips]

    monkeypatch.setattr(socket, "getaddrinfo", answer("93.184.216.34"))
    assert C.check_host_is_public("example.com") is None
    for bad in ("127.0.0.1", "::1", "::ffff:7f00:1", "169.254.169.254"):
        monkeypatch.setattr(socket, "getaddrinfo", answer("93.184.216.34", bad))  # one bad answer is enough
        with pytest.raises(C.UnsafeAddress):
            C.check_host_is_public("rebind.example.com")

    def nxdomain(*a, **kw):
        raise socket.gaierror(socket.EAI_NONAME, "nope")

    monkeypatch.setattr(socket, "getaddrinfo", nxdomain)
    with pytest.raises(socket.gaierror):
        C.check_host_is_public("nx.example.com")


def test_check_urls_never_fetches_unsafe_urls(capsys):
    fake = Fake()
    good = "https://ok.example.com/x"
    out = C.check_urls([*UNSAFE_URLS, good], fake, sleep=lambda s: None)
    assert fake.calls == [good]
    assert set(out) == {good}
    printed = capsys.readouterr().out
    for u in ("127.1", "0177.0.0.1", "localhost", "metadata.google.internal"):
        assert u in printed and "Skipped" in printed


def test_unsafe_catalog_and_evidence_urls_are_unknown_and_not_fetched(tmp_path, capsys):
    d = tmp_path / "catalog"
    d.mkdir()
    bad_main = res_dict(0)
    bad_main["url"] = "https://127.1/"
    bad_evidence = res_dict(1, evidence=("https://0177.0.0.1/", "https://localhost/", "https://metadata.google.internal/",
                                         "https://evidence1.example.org/proof"))
    (d / "x.json").write_text(json.dumps({"cluster": "data", "resources": [bad_main, bad_evidence]}), encoding="utf-8")
    fake = Fake()
    assert run(tmp_path, fake) == 0
    assert sorted(fake.calls) == sorted(["https://evidence0.example.org/proof", site(1),
                                         "https://evidence1.example.org/proof"])
    st = status(tmp_path)
    assert "course-00" not in st  # the unsafe main url is unknown with no history: nothing stored
    assert st["course-01"]["ok"] is True and st["course-01"]["evidence_fails"] == 0
    out = capsys.readouterr().out
    assert "skipped as unsafe" in out and "Skipped, not fetched" in out


def test_unsafe_url_keeps_previous_status(tmp_path):
    d = tmp_path / "catalog"
    d.mkdir()
    item = res_dict(0)
    item["url"] = "https://localhost/"
    (d / "x.json").write_text(json.dumps({"cluster": "data", "resources": [item]}), encoding="utf-8")
    prev = {"course-00": {"ok": False, "code": 404, "checked": "2026-10-05", "fails": 1, "moved_to": None,
                          "evidence_fails": 0}}
    (tmp_path / "status.json").write_text(json.dumps(prev), encoding="utf-8")
    assert run(tmp_path, Fake()) == 0
    assert status(tmp_path) == prev


def test_fetch_url_refuses_a_host_that_resolves_to_a_private_address(fake_session):
    fake_session.script = {"https://internal.example.com/": 200}

    def check(host):
        raise C.UnsafeAddress(host)

    got = C.fetch_url("https://internal.example.com/", dns_check=check)
    assert got.error == "blocked_address" and C.classify(got) == "unknown"
    assert fake_session.seen == []  # no request was sent


def test_fetch_url_checks_dns_for_every_redirect_hop(fake_session):
    fake_session.script = {
        "https://www.example.com/a": (302, "https://internal.example.com/b"),
        "https://internal.example.com/b": 200,
    }
    asked: list[str] = []

    def check(host):
        asked.append(host)
        if host.startswith("internal"):
            raise C.UnsafeAddress(host)

    got = C.fetch_url("https://www.example.com/a", dns_check=check)
    assert got.error == "blocked_address"
    assert asked == ["www.example.com", "internal.example.com"]
    assert fake_session.seen == ["https://www.example.com/a"]  # the internal hop was never requested


def test_fetch_url_dns_check_runs_before_each_allowed_hop(fake_session):
    fake_session.script = {"https://example.com/a": (301, "https://www.example.com/b"), "https://www.example.com/b": 200}
    asked: list[str] = []
    assert C.fetch_url("https://example.com/a", dns_check=asked.append) == FetchResult(200)
    assert asked == ["example.com", "www.example.com"]


def test_fetch_url_refuses_redirect_to_an_ip_literal_or_local_name(fake_session):
    for target in ("https://127.0.0.1/x", "https://localhost/x", "https://[::1]/x"):
        fake_session.script = {"https://localhost.example.com/a": (302, target)}
        fake_session.seen.clear()
        # these are other domains, so they are reported as moved and never contacted
        got = C.fetch_url("https://localhost.example.com/a", dns_check=lambda h: None)
        assert fake_session.seen == ["https://localhost.example.com/a"]
        assert got.moved_to == target
    # a same-domain hop to an unsafe name is blocked by the url rules
    fake_session.script = {"https://example.com/a": (302, "https://example.com:bad/")}
    got = C.fetch_url("https://example.com/a", dns_check=lambda h: None)
    assert got.error in ("other", "blocked_address") and got.code is None


def test_fetch_url_unsafe_start_url_is_blocked_without_dns_or_request(fake_session):
    asked: list[str] = []
    for u in ("https://127.1/", "https://0177.0.0.1/", "https://localhost/", "https://metadata.google.internal/"):
        got = C.fetch_url(u, dns_check=asked.append)
        assert got.error == "blocked_address"
    assert asked == [] and fake_session.seen == []


def test_dns_resolution_failure_is_a_connection_failure(fake_session):
    def nxdomain(host):
        raise socket.gaierror(socket.EAI_NONAME, "nope")

    got = C.fetch_url("https://gone.example.com/", dns_check=nxdomain)
    assert got.error == "connection" and C.classify(got) == "fail"
    assert fake_session.seen == []


def test_main_uses_the_injected_dns_check(tmp_path, fake_session):
    write_catalog(tmp_path, 3)
    fake_session.script = {site(0): 200, site(1): 200, site(2): 200}
    for i in range(3):
        fake_session.script[f"https://evidence{i}.example.org/proof"] = 200

    def check(host):
        if host == "site1.example.com":
            raise C.UnsafeAddress(host)

    code = C.main(["--catalog-dir", str(tmp_path / "catalog"), "--status-file", str(tmp_path / "status.json")],
                  sleep=lambda s: None, today=TODAY, dns_check=check)
    assert code == 0
    assert site(1) not in fake_session.seen and site(0) in fake_session.seen
    assert "course-01" not in status(tmp_path)  # blocked = unknown, nothing stored


def test_fetch_url_sends_no_credentials_and_no_cookies(fake_session):
    import requests

    jar = requests.cookies.RequestsCookieJar()
    fake_session.cookies = jar
    try:
        fake_session.script = {"https://example.com/a": 200}
        C.fetch_url("https://example.com/a")
        kw = fake_session.kwargs[0]
        assert set(kw["headers"]) == {"User-Agent", "Range", "Accept"}  # no Cookie, no Authorization
        assert "cookies" not in kw and "auth" in kw and callable(kw["auth"])  # a callable auth disables netrc
        marker = object()
        assert kw["auth"](marker) is marker

        class Headers:
            def get_all(self, name, default=None):
                return ["sid=abc; Path=/; Domain=example.com"] if name.lower() == "set-cookie" else default

        class Resp:
            def info(self):
                return Headers()

        from urllib.request import Request
        jar.extract_cookies(Resp(), Request("https://example.com/a"))
        assert len(jar) == 0  # the policy accepts no cookie, so none is stored and none can be sent back
    finally:
        del fake_session.cookies


def test_fetch_url_never_follows_a_redirect_to_http(fake_session):
    fake_session.script = {"https://example.com/a": (307, "http://example.com/b"), "http://example.com/b": 200}
    got = C.fetch_url("https://example.com/a")
    assert got.error == "insecure_redirect" and fake_session.seen == ["https://example.com/a"]


# --- hard time caps ---

class DripResponse(FakeResponse):
    """A body that never ends: every byte takes `delay` seconds on the injected clock."""

    def __init__(self, clock_box, delay):
        super().__init__(200)
        self.clock_box, self.delay, self.reads = clock_box, delay, 0

    def iter_content(self, n):
        while True:
            self.clock_box[0] += self.delay
            self.reads += 1
            yield b"x"


def test_fetch_url_budget_stops_a_dripping_body(fake_session, monkeypatch):
    now = [0.0]
    resp = DripResponse(now, 2.0)
    monkeypatch.setattr(FakeSession, "get", lambda self, url, **kw: resp)
    got = C.fetch_url("https://example.com/a", 10, budget=5.0, clock=lambda: now[0])
    assert got.error == "timeout" and C.classify(got) == "unknown"
    assert resp.reads <= 4 and resp.closed  # stopped right after the budget, not after the whole body


def test_fetch_url_budget_real_clock_drip(fake_session, monkeypatch):
    class Slow(FakeResponse):
        def iter_content(self, n):
            while True:
                time.sleep(0.05)
                yield b"x"

    monkeypatch.setattr(FakeSession, "get", lambda self, url, **kw: Slow(200))
    t0 = time.monotonic()
    got = C.fetch_url("https://example.com/a", 10, budget=0.3)
    assert got.error == "timeout" and time.monotonic() - t0 < 1.5


def test_fetch_url_request_timeout_is_capped_by_the_remaining_budget(fake_session):
    fake_session.script = {"https://example.com/a": 200}
    C.fetch_url("https://example.com/a", 10, budget=2.0)
    assert 0 < fake_session.kwargs[0]["timeout"] <= 2.0


def test_fetch_url_reads_a_small_body_in_chunks(fake_session):
    fake_session.script = {"https://example.com/a": 200}
    assert C.fetch_url("https://example.com/a") == FetchResult(200)
    assert C.READ_CHUNK <= 16 and C.READ_BYTES <= 4096


def test_hard_cap_returns_while_a_fetch_is_still_running():
    release = threading.Event()
    fake = Fake({"https://a.example.com/x": lambda: (release.wait(10), FetchResult(200))[1]})
    urls = ["https://a.example.com/x", "https://b.example.com/x", "https://c.example.com/x"]
    t0 = time.monotonic()
    try:
        out = C.check_urls(urls, fake, sleep=lambda s: None, time_cap_seconds=0.3, max_hosts=1)
        elapsed = time.monotonic() - t0
    finally:
        release.set()
    assert elapsed < 2.0
    assert fake.calls == ["https://a.example.com/x"]  # queued hosts were cancelled, never started
    assert out == {u: None for u in urls}  # everything unreached is unknown
    time.sleep(0.1)
    assert out == {u: None for u in urls}  # the late result of the abandoned fetch is not written back


def test_hard_cap_repro_slow_fetcher_with_half_second_cap():
    def slow(url, timeout):
        time.sleep(2.0)
        return FetchResult(200)

    t0 = time.monotonic()
    out = C.check_urls([f"https://h{i}.example.com/" for i in range(3)], slow, sleep=lambda s: None, time_cap=0.5)
    assert time.monotonic() - t0 < 1.5
    assert all(v is None for v in out.values())


def test_hard_cap_keeps_results_that_finished_in_time():
    fake = Fake({"https://slow.example.com/x": lambda: (time.sleep(1.0), FetchResult(200))[1]})
    out = C.check_urls(["https://fast.example.com/x", "https://slow.example.com/x"], fake, sleep=lambda s: None,
                       time_cap_seconds=0.3)
    assert out["https://fast.example.com/x"] == FetchResult(200)
    assert out["https://slow.example.com/x"] is None


def test_check_urls_gives_each_fetch_a_budget_of_min_remaining_and_per_url():
    seen: list[float] = []

    def aware(url, timeout, budget=None):
        seen.append(budget)
        return FetchResult(200)

    C.check_urls(["https://a.example.com/x"], aware, sleep=lambda s: None, time_cap_seconds=1000, per_url_seconds=7)
    C.check_urls(["https://a.example.com/x"], aware, sleep=lambda s: None, time_cap_seconds=3, per_url_seconds=30)
    assert seen[0] == pytest.approx(7, abs=0.5) and 0 < seen[1] <= 3
    # a plain two-argument fake keeps working
    assert C.check_urls(["https://a.example.com/x"], Fake(), sleep=lambda s: None) == {
        "https://a.example.com/x": FetchResult(200)}


def test_main_passes_the_caps_through(tmp_path):
    write_catalog(tmp_path, 2)
    budgets: list[float] = []

    def aware(url, timeout, budget=None):
        budgets.append(budget)
        return FetchResult(200)

    assert C.main(["--catalog-dir", str(tmp_path / "catalog"), "--status-file", str(tmp_path / "s.json")],
                  fetcher=aware, sleep=lambda s: None, today=TODAY, time_cap_seconds=50, per_url_seconds=4) == 0
    assert budgets and all(b <= 4.0 + 1e-6 for b in budgets)


def test_unreached_urls_after_the_cap_are_unknown_and_exit_code_is_unchanged(tmp_path):
    write_catalog(tmp_path, 3)
    release = threading.Event()
    fake = Fake({site(0): lambda: (release.wait(5), FetchResult(404))[1]})
    try:
        code = run_with_caps(tmp_path, fake, time_cap_seconds=0.3)
    finally:
        release.set()
    assert code == 0
    st = status(tmp_path)
    assert "course-00" not in st  # unreached = unknown, not fail


def run_with_caps(tmp_path: Path, fake, **caps) -> int:
    return C.main(["--catalog-dir", str(tmp_path / "catalog"), "--status-file", str(tmp_path / "status.json")],
                  fetcher=fake, sleep=lambda s: None, today=TODAY, **caps)


# --- evidence ---

def test_evidence_failures_are_counted_but_do_not_hide(tmp_path):
    cat = write_catalog(tmp_path, 10)
    bad = "https://evidence8.example.org/proof"
    fake = Fake({bad: 404})
    for week in range(3):
        assert run(tmp_path, fake, today=date(2026, 10, 12 + 7 * week)) == 0
    entry = status(tmp_path)["course-08"]
    assert entry["evidence_fails"] == 1 and entry["fails"] == 0 and entry["ok"] is True
    assert "course-08" in {r.id for r in visible_resources(load_catalog(cat), status(tmp_path))}


def test_evidence_403_is_not_counted_and_each_url_checked_once(tmp_path):
    shared = "https://shared.example.org/proof"
    d = tmp_path / "catalog"
    d.mkdir()
    items = [res_dict(0, evidence=(shared, "https://e.example.org/a")), res_dict(1, evidence=(shared,))]
    (d / "x.json").write_text(json.dumps({"cluster": "data", "resources": items}), encoding="utf-8")
    fake = Fake({shared: 403, "https://e.example.org/a": 410})
    assert run(tmp_path, fake) == 0
    st = status(tmp_path)
    assert st["course-00"]["evidence_fails"] == 1 and st["course-01"]["evidence_fails"] == 0
    assert fake.calls.count(shared) == 1


# --- ordering, quiet weeks, politeness ---

def test_output_is_deterministic_and_sorted(tmp_path):
    cat = write_catalog(tmp_path, 10)
    fake = Fake({site(5): 404, site(2): 404})
    run(tmp_path, fake)
    first = (tmp_path / "status.json").read_bytes()
    keys = list(json.loads(first))
    assert keys == sorted(keys)
    (tmp_path / "status.json").unlink()
    items = json.loads((cat / "data.json").read_text(encoding="utf-8"))
    items["resources"].reverse()
    (cat / "data.json").write_text(json.dumps(items), encoding="utf-8")
    run(tmp_path, Fake({site(5): 404, site(2): 404}))
    assert (tmp_path / "status.json").read_bytes() == first
    assert first.endswith(b"\n")


def test_quiet_week_keeps_file_byte_identical(tmp_path):
    write_catalog(tmp_path, 4)
    run(tmp_path, Fake())
    before = (tmp_path / "status.json").read_bytes()
    assert run(tmp_path, Fake(), today=NEXT_WEEK) == 0
    assert (tmp_path / "status.json").read_bytes() == before  # no new date, so no diff and no PR


def test_dead_resources_are_skipped_but_needs_check_is_checked(tmp_path):
    d = tmp_path / "catalog"
    d.mkdir()
    items = [res_dict(0, status="dead"), res_dict(1, status="needs_check"), res_dict(2)]
    (d / "x.json").write_text(json.dumps({"cluster": "data", "resources": items}), encoding="utf-8")
    fake = Fake()
    assert run(tmp_path, fake) == 0
    assert site(0) not in fake.calls and site(1) in fake.calls and site(2) in fake.calls
    assert sorted(status(tmp_path)) == ["course-01", "course-02"]


def test_only_checks_one_resource_and_keeps_the_rest(tmp_path):
    write_catalog(tmp_path, 4)
    (tmp_path / "status.json").write_text(json.dumps({"other": {"ok": True, "code": 200, "checked": "2026-01-01",
                                                               "fails": 0}}), encoding="utf-8")
    fake = Fake()
    assert run(tmp_path, fake, "--only", "course-02") == 0
    assert set(fake.calls) == {site(2), "https://evidence2.example.org/proof"}
    st = status(tmp_path)
    assert set(st) == {"other", "course-02"}


def test_only_with_unknown_id_exits_2(tmp_path):
    write_catalog(tmp_path, 2)
    assert run(tmp_path, Fake(), "--only", "nope") == 2


def test_broken_catalog_exits_2(tmp_path):
    d = tmp_path / "catalog"
    d.mkdir()
    (d / "bad.json").write_text("{not json", encoding="utf-8")
    assert run(tmp_path, Fake()) == 2


def test_fetcher_exception_is_unknown(tmp_path):
    write_catalog(tmp_path, 10)

    def boom():
        raise RuntimeError("boom")

    assert run(tmp_path, Fake({site(1): boom})) == 0
    assert "course-01" not in status(tmp_path)


def test_pause_between_requests_to_the_same_host(tmp_path):
    d = tmp_path / "catalog"
    d.mkdir()
    items = [res_dict(0, evidence=("https://site0.example.com/e1", "https://site0.example.com/e2"))]
    (d / "x.json").write_text(json.dumps({"cluster": "data", "resources": items}), encoding="utf-8")
    sleeps: list[float] = []
    C.main(["--catalog-dir", str(d), "--status-file", str(tmp_path / "s.json")], fetcher=Fake(),
           sleep=sleeps.append, today=TODAY)
    assert sleeps == [1.0, 1.0]  # three urls on one host: two pauses


def test_at_most_four_hosts_in_parallel():
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fetcher(url, timeout):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(0.02)
        with lock:
            live["now"] -= 1
        return FetchResult(200)

    urls = [f"https://host{i}.example.com/x" for i in range(12)]
    out = C.check_urls(urls, fetcher, sleep=lambda s: None)
    assert len(out) == 12 and all(v == FetchResult(200) for v in out.values())
    assert 1 < live["max"] <= 4


def test_sequential_per_host():
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    def fetcher(url, timeout):
        with lock:
            live["now"] += 1
            live["max"] = max(live["max"], live["now"])
        time.sleep(0.01)
        with lock:
            live["now"] -= 1
        return FetchResult(200)

    C.check_urls([f"https://one.example.com/{i}" for i in range(5)], fetcher, sleep=lambda s: None)
    assert live["max"] == 1


def test_time_cap_leaves_the_rest_unknown():
    ticks = iter(range(0, 10_000, 100))  # every clock() call is 100 s later
    fake = Fake()
    out = C.check_urls([f"https://one.example.com/{i}" for i in range(20)], fake, sleep=lambda s: None,
                       clock=lambda: float(next(ticks)), time_cap=1200)
    assert 0 < len(fake.calls) < 20
    assert sum(1 for v in out.values() if v is None) == 20 - len(fake.calls)
    assert C.classify(None) == "unknown"


def test_cli_flags_exist():
    a = C.parse_args(["--dry-run", "--only", "a", "--only", "b", "--catalog-dir", "c", "--status-file", "s",
                      "--timeout", "3"])
    assert a.dry_run and a.only == ["a", "b"] and a.catalog_dir == "c" and a.status_file == "s" and a.timeout == 3


def test_committed_status_file_is_valid_json_object():
    data = json.loads((ROOT / "cvmax" / "resources" / "link_status.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)


# --- workflow ---

WORKFLOW = ROOT / ".github" / "workflows" / "resources.yml"


def test_workflow_structure():
    text = WORKFLOW.read_text(encoding="utf-8")
    try:
        import yaml
    except ImportError:
        yaml = None
    if yaml is None:
        for needle in ('cron: "0 5 * * 1"', "workflow_dispatch", "check:", "pr:", "contents: read", "contents: write",
                       "pull-requests: write", "peter-evans/create-pull-request@v7",
                       "secrets.LEARN_PR_TOKEN || secrets.GITHUB_TOKEN", "add-paths", "scripts/check_links.py",
                       "requirements-dev.txt", "upload-artifact", "download-artifact"):
            assert needle in text
        assert "gh pr merge" not in text and "auto-merge" not in text.lower().replace("no auto-merge", "")
        return
    wf = yaml.safe_load(text)
    on = wf.get("on", wf.get(True))
    assert on["schedule"] == [{"cron": "0 5 * * 1"}]
    assert "workflow_dispatch" in on
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    assert set(jobs) == {"check", "pr"}
    check, pr = jobs["check"], jobs["pr"]
    assert check["permissions"] == {"contents": "read"}
    assert pr["permissions"] == {"contents": "write", "pull-requests": "write"}
    assert pr["needs"] == "check"
    check_steps = " ".join(str(s) for s in check["steps"])
    assert "requirements-dev.txt" in check_steps and "scripts/check_links.py" in check_steps
    upload = next(s for s in check["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact"))
    assert upload["with"]["path"] == "cvmax/resources/link_status.json"
    assert any(str(s.get("uses", "")).startswith("actions/download-artifact") for s in pr["steps"])
    create = next(s for s in pr["steps"] if str(s.get("uses", "")).startswith("peter-evans/create-pull-request"))
    assert create["with"]["token"] == "${{ secrets.LEARN_PR_TOKEN || secrets.GITHUB_TOKEN }}"
    assert create["with"]["add-paths"].split() == ["cvmax/resources/link_status.json"]
    assert "gh pr merge" not in text and "auto-merge" not in text.lower().replace("no auto-merge", "")
    # the write job runs no repository code
    assert "scripts/" not in " ".join(str(s.get("run", "")) for s in pr["steps"])
