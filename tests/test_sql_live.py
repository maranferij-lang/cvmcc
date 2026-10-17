"""Runs the real Supabase SQL (supabase/schema.sql and the apply-*.sql files) in a throwaway local Postgres 16.

Skipped when the Postgres 16 binaries are missing. The cluster lives in a temp dir, listens on 127.0.0.1 only on a
free port, and is removed at the end. Supabase is emulated with the roles anon / authenticated / service_role and the
schema `extensions` (pgcrypto). Never touches a live database. Tokens here are random per run and never printed.
"""
from __future__ import annotations

import json
import os
import pwd
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

PG_BIN = Path(os.environ.get("CVMAX_PG_BIN", "/usr/lib/postgresql/16/bin"))
PSQL = shutil.which("psql") or "/usr/bin/psql"
SUPABASE = Path(__file__).resolve().parents[1] / "supabase"
SQL_FILES = ["schema.sql", "apply-now.sql", "apply-judge.sql", "apply-billing.sql"]

_have_pg = all((PG_BIN / b).exists() for b in ("initdb", "pg_ctl", "postgres")) and Path(PSQL).exists()
pytestmark = pytest.mark.skipif(not _have_pg, reason="Postgres 16 binaries (initdb, pg_ctl, postgres, psql) not found")

TABLES = ["cvmax_users", "cvmax_usage", "cvmax_results", "cvmax_edit_feedback", "cvmax_feedback", "cvmax_waitlist",
          "cvmax_events", "cvmax_vacancies", "cvmax_skill_demand", "cvmax_training_samples", "cvmax_purchases",
          "cvmax_credit_use"]


ROLES_SQL = "create role anon nologin; create role authenticated nologin; create role service_role nologin bypassrls;"
DB_SQL = """
    create schema extensions;
    grant usage on schema public, extensions to anon, authenticated, service_role;
    alter default privileges in schema public grant all on tables to anon, authenticated, service_role;
    alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
    alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
    create extension pgcrypto with schema extensions;
"""


class SqlError(Exception):
    pass


class PG:
    def __init__(self, port: int, tokens: dict[str, str]):
        self.port = port
        self.tokens = tokens

    def run(self, sql: str, role: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        script = (f"set role {role};\n" if role else "") + sql
        proc = subprocess.run(
            [PSQL, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p", str(self.port),
             "-U", "postgres", "-d", "cvmax"],
            input=script, capture_output=True, text=True, timeout=120)
        if check and proc.returncode != 0:
            raise SqlError(proc.stderr.strip())
        return proc

    def val(self, sql: str, role: str | None = None) -> str:
        out = self.run(sql, role).stdout.strip().splitlines()
        return out[-1] if out else ""

    def js(self, sql: str, role: str | None = "anon"):
        return json.loads(self.val(sql, role))

    def fails(self, sql: str, role: str | None = "anon") -> str:
        proc = self.run(sql, role, check=False)
        assert proc.returncode != 0, f"expected an error, got: {proc.stdout}"
        return proc.stderr

    # --- helpers for the app-facing functions, always as role anon ---
    def call(self, fn: str, token: str, *args) -> object:
        lits = ", ".join("null" if a is None else (str(a) if isinstance(a, int) else "'" + str(a).replace("'", "''") + "'")
                         for a in args)
        return self.js(f"select public.{fn}('{self.tokens[token]}'{', ' if lits else ''}{lits});")

    def grant(self, user, plan, txn, token="bill"):
        return self.call("cvmax_grant_purchase", token, user, plan, txn)

    def use(self, user, aid, role="Data Analyst", purpose="review"):
        return self.call("cvmax_use_credit", "app", user, aid, role, purpose)

    def credits(self, user):
        return self.call("cvmax_credits", "app", user)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def pg():
    tmp = Path(tempfile.mkdtemp(prefix="cvmax-pg-"))
    run_user = None
    if os.geteuid() == 0:
        for name in ("postgres", "nobody"):
            try:
                pwd.getpwnam(name)
                run_user = name
                break
            except KeyError:
                continue
        if run_user is None:
            subprocess.run(["useradd", "--system", "--no-create-home", "cvmax-pg"], check=True)
            run_user = "cvmax-pg"
        shutil.chown(tmp, run_user)
    tmp.chmod(0o700)
    data = tmp / "data"
    port = _free_port()
    kw = {"user": run_user} if run_user else {}
    env = {"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8", "HOME": str(tmp)}
    started = False
    try:
        subprocess.run([str(PG_BIN / "initdb"), "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
                       check=True, capture_output=True, env=env, **kw)
        opts = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories={tmp} -c fsync=off"
        subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(tmp / "log"), "-o", opts, "-w", "start"],
                       check=True, capture_output=True, env=env, **kw)
        started = True
        tokens = {"app": "cvmx_" + secrets.token_urlsafe(24), "learn": "cvmx_" + secrets.token_urlsafe(24),
                  "bill": "cvmx_bill_" + secrets.token_hex(24)}
        db = PG(port, tokens)
        subprocess.run([PSQL, "-X", "-q", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres", "-d", "postgres",
                        "-c", "create database cvmax"], check=True, capture_output=True)
        # Supabase emulation: API roles, the extensions schema with pgcrypto, default grants to the API roles.
        db.run(ROLES_SQL)
        db.run(DB_SQL)
        for name in SQL_FILES:
            for _ in range(2):  # every file must apply, and apply again
                db.run((SUPABASE / name).read_text())
            if name == "schema.sql":
                for scope, i, key in (("app", 1, "app"), ("learning", 2, "learn")):
                    db.run(f"insert into cvmax_private.app_config (id, scope, token_hash) values "
                           f"({i}, '{scope}', encode(extensions.digest('{tokens[key]}', 'sha256'), 'hex')) "
                           f"on conflict (scope) do update set token_hash = excluded.token_hash;")
        # apply-billing.sql prints a random billing token and stores its hash; replace it with ours.
        db.run(f"insert into cvmax_private.app_config (id, scope, token_hash) values "
               f"(3, 'billing', encode(extensions.digest('{tokens['bill']}', 'sha256'), 'hex')) "
               f"on conflict (scope) do update set token_hash = excluded.token_hash;")
        yield db
    finally:
        if started:
            subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                           capture_output=True, env=env, **kw)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean(pg):
    pg.run("truncate " + ", ".join("public." + t for t in TABLES) + " restart identity cascade;")


def _backdate_purchase(pg, txn, months):
    pg.run(f"update public.cvmax_purchases set created_at = now() - interval '{months} months' "
           f"where transaction_id = '{txn}';")


# ---------------------------------------------------------------- setup

def test_files_apply_and_functions_exist(pg):
    n = pg.val("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
               "where n.nspname = 'public' and p.proname like 'cvmax_%';")
    assert int(n) >= 27
    assert pg.val("select count(*) from cvmax_private.app_config;") == "3"
    # all public functions are security definer with an empty search_path
    bad = pg.val("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                 "where n.nspname = 'public' and p.proname like 'cvmax_%' and "
                 "(not p.prosecdef or p.proconfig is null or not 'search_path=\"\"' = any(p.proconfig));")
    assert bad == "0"


def test_anon_cannot_touch_tables(pg):
    for t in TABLES:
        assert "permission denied" in pg.fails(f"select * from public.{t};")
    assert "permission denied" in pg.fails("select * from cvmax_private.app_config;")
    assert "permission denied" in pg.fails("insert into public.cvmax_purchases (user_key, plan, transaction_id, "
                                           "full_credits) values ('a@x.example', 'full', 't1', 1);")


def test_wrong_or_missing_token_rejected(pg):
    assert "unauthorized" in pg.fails("select public.cvmax_credits('nope', 'a@x.example');")
    assert "unauthorized" in pg.fails("select public.cvmax_credits(null, 'a@x.example');")


# ---------------------------------------------------------------- grants

def test_grant_full_is_idempotent_on_transaction_id(pg):
    assert pg.grant("A@x.example", "full", "txn_one") == {"granted": True, "plan": "full", "full_credits": 1}
    assert pg.grant("a@x.example", "full", "txn_one") == {"granted": False, "reason": "duplicate"}
    assert pg.credits("a@x.example")["full"] == 1
    assert pg.val("select count(*) from public.cvmax_purchases;") == "1"


def test_hunt_grants_five_and_has_hunt(pg):
    assert pg.grant("a@x.example", "hunt", "txn_h")["full_credits"] == 5
    c = pg.credits("a@x.example")
    assert c["full"] == 5 and c["has_hunt"] is True
    pg.grant("b@x.example", "full", "txn_f")
    assert pg.credits("b@x.example")["has_hunt"] is False


def test_grant_rejects_bad_input(pg):
    assert "bad plan" in pg.fails(f"select public.cvmax_grant_purchase('{pg.tokens['bill']}', 'a@x.example', 'scan', 't');")
    assert "bad user key" in pg.fails(f"select public.cvmax_grant_purchase('{pg.tokens['bill']}', '  ', 'full', 't');")
    assert "bad transaction id" in pg.fails(f"select public.cvmax_grant_purchase('{pg.tokens['bill']}', 'a@x', 'full', '');")


# ---------------------------------------------------------------- credits

def test_use_credit_spends_once_per_analysis_id(pg):
    pg.grant("a@x.example", "hunt", "txn_h")
    assert pg.use("a@x.example", "an1", "Data Analyst") == {"allowed": True, "mode": "credit", "duplicate": False}
    assert pg.use("a@x.example", "an1", "Data Analyst") == {"allowed": True, "mode": "credit", "duplicate": True}
    assert pg.credits("a@x.example")["full"] == 4
    assert pg.val("select count(*) from public.cvmax_credit_use;") == "1"


def test_no_credit_and_foreign_analysis_conflict(pg):
    assert pg.use("a@x.example", "an1") == {"allowed": False, "reason": "no_credit"}
    pg.grant("a@x.example", "full", "t1")
    pg.grant("b@x.example", "full", "t2")
    pg.use("a@x.example", "an1")
    assert pg.use("b@x.example", "an1") == {"allowed": False, "reason": "conflict"}


def test_bad_purpose_rejected(pg):
    pg.grant("a@x.example", "full", "t1")
    assert "bad purpose" in pg.fails(
        f"select public.cvmax_use_credit('{pg.tokens['app']}', 'a@x.example', 'an1', 'r', 'other');")


def test_fifo_per_purchase(pg):
    pg.grant("a@x.example", "full", "t_old")
    pg.grant("a@x.example", "hunt", "t_new")
    pg.run("update public.cvmax_purchases set created_at = now() - interval '2 days' where transaction_id = 't_old';")
    pg.use("a@x.example", "an1", "role one")
    ids = pg.val("select string_agg(p.transaction_id, ',') from public.cvmax_credit_use u "
                 "join public.cvmax_purchases p on p.id = u.purchase_id;")
    assert ids == "t_old"
    pg.use("a@x.example", "an2", "role two")
    ids = pg.val("select string_agg(p.transaction_id, ',' order by u.id) from public.cvmax_credit_use u "
                 "join public.cvmax_purchases p on p.id = u.purchase_id;")
    assert ids == "t_old,t_new"
    assert pg.credits("a@x.example")["full"] == 4


def test_refund_of_used_purchase_leaves_later_purchase(pg):
    pg.grant("a@x.example", "full", "t_old")
    pg.grant("a@x.example", "hunt", "t_new")
    pg.run("update public.cvmax_purchases set created_at = now() - interval '2 days' where transaction_id = 't_old';")
    pg.use("a@x.example", "an1", "role one")  # consumes t_old
    r = pg.call("cvmax_refund_purchase", "bill", "t_old")
    assert r == {"refunded": True, "plan": "full", "removed": 0}
    c = pg.credits("a@x.example")
    assert c["full"] == 5  # the hunt purchase is untouched, the used credit stays consumed
    assert pg.use("a@x.example", "an1", "role one")["duplicate"] is True


def test_refund_unspent_removes_credits_and_double_refund_is_noop(pg):
    pg.grant("a@x.example", "hunt", "t_h")
    pg.use("a@x.example", "an1", "r")
    assert pg.call("cvmax_refund_purchase", "bill", "t_h") == {"refunded": True, "plan": "hunt", "removed": 4}
    assert pg.credits("a@x.example")["full"] == 0
    assert pg.call("cvmax_refund_purchase", "bill", "t_h") == {"refunded": False, "reason": "already_refunded"}
    assert pg.call("cvmax_refund_purchase", "bill", "nope") == {"refunded": False, "reason": "unknown"}
    assert pg.credits("a@x.example")["has_hunt"] is False


def test_credits_expire_after_12_months(pg):
    pg.grant("a@x.example", "full", "t_old")
    _backdate_purchase(pg, "t_old", 13)
    assert pg.credits("a@x.example")["full"] == 0
    assert pg.use("a@x.example", "an1") == {"allowed": False, "reason": "no_credit"}
    pg.grant("a@x.example", "full", "t_ok")
    _backdate_purchase(pg, "t_ok", 11)
    assert pg.credits("a@x.example")["full"] == 1
    assert pg.use("a@x.example", "an2")["allowed"] is True


def test_builder_never_rescans_and_has_no_rescans_left(pg):
    pg.grant("a@x.example", "hunt", "t_h")
    assert pg.use("a@x.example", "b1", "Data Analyst", "builder") == {"allowed": True, "mode": "credit", "duplicate": False}
    assert pg.val("select rescans_left from public.cvmax_credit_use where analysis_id = 'b1';") == "0"
    c = pg.credits("a@x.example")
    assert c["rescan_roles"] == [] and c["last_use"] is None
    # a later review of the same role is a normal credit, not a re-scan
    assert pg.use("a@x.example", "r1", "Data Analyst")["mode"] == "credit"
    assert pg.credits("a@x.example")["full"] == 3
    # a builder use never consumes a re-scan either
    assert pg.use("a@x.example", "b2", "Data Analyst", "builder")["mode"] == "credit"
    assert pg.credits("a@x.example")["rescan_roles"] == ["data analyst"]


def test_review_rescan_same_normalized_role(pg):
    pg.grant("a@x.example", "full", "t1")
    assert pg.use("a@x.example", "r1", "Data  Analyst")["mode"] == "credit"
    c = pg.credits("a@x.example")
    assert c["full"] == 0 and c["rescan_roles"] == ["data analyst"]
    assert c["last_use"]["rescans_left"] == 1 and c["last_use"]["target_role"] == "data analyst"
    assert pg.use("a@x.example", "r2", "  data analyst ") == {"allowed": True, "mode": "rescan", "duplicate": False}
    assert pg.credits("a@x.example")["rescan_roles"] == []
    # only one re-scan per credit, and a different role never matches
    assert pg.use("a@x.example", "r3", "data analyst") == {"allowed": False, "reason": "no_credit"}
    assert pg.use("a@x.example", "r4", "Product Manager") == {"allowed": False, "reason": "no_credit"}


def test_refund_credit_restores_credit_and_rescan(pg):
    pg.grant("a@x.example", "full", "t1")
    pg.use("a@x.example", "r1", "dev")
    assert pg.use("a@x.example", "r2", "dev")["mode"] == "rescan"
    assert pg.call("cvmax_refund_credit", "app", "a@x.example", "r2") == {"refunded": True, "mode": "rescan"}
    assert pg.call("cvmax_refund_credit", "app", "a@x.example", "r2") == {"refunded": False}
    assert pg.credits("a@x.example")["rescan_roles"] == ["dev"]
    assert pg.call("cvmax_refund_credit", "app", "a@x.example", "r1") == {"refunded": True, "mode": "credit"}
    assert pg.credits("a@x.example")["full"] == 1
    # retry of a refunded analysis spends again
    assert pg.use("a@x.example", "r1", "dev") == {"allowed": True, "mode": "credit", "duplicate": False}
    assert pg.credits("a@x.example")["full"] == 0


def test_rescan_blocked_when_parent_purchase_refunded(pg):
    pg.grant("a@x.example", "full", "t1")
    pg.use("a@x.example", "r1", "dev")
    pg.call("cvmax_refund_purchase", "bill", "t1")
    assert pg.credits("a@x.example")["rescan_roles"] == []
    assert pg.use("a@x.example", "r2", "dev") == {"allowed": False, "reason": "no_credit"}


# ---------------------------------------------------------------- token scopes

def test_scope_enforcement(pg):
    learn, bill, app = pg.tokens["learn"], pg.tokens["bill"], pg.tokens["app"]
    assert "unauthorized" in pg.fails(
        f"select public.cvmax_grant_purchase('{learn}', 'a@x.example', 'full', 't1');")
    for tok in (learn, bill):
        assert "unauthorized" in pg.fails(f"select public.cvmax_credits('{tok}', 'a@x.example');")
        assert "unauthorized" in pg.fails(
            f"select public.cvmax_use_credit('{tok}', 'a@x.example', 'x', 'r', 'review');")
        assert "unauthorized" in pg.fails(f"select public.cvmax_touch_user('{tok}', 'a@x.example', 'A');")
    assert "unauthorized" in pg.fails(f"select public.cvmax_training_samples_for_judge('{bill}');")
    assert "unauthorized" in pg.fails(f"select public.cvmax_judged_samples('{bill}');")
    assert "unauthorized" in pg.fails(f"select public.cvmax_save_training_sample('{learn}', 'a@x.example', 'v1', "
                                      f"'{{}}'::jsonb, 'cv', '{{}}'::jsonb);")
    # the app token passes everywhere
    assert pg.call("cvmax_grant_purchase", "app", "a@x.example", "full", "t_app")["granted"] is True
    assert isinstance(pg.js(f"select public.cvmax_training_samples_for_judge('{app}');"), list)
    assert pg.call("cvmax_refund_purchase", "app", "t_app")["refunded"] is True
    # the learning token reaches the learning functions
    assert isinstance(pg.js(f"select public.cvmax_training_samples_for_judge('{learn}');"), list)


# ---------------------------------------------------------------- delete my data and training samples

def test_delete_my_data(pg):
    app = pg.tokens["app"]
    me, other = "me@x.example", "other@x.example"
    for u in (me, other):
        pg.run(f"select public.cvmax_touch_user('{app}', '{u}', 'N');", "anon")
        pg.grant(u, "hunt", "t_" + u)
        pg.use(u, "an_" + u, "dev")
        pg.run(f"select public.cvmax_log_event('{app}', '{u}', 'rating', '{{\"analysis_id\": \"{u}\"}}'::jsonb);", "anon")
        pg.run(f"select public.cvmax_log_edit_feedback('{app}', '{u}', 'ef_{u}', 'dev', 'cs', "
               f"'[{{\"source\":\"analysis\",\"before\":\"a\",\"after\":\"b\",\"accepted\":true}}]'::jsonb);", "anon")
        pg.run(f"select public.cvmax_save_training_sample('{app}', '{u}', 'v1', '{{}}'::jsonb, 'cv text', "
               f"'{{}}'::jsonb);", "anon")
        pg.run(f"select public.cvmax_save_result('{app}', '{u}', 'analysis', 'T', '{{}}'::jsonb);", "anon")
        pg.run(f"select public.cvmax_consume('{app}', '{u}', 'analysis', 5, 50);", "anon")
    # an older usage row (before today's start) is deleted; today's counters stay
    pg.run(f"insert into public.cvmax_usage (user_key, kind, created_at) values ('{me}', 'analysis', now() - interval '3 days');")
    pg.run(f"select public.cvmax_delete_my_data('{app}', '{me.upper()}');", "anon")
    for t in ("cvmax_users", "cvmax_events", "cvmax_edit_feedback", "cvmax_training_samples", "cvmax_credit_use"):
        col = "email" if t == "cvmax_users" else "user_key"
        assert pg.val(f"select count(*) from public.{t} where {col} = '{me}';") == "0", t
    assert pg.val(f"select count(*) from public.cvmax_results r where r.user_id not in (select id from public.cvmax_users);") == "0"
    assert pg.val(f"select count(*) from public.cvmax_usage where user_key = '{me}' and created_at < now() - interval '1 day';") == "0"
    keys = pg.val("select string_agg(user_key, ',' order by id) from public.cvmax_purchases;")
    assert keys == f"deleted:{pg.val(f'select md5($${me}$$);')},{other}"
    assert pg.val("select count(*) from public.cvmax_purchases;") == "2"
    # the other person is untouched
    for t in ("cvmax_events", "cvmax_edit_feedback", "cvmax_training_samples", "cvmax_credit_use"):
        assert pg.val(f"select count(*) from public.{t} where user_key = '{other}';") == "1", t
    assert pg.credits(other)["full"] == 4
    assert pg.credits(me)["full"] == 0


def test_save_training_sample_purges_old_rows(pg):
    app = pg.tokens["app"]
    pg.run(f"select public.cvmax_save_training_sample('{app}', 'a@x.example', 'v1', '{{}}'::jsonb, 'old cv', '{{}}'::jsonb);", "anon")
    pg.run("update public.cvmax_training_samples set created_at = now() - interval '13 months';")
    pg.run(f"select public.cvmax_save_training_sample('{app}', 'b@x.example', 'v1', '{{}}'::jsonb, 'new cv', '{{}}'::jsonb);", "anon")
    assert pg.val("select string_agg(cv_text, ',') from public.cvmax_training_samples;") == "new cv"


def test_purge_function_and_judge_limits(pg):
    app, learn = pg.tokens["app"], pg.tokens["learn"]
    for i in range(3):
        pg.run(f"select public.cvmax_save_training_sample('{app}', 'u{i}@x.example', 'v1', '{{\"i\": {i}}}'::jsonb, "
               f"'cv {i}', '{{}}'::jsonb);", "anon")
    pg.run("update public.cvmax_training_samples set created_at = now() - interval '13 months' where cv_text = 'cv 0';")
    pg.run("update public.cvmax_training_samples set created_at = now() - interval '30 days' where cv_text = 'cv 1';")
    # days window: the 30-day-old sample is outside the default 7 days; the purge removes only the 13-month-old one
    got = pg.js(f"select public.cvmax_training_samples_for_judge('{learn}', 7, 20);")
    assert [s["cv_text"] for s in got] == ["cv 2"]
    got = pg.js(f"select public.cvmax_training_samples_for_judge('{learn}', 60, 1);")
    assert len(got) == 1  # limit respected
    got = pg.js(f"select public.cvmax_training_samples_for_judge('{learn}', 60, 0);")
    assert len(got) == 1  # limit clamped to at least 1
    got = pg.js(f"select public.cvmax_training_samples_for_judge('{learn}', 1000, 500);")
    assert len(got) == 3  # an old row is still returned until it is purged; 500 is clamped to 50
    assert pg.js(f"select public.cvmax_purge_training_samples('{learn}');") == 1
    sid = pg.js(f"select public.cvmax_training_samples_for_judge('{learn}', 7, 20);")[0]["id"]
    pg.run(f"select public.cvmax_save_judgement('{learn}', '{sid}', '{{\"score\": 4}}'::jsonb);", "anon")
    assert pg.js(f"select public.cvmax_training_samples_for_judge('{learn}', 7, 20);") == []
    judged = pg.js(f"select public.cvmax_judged_samples('{learn}', 90, 200);")
    assert len(judged) == 1 and judged[0]["judge"] == {"score": 4}
    assert "bad judgement" in pg.fails(f"select public.cvmax_save_judgement('{learn}', '{sid}', '[1]'::jsonb);")


def test_training_sample_validation(pg):
    app = pg.tokens["app"]
    assert "bad cv text" in pg.fails(f"select public.cvmax_save_training_sample('{app}', 'a@x.example', 'v1', '{{}}'::jsonb, '   ', '{{}}'::jsonb);")
    assert "bad email" in pg.fails(f"select public.cvmax_save_training_sample('{app}', '', 'v1', '{{}}'::jsonb, 'cv', '{{}}'::jsonb);")
    assert "bad meta" in pg.fails(f"select public.cvmax_save_training_sample('{app}', 'a@x.example', 'v1', '[]'::jsonb, 'cv', '{{}}'::jsonb);")


def test_apply_files_match_schema(pg):
    """schema.sql alone and schema.sql + the apply-*.sql files end with identical functions and grants."""
    def psql(db, sql):
        r = subprocess.run([PSQL, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p",
                            str(pg.port), "-U", "postgres", "-d", db], input=sql, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout
    psql("postgres", "drop database if exists cvmax_schema_only;")
    psql("postgres", "create database cvmax_schema_only;")
    try:
        psql("cvmax_schema_only", DB_SQL)
        psql("cvmax_schema_only", (SUPABASE / "schema.sql").read_text())
        q = ("select p.oid::regprocedure::text || ' ' || pg_get_functiondef(p.oid) || ' ' || coalesce(p.proacl::text, '')"
             " from pg_proc p join pg_namespace n on n.oid = p.pronamespace"
             " where n.nspname in ('public', 'cvmax_private') order by p.oid::regprocedure::text;")
        assert psql("cvmax_schema_only", q) == pg.run(q).stdout
    finally:
        psql("postgres", "drop database if exists cvmax_schema_only;")


def test_rerunning_older_apply_files_keeps_every_delete(pg):
    """apply-now.sql / apply-judge.sql run again after apply-billing.sql must not lose the newer deletes."""
    app = pg.tokens["app"]
    try:
        for name in ("apply-now.sql", "apply-judge.sql"):
            pg.run((SUPABASE / name).read_text())
            u = "me@x.example"
            pg.grant(u, "full", "t_" + name)
            pg.use(u, "an_" + name)
            pg.run(f"select public.cvmax_save_training_sample('{app}', '{u}', 'v1', '{{}}'::jsonb, 'cv', '{{}}'::jsonb);", "anon")
            pg.run(f"select public.cvmax_delete_my_data('{app}', '{u}');", "anon")
            assert pg.val("select count(*) from public.cvmax_training_samples;") == "0", name
            assert pg.val("select count(*) from public.cvmax_credit_use;") == "0", name
            assert pg.val("select count(*) from public.cvmax_purchases where user_key like 'deleted:%';") == "1", name
            pg.run("truncate public.cvmax_purchases restart identity cascade;")
    finally:
        pg.run((SUPABASE / "schema.sql").read_text())  # back to the canonical definitions
