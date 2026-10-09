import json
from types import SimpleNamespace

import pytest

from cvmax.db import MemoryDB, SupabaseDB


class FakeHTTP:
    def __init__(self, body=None):
        self.calls, self.body = [], body

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json))
        return SimpleNamespace(status_code=200, content=b"x", text="", json=lambda: self.body)


def sdb(body=None):
    http = FakeHTTP(body)
    return SupabaseDB("https://p.supabase.co", "k", "tok", session=http), http


def test_rpc_names_and_params():
    cases = [
        (lambda d: d.log_event("u", "export", {"a": 1}), "cvmax_log_event",
         {"p_user_key": "u", "p_kind": "export", "p_payload": {"a": 1}}),
        (lambda d: d.variant_stats("analysis", 30), "cvmax_variant_stats", {"p_task": "analysis", "p_days": 30}),
        (lambda d: d.learning_export(7), "cvmax_learning_export", {"p_days": 7}),
        (lambda d: d.upsert_vacancies([{"url": "x"}]), "cvmax_upsert_vacancies", {"p_items": [{"url": "x"}]}),
        (lambda d: d.recent_vacancies("data", "ua", 10, 5), "cvmax_recent_vacancies",
         {"p_role_family": "data", "p_region": "ua", "p_days": 10, "p_limit": 5}),
        (lambda d: d.save_skill_demand([{"skill": "sql"}]), "cvmax_save_skill_demand", {"p_items": [{"skill": "sql"}]}),
        (lambda d: d.skill_demand("econ", "ua"), "cvmax_skill_demand", {"p_program": "econ", "p_region": "ua"}),
    ]
    for call, name, params in cases:
        db, http = sdb()
        call(db)
        url, body = http.calls[0]
        assert url.endswith("/rpc/" + name)
        assert body == {"p_token": "tok", **params}


def test_return_shapes_with_empty_answers():
    db, _ = sdb(None)
    assert db.variant_stats("analysis") == [] and db.recent_vacancies(None, None) == []
    assert db.skill_demand("p", "r") == [] and db.upsert_vacancies([]) == 0
    assert set(db.learning_export()) == {"edit_feedback", "feedback", "events"}


def test_oversized_payload_or_bad_kind_raises_before_http():
    db, http = sdb()
    with pytest.raises(ValueError):
        db.log_event("u", "x", {"t": "a" * 4001})
    with pytest.raises(ValueError):
        db.log_event("u", "", {})
    with pytest.raises(ValueError):
        db.log_event("u", "k" * 41, {})
    assert http.calls == []
    with pytest.raises(ValueError):
        MemoryDB().log_event("u", "k" * 41, {})


def done(db, aid, variant, program="econ", **extra):
    db.log_event("u", "analysis_done", {"analysis_id": aid, "variant": variant, "program": program, **extra})


def stats(db):
    return {(r["variant"], r["program"]): (r["successes"], r["failures"]) for r in db.variant_stats("analysis")}


def test_rating_beats_edits():
    db = MemoryDB()
    done(db, "a1", "v1")
    db.log_event("u", "edits_decided", {"analysis_id": "a1", "accepted": 0, "total": 5})
    db.log_event("u", "analysis_rated", {"analysis_id": "a1", "rating": 1})
    done(db, "a2", "v1")
    db.log_event("u", "edits_decided", {"analysis_id": "a2", "accepted": 5, "total": 5})
    db.log_event("u", "analysis_rated", {"analysis_id": "a2", "rating": 0})
    assert stats(db) == {("v1", "econ"): (1, 1)}


def test_latest_rating_wins():
    db = MemoryDB()
    done(db, "a1", "v1")
    db.log_event("u", "analysis_rated", {"analysis_id": "a1", "rating": 0})
    db.log_event("u", "analysis_rated", {"analysis_id": "a1", "rating": 1})
    assert stats(db) == {("v1", "econ"): (1, 0)}


def test_unrated_uses_acceptance_rule_and_ignores_weak_signals():
    db = MemoryDB()
    done(db, "ok", "v1")
    db.log_event("u", "edits_decided", {"analysis_id": "ok", "accepted": 1, "total": 2})  # exactly 50%
    done(db, "bad", "v1")
    db.log_event("u", "edits_decided", {"analysis_id": "bad", "accepted": 0, "total": 3})
    done(db, "tiny", "v1")
    db.log_event("u", "edits_decided", {"analysis_id": "tiny", "accepted": 1, "total": 1})  # total < 2
    done(db, "none", "v1")
    assert stats(db) == {("v1", "econ"): (1, 1)}


def test_outcome_yes_adds_two_successes_and_no_does_not():
    db = MemoryDB()
    done(db, "a1", "v1")
    db.log_event("u", "analysis_rated", {"analysis_id": "a1", "rating": 1})
    db.log_event("u", "outcome", {"analysis_id": "a1", "answer": "yes"})
    done(db, "a2", "v1")
    db.log_event("u", "analysis_rated", {"analysis_id": "a2", "rating": 0})
    db.log_event("u", "outcome", {"analysis_id": "a2", "answer": "no"})
    assert stats(db) == {("v1", "econ"): (3, 1)}


def test_grouping_by_variant_program_and_task():
    db = MemoryDB()
    for aid, variant, program in [("a", "v1", "econ"), ("b", "v2", "econ"), ("c", "v1", None)]:
        done(db, aid, variant, program)
        db.log_event("u", "analysis_rated", {"analysis_id": aid, "rating": 1})
    done(db, "d", "v1", task="qa")
    db.log_event("u", "analysis_rated", {"analysis_id": "d", "rating": 1})
    assert stats(db) == {("v1", "econ"): (1, 0), ("v2", "econ"): (1, 0), ("v1", ""): (1, 0)}
    assert db.variant_stats("qa") == [{"variant": "v1", "program": "econ", "successes": 1, "failures": 0}]
    assert MemoryDB().variant_stats("analysis") == []


def test_learning_export_has_no_user_key():
    db = MemoryDB()
    db.log_event("secret_user", "analysis_done", {"analysis_id": "a1", "variant": "v1"})
    db.log_event("secret_user", "page_view", {"x": 1})  # not in the list of kinds
    db.save_feedback("career", 1, "nice")
    out = db.learning_export()
    text = json.dumps(out)
    assert "user_key" not in text and "secret_user" not in text
    assert [e["kind"] for e in out["events"]] == ["analysis_done"]
    assert out["feedback"][0]["page"] == "career" and "message" not in out["feedback"][0]


def test_delete_my_data_drops_events():
    db = MemoryDB()
    db.log_event("a@b.c", "export", {})
    db.log_event("o@b.c", "export", {})
    db.delete_my_data("A@b.c")
    assert [e["user_key"] for e in db.events] == ["o@b.c"]


def test_upsert_vacancies_dedupes_by_url_and_skips_empty():
    db = MemoryDB()
    n = db.upsert_vacancies([
        {"url": "https://x/1", "title": "A", "source": "dou", "role_family": "data", "region": "ua",
         "snippet": "s" * 900},
        {"url": "https://x/1", "title": "A2", "source": "dou", "role_family": "data", "region": "ua"},
        {"url": "", "title": "no url"}, {"url": "https://x/2", "title": ""},
    ])
    assert n == 2 and len(db.vacancies) == 1
    rows = db.recent_vacancies("data", "ua")
    assert len(rows) == 1 and rows[0]["title"] == "A2" and len(rows[0]["snippet"]) == 600
    assert db.recent_vacancies("other", "ua") == []


def test_skill_demand_replaces_per_program_region():
    db = MemoryDB()
    item = lambda p, r, s, n: {"program": p, "region": r, "skill": s, "postings": n, "total_postings": 50,
                               "window_days": 30}
    assert db.save_skill_demand([item("econ", "ua", "sql", 10), item("econ", "ua", "excel", 20),
                                 item("cs", "ua", "python", 5)]) == 3
    assert [r["skill"] for r in db.skill_demand("econ", "ua")] == ["excel", "sql"]
    db.save_skill_demand([item("econ", "ua", "tableau", 7)])
    assert [r["skill"] for r in db.skill_demand("econ", "ua")] == ["tableau"]
    assert [r["skill"] for r in db.skill_demand("cs", "ua")] == ["python"]  # another pair is not touched


def test_rating_counts_only_real_int_0_or_1():
    db = MemoryDB()
    for i, bad in enumerate([True, False, 1.0, 0.0, "1", None, 2, -1]):
        done(db, f"a{i}", "v1")
        db.log_event("u", "analysis_rated", {"analysis_id": f"a{i}", "rating": bad})
    assert stats(db) == {}  # no rating counted, no edits
    done(db, "ok1", "v1")
    db.log_event("u", "analysis_rated", {"analysis_id": "ok1", "rating": 1})
    done(db, "ok0", "v1")
    db.log_event("u", "analysis_rated", {"analysis_id": "ok0", "rating": 0})
    assert stats(db) == {("v1", "econ"): (1, 1)}


def test_count_parsing_is_strict_and_never_raises():
    from cvmax.db import _as_count
    assert _as_count(0) == 0 and _as_count(999999) == 999999 and _as_count("000012") == 12
    for bad in (True, 1.0, -1, 1000000, "1000000", "-1", "1.0", "\u0663", "", " 1", "1\n", None, [1], {}):
        assert _as_count(bad) is None
    db = MemoryDB()
    done(db, "a", "v1")
    db.log_event("u", "edits_decided", {"analysis_id": "a", "accepted": [1], "total": {"x": 1}})
    assert stats(db) == {}


def test_learning_export_has_breadth_key():
    out = MemoryDB().learning_export()
    assert set(out) == {"edit_feedback", "feedback", "events", "breadth"}
    assert out["breadth"] == {}


def test_same_url_in_two_regions_keeps_two_rows():
    db = MemoryDB()
    base = {"url": "https://x/1", "title": "A", "source": "dou", "role_family": "data"}
    assert db.upsert_vacancies([{**base, "region": "ua"}, {**base, "region": "pl"}, {**base, "region": "ua"}]) == 3
    assert len(db.vacancies) == 2
    assert len(db.recent_vacancies("data", "ua")) == 1 and len(db.recent_vacancies("data", "pl")) == 1
    assert len(db.recent_vacancies("data", None)) == 1  # one post, not two


def test_schema_pins_vacancy_uniqueness_contract():
    import pathlib
    sql = (pathlib.Path(__file__).resolve().parents[1] / "supabase" / "schema.sql").read_text()
    assert "unique (url_hash, role_family, region)" in sql
    assert "on conflict (url_hash, role_family, region) do update" in sql
    assert "url_hash text not null unique" not in sql
    assert "on conflict (url_hash) do update" not in sql
    assert sql.count("create or replace function public.cvmax_upsert_vacancies") == 1
    assert "cvmax_private.canonical_section(text)" in sql
