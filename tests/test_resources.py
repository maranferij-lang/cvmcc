"""Tests for the vetted learning resources core: catalog, validation, selection, plan, events, vocabulary."""
from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from cvmax.learning import events
from cvmax.profile import PROGRAMS
from cvmax.resources import catalog as catalog_mod
from cvmax.resources import select as select_mod
from cvmax.resources.catalog import (
    CatalogError, load_catalog, load_link_status, load_skills, validate, visible_resources,
)
from cvmax.resources.models import Evidence, Pick, Rating, Resource, Votes
from cvmax.resources.plan import build_plan
from cvmax.resources.select import match_skills, pick, quality_points

FIXTURES = Path(__file__).parent / "fixtures" / "resources"
FIXTURE_CATALOG = FIXTURES / "catalog"
FIXTURE_SKILLS = FIXTURES / "skills.json"
TODAY = date(2026, 10, 20)


# --- helpers ---

def fixture_resources() -> list[Resource]:
    return load_catalog(FIXTURE_CATALOG)


def fixture_skills():
    return load_skills(FIXTURE_SKILLS)


def by_id(rid: str) -> Resource:
    return next(r for r in fixture_resources() if r.id == rid)


def base() -> Resource:
    """A valid free resource."""
    return by_id("northfield-sql-basics")


def errors_for(r: Resource, today: date = TODAY) -> list[str]:
    return validate([r], fixture_skills(), today=today)


def make(rid: str, **over) -> Resource:
    """A valid resource with a different id (and optional overrides)."""
    return replace(base(), id=rid, title=f"{rid} course", **over)


def evidence(n: int) -> list[Evidence]:
    return [Evidence(claim=f"Claim {i}", url=f"https://example.org/e/{i}", seen="2026-10-12") for i in range(n)]


def iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


# --- fixtures and loading ---

def test_fixture_catalog_is_valid():
    resources = fixture_resources()
    assert len(resources) == 7
    assert validate(resources, fixture_skills(), today=TODAY) == []


def test_load_catalog_empty_or_missing_directory(tmp_path):
    assert load_catalog(tmp_path) == []
    assert load_catalog(tmp_path / "does-not-exist") == []
    (tmp_path / "notes.txt").write_text("not json")
    assert load_catalog(tmp_path) == []


def test_load_catalog_default_directory_is_the_package_catalog():
    assert catalog_mod.CATALOG_DIR == Path(catalog_mod.__file__).resolve().parent / "catalog"


def test_load_catalog_roundtrip():
    r = base()
    assert Resource.from_dict(r.to_dict()) == r


@pytest.mark.parametrize("content, expected", [
    ("{not json", "invalid JSON"),
    ('["a list"]', "top level must be an object"),
    ('{"resources": []}', "'cluster'"),
    ('{"cluster": "data", "resources": {}}', "'resources' must be a list"),
    ('{"cluster": "data", "resources": ["text"]}', "must be an object"),
])
def test_malformed_file_raises_and_names_the_file(tmp_path, content, expected):
    (tmp_path / "broken.json").write_text(content, encoding="utf-8")
    with pytest.raises(CatalogError) as exc:
        load_catalog(tmp_path)
    assert "broken.json" in str(exc.value)
    assert expected in str(exc.value)


def test_missing_field_names_file_and_field(tmp_path):
    item = base().to_dict()
    del item["why_picked"]
    (tmp_path / "data.json").write_text(json.dumps({"cluster": "data", "resources": [item]}))
    with pytest.raises(CatalogError) as exc:
        load_catalog(tmp_path)
    assert "data.json" in str(exc.value) and "why_picked" in str(exc.value)


def test_missing_nested_field_is_reported(tmp_path):
    item = base().to_dict()
    del item["evidence"][0]["url"]
    (tmp_path / "data.json").write_text(json.dumps({"cluster": "data", "resources": [item]}))
    with pytest.raises(CatalogError) as exc:
        load_catalog(tmp_path)
    assert "evidence[0]" in str(exc.value) and "url" in str(exc.value)


def test_one_bad_file_among_good_ones_is_not_skipped(tmp_path):
    for f in FIXTURE_CATALOG.glob("*.json"):
        (tmp_path / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "zz_bad.json").write_text("{oops")
    with pytest.raises(CatalogError, match="zz_bad.json"):
        load_catalog(tmp_path)


# --- validate(): every rule has a passing and a failing case ---

def test_valid_resource_has_no_errors():
    assert errors_for(base()) == []


@pytest.mark.parametrize("url", [
    "https://learn.example.org/x", "https://learn.example.org/x?lang=en&page=2",
])
def test_url_accepts_https_and_harmless_query(url):
    assert errors_for(replace(base(), url=url)) == []


@pytest.mark.parametrize("url, fragment", [
    ("http://learn.example.org/x", "https"),
    ("ftp://learn.example.org/x", "https"),
    ("learn.example.org/x", "https"),
    ("https://192.168.0.1/course", "IP"),
    ("https://[::1]/course", "IP"),
    ("https://2130706433/course", "IP"),
    ("https://bit.ly/abc", "shortener"),
    ("https://t.co/abc", "shortener"),
    ("https://tinyurl.com/abc", "shortener"),
    ("https://click.linksynergy.com/deeplink?id=1", "affiliate"),
    ("https://go.skimresources.com/?id=1", "affiliate"),
    ("https://www.awin1.com/cread.php?awinmid=1", "affiliate"),
    ("https://goto.impact.com/c/1/2", "affiliate"),
    ("https://www.shareasale.com/r.cfm?b=1", "affiliate"),
    ("https://learn.example.org/x?affid=42", "query"),
    ("https://learn.example.org/x?aff_id=42", "query"),
    ("https://learn.example.org/x?AffID=42", "query"),
    ("https://learn.example.org/x?ref=partner", "query"),
    ("https://learn.example.org/x?a=1&utm_source=blog", "query"),
    ("https://user:pw@learn.example.org/x", "credentials"),
    ("https://learn.example.org/a b", "whitespace"),
])
def test_url_rules_reject(url, fragment):
    errs = errors_for(replace(base(), url=url))
    assert errs, url
    assert any(fragment in e for e in errs), errs
    assert all(e.startswith("northfield-sql-basics: ") for e in errs)


def test_evidence_counts_by_cost():
    free = base()
    assert errors_for(replace(free, evidence=evidence(1))) == []
    assert any("evidence" in e for e in errors_for(replace(free, evidence=[])))
    paid = by_id("brightpath-advanced-sql")
    assert errors_for(replace(paid, evidence=evidence(2))) == []
    assert any("at least 2" in e for e in errors_for(replace(paid, evidence=evidence(1))))
    freemium = by_id("dataworks-power-bi-project")
    assert errors_for(replace(freemium, evidence=evidence(2))) == []
    assert any("at least 2" in e for e in errors_for(replace(freemium, evidence=evidence(1))))
    audit = by_id("datalab-pandas-audit")
    assert errors_for(replace(audit, evidence=evidence(1))) == []
    assert errors_for(replace(audit, evidence=[]))


@pytest.mark.parametrize("bad", [
    Evidence("Seen", "http://example.org/e", "2026-10-12"),
    Evidence("Seen", "https://bit.ly/e", "2026-10-12"),
    Evidence("Seen", "https://example.org/e", "12 Oct 2026"),
    Evidence("Seen", "https://example.org/e", "2026-11-30"),
    Evidence("  ", "https://example.org/e", "2026-10-12"),
])
def test_evidence_items_are_checked(bad):
    assert any("evidence[0]" in e for e in errors_for(replace(base(), evidence=[bad])))


def test_rating_thresholds():
    def rating(value=4.8, scale=5, count=15300, url="https://example.org/r", seen="2026-10-12"):
        return Rating(value=value, scale=scale, count=count, source_url=url, seen=seen)

    assert errors_for(replace(base(), rating=None)) == []
    assert errors_for(replace(base(), rating=rating(count=1000))) == []
    assert errors_for(replace(base(), rating=rating(value=4.5, scale=5))) == []  # exactly 0.9
    assert errors_for(replace(base(), rating=rating(value=9, scale=10))) == []
    assert any("count" in e for e in errors_for(replace(base(), rating=rating(count=999))))
    assert any("below" in e for e in errors_for(replace(base(), rating=rating(value=4.4))))
    assert any("below" in e for e in errors_for(replace(base(), rating=rating(value=8.9, scale=10))))
    assert errors_for(replace(base(), rating=rating(value=6, scale=5)))
    assert errors_for(replace(base(), rating=rating(url="http://example.org/r")))
    assert errors_for(replace(base(), rating=rating(seen="yesterday")))


def test_verified_at_freshness_uses_injected_today():
    assert errors_for(replace(base(), verified_at=iso(0))) == []
    assert errors_for(replace(base(), verified_at=iso(400))) == []
    assert any("days old" in e for e in errors_for(replace(base(), verified_at=iso(401))))
    assert any("future" in e for e in errors_for(replace(base(), verified_at=iso(-1))))
    assert any("ISO" in e for e in errors_for(replace(base(), verified_at="20/10/2026")))
    # The same date is fine on an earlier "today".
    assert errors_for(replace(base(), verified_at=iso(401)), today=TODAY - timedelta(days=5)) == []


def test_why_picked_length():
    assert errors_for(replace(base(), why_picked="x" * 200)) == []
    assert any("why_picked" in e for e in errors_for(replace(base(), why_picked="x" * 201)))
    assert any("why_picked" in e for e in errors_for(replace(base(), why_picked="  ")))


def test_skill_ids_must_exist_and_count_is_1_to_3():
    assert errors_for(replace(base(), skills=["sql", "python", "pandas"])) == []
    assert any("unknown skill" in e for e in errors_for(replace(base(), skills=["sql", "cobol"])))
    assert any("1 to 3" in e for e in errors_for(replace(base(), skills=[])))
    assert any("1 to 3" in e for e in errors_for(replace(base(), skills=["sql", "python", "pandas", "ielts"])))
    assert errors_for(replace(base(), skills=["sql", "sql"]))


def test_status_must_be_active():
    assert errors_for(replace(base(), status="active")) == []
    assert any("active" in e for e in errors_for(replace(base(), status="needs_check")))
    assert any("active" in e for e in errors_for(replace(base(), status="dead")))
    assert any("status must be one of" in e for e in errors_for(replace(base(), status="hidden")))


def test_id_rules():
    assert errors_for(replace(base(), id="northfield-sql-basics")) == []
    assert any("slug" in e for e in errors_for(replace(base(), id="Northfield SQL")))
    assert any("slug" in e for e in errors_for(replace(base(), id="north_field")))
    assert any("derived" in e for e in errors_for(replace(base(), id="zzz-qqq")))
    dup = validate([base(), replace(base())], fixture_skills(), today=TODAY)
    assert any("duplicate id" in e for e in dup)
    assert validate([base(), make("northfield-other")], fixture_skills(), today=TODAY) == []


def test_enum_fields():
    for field, good, bad in [("kind", "tutorial", "podcast"), ("level", "advanced", "expert"),
                             ("cost", "free", "cheap"), ("verification", "search-snippet", "guess")]:
        assert errors_for(replace(base(), **{field: good})) == [], field
        assert any(field in e for e in errors_for(replace(base(), **{field: bad}))), field


def test_hours_range_and_type():
    assert errors_for(replace(base(), hours=1)) == []
    assert errors_for(replace(base(), hours=400)) == []
    for bad in (0, 401, -3, "8", 8.5, True):
        assert any("hours" in e for e in errors_for(replace(base(), hours=bad))), bad


def test_price_rules():
    paid = by_id("brightpath-advanced-sql")
    assert errors_for(paid) == []
    assert any("price_usd" in e for e in errors_for(replace(paid, price_usd=None)))
    assert any("price_usd" in e for e in errors_for(replace(paid, price_usd=0)))
    assert any("price_usd" in e for e in errors_for(replace(paid, price_usd="49")))
    freemium = by_id("dataworks-power-bi-project")
    assert any("price_usd" in e for e in errors_for(replace(freemium, price_usd=None)))
    assert any("price_usd" in e for e in errors_for(replace(base(), price_usd=10)))


def test_other_typed_fields():
    assert any("languages" in e for e in errors_for(replace(base(), languages=[])))
    assert any("languages" in e for e in errors_for(replace(base(), languages=["English"])))
    assert any("hands_on" in e for e in errors_for(replace(base(), hands_on="yes")))
    assert any("updated_year" in e for e in errors_for(replace(base(), updated_year=2099)))
    assert errors_for(replace(base(), updated_year=None)) == []
    assert any("title" in e for e in errors_for(replace(base(), title="")))
    assert any("provider" in e for e in errors_for(replace(base(), provider=" ")))
    assert any("output" in e for e in errors_for(replace(base(), output="")))


def test_validate_accepts_plain_skill_ids():
    assert validate([base()], ["sql"], today=TODAY) == []
    assert validate([base()], {"python"}, today=TODAY)


def test_errors_name_the_resource():
    errs = validate([base(), replace(by_id("pycamp-python-intro"), hours=0)], fixture_skills(), today=TODAY)
    assert errs and all(e.startswith("pycamp-python-intro: ") for e in errs)


# --- link health and visibility ---

def test_visible_resources_hides_dead_and_non_active():
    a, b, c, d = make("a-one"), make("b-two"), make("c-three", status="needs_check"), make("d-four", status="dead")
    status = {
        "a-one": {"ok": False, "code": 404, "checked": "2026-10-19", "fails": 2},
        "b-two": {"ok": True, "code": 200, "checked": "2026-10-19", "fails": 0},
    }
    assert [r.id for r in visible_resources([a, b, c, d], status)] == ["b-two"]
    # A first failure is not dead yet; unknown ids and an empty status keep everything active visible.
    status["a-one"]["fails"] = 1
    assert [r.id for r in visible_resources([a, b, c, d], status)] == ["a-one", "b-two"]
    assert [r.id for r in visible_resources([a, b, c, d], {})] == ["a-one", "b-two"]
    assert [r.id for r in visible_resources([a, b], None)] == ["a-one", "b-two"]
    assert visible_resources([a], {"a-one": "dead"}) == []
    assert [r.id for r in visible_resources([a], {"a-one": "garbage"})] == ["a-one"]


def test_load_link_status_missing_or_broken_is_empty(tmp_path):
    assert load_link_status(tmp_path / "nope.json") == {}
    (tmp_path / "bad.json").write_text("{oops")
    assert load_link_status(tmp_path / "bad.json") == {}
    (tmp_path / "ok.json").write_text(json.dumps({"x": {"ok": True, "code": 200, "checked": "2026-10-12", "fails": 0}}))
    assert load_link_status(tmp_path / "ok.json")["x"]["ok"] is True


def test_default_pick_uses_catalog_dir_and_hides_dead(tmp_path, monkeypatch):
    catalog_dir = tmp_path / "catalog"
    catalog_dir.mkdir()
    for f in FIXTURE_CATALOG.glob("*.json"):
        (catalog_dir / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
    status_dir = tmp_path / "status"
    status_dir.mkdir()
    status_file = status_dir / "link_status.json"
    monkeypatch.setattr(select_mod, "CATALOG_DIR", catalog_dir)
    monkeypatch.setattr(select_mod, "LINK_STATUS_PATH", status_file)
    # status_file does not exist yet: nothing is hidden.
    assert [p.resource.id for p in pick("sql")][0] == "northfield-sql-basics"
    status_file.write_text(json.dumps({"northfield-sql-basics": {"ok": False, "code": 404, "checked": "2026-10-19", "fails": 3}}))
    assert "northfield-sql-basics" not in [p.resource.id for p in pick("sql")]


# --- match_skills (real vocabulary) ---

@pytest.mark.parametrize("text, expected", [
    ("SQL (joins, GROUP BY, window functions) on a public dataset", ["sql"]),
    ("Power BI dashboard", ["power bi"]),
    ("IELTS 7.0+", ["ielts"]),
    ("Python (pandas)", ["pandas", "python"]),
])
def test_match_skills_gap_sentences(text, expected):
    assert match_skills(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "Add measurable results to your bullet points", "Your summary is too generic.",
    "Rewrite the education section", None,
])
def test_match_skills_no_skill_returns_empty(text):
    assert match_skills(text) == []


def test_match_skills_aliases_and_boundaries():
    assert match_skills("Experience with PostgreSQL") == ["sql"]
    assert match_skills("strong knowledge of JavaScript") == ["javascript"]  # not java
    assert match_skills("Java developer") == ["java"]
    assert set(match_skills("Used R and Python for analysis")) == {"python", "r"}
    assert "r" not in match_skills("Worked in R&D on new products")
    assert match_skills("no mysqlish word") == []
    assert match_skills("A/B testing of the landing page") == ["a/b testing"]
    assert match_skills("Business English certificate") == ["business english"]


def test_match_skills_longest_alias_first_and_max_three():
    # "power bi" is consumed as one span, so no second skill is added from its parts.
    assert match_skills("Power BI") == ["power bi"]
    many = "SQL, Excel, Tableau, Docker, React and Linux"
    got = match_skills(many)
    assert len(got) == 3
    assert got == match_skills(many)


def test_match_skills_is_deterministic_and_case_insensitive():
    text = "sql and EXCEL and Tableau"
    assert match_skills(text) == match_skills(text.upper()) == match_skills(text)


def test_match_skills_custom_vocabulary():
    assert match_skills("we use dataframes daily", fixture_skills()) == ["pandas"]
    assert match_skills("Power BI", fixture_skills()) == ["power bi"]


def test_match_skills_bounds_huge_input():
    assert match_skills("sql " + "x" * 10_000_000) == ["sql"]


@pytest.mark.parametrize("text", [
    "React to feedback quickly",
    "Learn to react to recruiter feedback",
    "Excel at stakeholder communication",
    "You excel in teamwork",
    "interpret regression output (R-squared, p-values)",
    "Project R-1 deliverables",
    "section R of the form",
    "Appendix R lists the fees",
    "Interviewing users for UX is not a skill gap",
    "NoSQL databases (MongoDB)",
    "Demand forecasting",
    "Credit/Model risk management",
    "CI/CD pipeline",
    "Marian joins the team in May",
    "Sprints and containers are everywhere",
    "R&D",
    "go-to-market",
])
def test_match_skills_false_positives_stay_out(text):
    got = match_skills(text)
    assert not {"react", "excel", "r", "statistics", "sql", "financial modeling", "compliance", "docker",
                "interview skills"} & set(got), got


def test_match_skills_interviewing_users_is_ux_research():
    assert match_skills("Interviewing users for UX") == ["ux research"]


@pytest.mark.parametrize("text, expected", [
    ("SQL (joins, GROUP BY) on a public dataset", ["sql"]),
    ("IELTS 7.0+", ["ielts"]),
    ("English", ["english"]),
    ("Java", ["java"]),
    ("JavaScript", ["javascript"]),
    ("Python (pandas)", ["pandas", "python"]),
    ("React", ["react"]),
    ("React.js", ["react"]),
    ("Experience with React and TypeScript", ["typescript", "react"]),
    ("Microsoft Excel pivot tables", ["excel"]),
    ("Advanced Excel", ["excel"]),
    ("EXCEL and REACT", ["excel", "react"]),
    ("Used R", ["r"]),
    ("Statistics in R.", ["statistics", "r"]),
    ("NoSQL and SQL", ["sql"]),
    ("A NoSQL store plus a SQL warehouse", ["sql"]),
])
def test_match_skills_still_correct(text, expected):
    assert match_skills(text) == expected


def test_match_skills_lowercase_react_and_excel_are_verbs():
    # The words are matched case-sensitively; the capitalised noun and the aliases still work.
    assert match_skills("we react quickly and excel at sales") == []
    assert match_skills("learn reactjs") == ["react"]
    assert match_skills("excel formulas") == ["excel"]


def test_match_skills_nosql_alone_is_not_sql():
    assert match_skills("NoSQL") == []
    assert match_skills("MongoDB and other NoSQL stores") == []


def test_match_skills_r_is_not_a_hyphenated_or_labelled_letter():
    assert match_skills("R-squared and R-1") == []
    assert match_skills("Part R, table R and row R") == []
    assert match_skills("Python and R") == ["python", "r"]


@pytest.mark.parametrize("text, expected", [
    ("Англійська на рівні B2", ["english"]),
    ("Статистика і теорія ймовірностей", ["statistics"]),
    ("Бухгалтерський облік за МСФЗ", ["accounting"]),
    ("Основи бухгалтерського обліку", ["accounting"]),
    ("Знання англійської мови", ["english"]),
    ("УПРАВЛІННЯ ПРОЄКТАМИ", ["project management"]),
    ("Проєктний менеджмент", ["project management"]),
    ("Досвід продуктового менеджменту", ["product management"]),
    ("Ділове листування англійською", ["business writing", "english"]),
    ("Підготовка до співбесід", ["interview skills"]),
    ("Публічні виступи", ["public speaking"]),
    ("Дослідження користувачів", ["ux research"]),
    ("Захист персональних даних", ["gdpr"]),
    ("Юридичне дослідження", ["legal research"]),
    ("Академічне письмо", ["academic writing"]),
    ("Фінансове моделювання", ["financial modeling"]),
    ("Оцінка бізнесу", ["valuation"]),
    ("Ділова англійська", ["business english"]),
])
def test_match_skills_ukrainian_gaps(text, expected):
    assert match_skills(text) == expected


def test_match_skills_ukrainian_needs_a_word_start_and_is_case_insensitive():
    assert match_skills("нестатистика") == []
    assert match_skills("СТАТИСТИКА") == match_skills("статистика") == ["statistics"]
    assert match_skills("Ukrainian и English: англійська") == ["english"]


# --- quality_points ---

def test_quality_points_full_example_and_reasons():
    r = base()  # free, hands-on, 3 evidence, 4.8 from 15300, updated 2025, beginner, en
    pts, reasons = quality_points(r, today=TODAY)
    assert pts == 3 + 2 + 3 + 2 + 1
    assert reasons == ["Free", "Hands-on", "3 sources checked by a person",
                       "4.8 from 15,300 ratings (seen Oct 2026)", "Updated 2025"]
    pts, reasons = quality_points(r, level="beginner", language="English", today=TODAY)
    assert pts == 11 + 1 + 2
    assert "Fits your level (beginner)" in reasons and "In English" in reasons


def test_quality_points_cost_tiers():
    assert quality_points(replace(base(), cost="free", hands_on=False, evidence=[], rating=None, updated_year=None), today=TODAY)[0] == 3
    assert quality_points(replace(base(), cost="free-audit", hands_on=False, evidence=[], rating=None, updated_year=None), today=TODAY)[0] == 2
    assert quality_points(replace(base(), cost="freemium", price_usd=9, hands_on=False, evidence=[], rating=None, updated_year=None), today=TODAY)[0] == 1
    pts, reasons = quality_points(replace(base(), cost="paid", price_usd=49, hands_on=False, evidence=[], rating=None, updated_year=None), today=TODAY)
    assert pts == 0 and reasons == ["Paid, $49"] or pts == 0 and reasons == ["Paid, $49 per month"]
    # The billing basis is part of the reason line, and "full version" is never claimed for a freemium entry.
    paid = replace(base(), cost="paid", price_usd=59, price_note="per month with Coursera Plus", hands_on=False,
                   evidence=[], rating=None, updated_year=None)
    assert quality_points(paid, today=TODAY)[1] == ["Paid, $59 per month with Coursera Plus"]
    freemium = replace(base(), cost="freemium", price_usd=3, price_note="AWS usage per month after the free tier",
                       hands_on=False, evidence=[], rating=None, updated_year=None)
    reasons = quality_points(freemium, today=TODAY)[1]
    assert reasons == ["Free tier, $3 AWS usage per month after the free tier"]
    assert not any("full version" in text for text in reasons)


def test_quality_points_evidence_is_capped_at_three():
    bare = replace(base(), hands_on=False, rating=None, updated_year=None)
    assert quality_points(replace(bare, evidence=evidence(1)), today=TODAY)[0] == 3 + 1
    assert quality_points(replace(bare, evidence=evidence(3)), today=TODAY)[0] == 3 + 3
    assert quality_points(replace(bare, evidence=evidence(7)), today=TODAY)[0] == 3 + 3


def test_quality_points_rating_tiers():
    bare = replace(base(), hands_on=False, evidence=[], updated_year=None)

    def pts(value, count, scale=5):
        g = Rating(value=value, scale=scale, count=count, source_url="https://example.org/r", seen="2026-10-12")
        return quality_points(replace(bare, rating=g), today=TODAY)[0] - 3

    assert pts(4.6, 5000) == 2        # 0.92 exactly at 5000
    assert pts(4.6, 4999) == 1
    assert pts(4.5, 20000) == 1       # share 0.90 < 0.92
    assert pts(4.9, 1000) == 1
    assert pts(4.9, 999) == 0
    assert pts(9.5, 6000, scale=10) == 2


def test_quality_points_freshness_window():
    bare = replace(base(), hands_on=False, evidence=[], rating=None)
    assert quality_points(replace(bare, updated_year=2026), today=TODAY)[0] == 4
    assert quality_points(replace(bare, updated_year=2023), today=TODAY)[0] == 4
    assert quality_points(replace(bare, updated_year=2022), today=TODAY)[0] == 3
    assert quality_points(replace(bare, updated_year=None), today=TODAY)[0] == 3


def test_quality_points_language_and_level():
    bare = replace(base(), hands_on=False, evidence=[], rating=None, updated_year=None, languages=["uk"], level="advanced")
    assert quality_points(bare, language="Ukrainian", today=TODAY)[0] == 3 + 2
    assert quality_points(bare, language="uk", today=TODAY)[0] == 3 + 2
    assert quality_points(bare, language="English", today=TODAY)[0] == 3
    assert quality_points(bare, level="advanced", today=TODAY)[0] == 3 + 1
    assert quality_points(bare, level="beginner", today=TODAY)[0] == 3
    assert quality_points(bare, language=None, level=None, today=TODAY)[0] == 3


def test_votes_ignored_below_20_and_counted_from_20():
    bare = replace(base(), hands_on=False, evidence=[], rating=None, updated_year=None)
    nothing = quality_points(bare, today=TODAY)[0]
    assert quality_points(bare, None, today=TODAY)[0] == nothing
    # 19 votes: ignored, even when all are negative or all positive.
    assert quality_points(bare, Votes(helpful=0, not_helpful=19), today=TODAY)[0] == nothing
    assert quality_points(bare, Votes(helpful=19, not_helpful=0), today=TODAY)[0] == nothing
    # 20 votes: counted.
    assert quality_points(bare, Votes(helpful=16, not_helpful=4), today=TODAY)[0] == nothing + 2   # 0.8
    assert quality_points(bare, Votes(helpful=15, not_helpful=5), today=TODAY)[0] == nothing        # 0.75
    assert quality_points(bare, Votes(helpful=10, not_helpful=10), today=TODAY)[0] == nothing       # 0.5
    assert quality_points(bare, Votes(helpful=9, not_helpful=11), today=TODAY)[0] == nothing - 3    # 0.45
    pts, reasons = quality_points(bare, Votes(helpful=18, not_helpful=2), today=TODAY)
    assert pts == nothing + 2 and "90% of 20 users found it helpful" in reasons


def test_votes_model():
    assert Votes(0, 0).share == 0.0
    assert Votes(3, 1).total == 4 and Votes(3, 1).share == 0.75


# --- pick ---

def test_pick_free_budget_excludes_paid_and_sorts_by_points():
    picks = pick("sql", catalog=fixture_resources(), today=TODAY)
    ids = [p.resource.id for p in picks]
    assert ids == ["northfield-sql-basics", "kyiv-open-sql-uk"]
    assert picks[0].points > picks[1].points
    assert all(isinstance(p, Pick) and p.reasons for p in picks)


def test_pick_unknown_skill_or_empty_catalog():
    assert pick("cobol", catalog=fixture_resources(), today=TODAY) == []
    assert pick("sql", catalog=[], today=TODAY) == []
    assert pick("sql", catalog=fixture_resources(), limit=0, today=TODAY) == []


def test_pick_limit():
    pool = [make(f"item-{i}") for i in range(6)]
    assert len(pick("sql", catalog=pool, limit=3, today=TODAY)) == 3
    assert len(pick("sql", catalog=pool, limit=1, today=TODAY)) == 1
    assert len(pick("sql", catalog=pool, limit=10, today=TODAY)) == 6


def test_pick_never_returns_non_active():
    pool = [make("item-a"), make("item-b", status="needs_check"), make("item-c", status="dead")]
    assert [p.resource.id for p in pick("sql", catalog=pool, budget="any", today=TODAY)] == ["item-a"]


def test_pick_is_deterministic_and_ties_break_by_id():
    pool = [make(i) for i in ("item-c", "item-a", "item-d", "item-b")]
    expected = ["item-a", "item-b", "item-c", "item-d"]
    for seed in range(5):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)
        got = [p.resource.id for p in pick("sql", catalog=shuffled, limit=4, today=TODAY)]
        assert got == expected
    first = pick("sql", catalog=pool, today=TODAY)
    second = pick("sql", catalog=pool, today=TODAY)
    assert [(p.resource.id, p.points, p.reasons) for p in first] == [(p.resource.id, p.points, p.reasons) for p in second]


def test_pick_budget_any_keeps_best_free_first_and_at_most_one_paid():
    weak_free = make("free-weak", hands_on=False, rating=None, evidence=evidence(1), updated_year=None)
    strong_paid_1 = make("paid-one", cost="paid", price_usd=40, evidence=evidence(3))
    strong_paid_2 = make("paid-two", cost="paid", price_usd=60, evidence=evidence(3))
    pool = [strong_paid_2, strong_paid_1, weak_free]
    # Without a budget the paid ones do not exist.
    assert [p.resource.id for p in pick("sql", catalog=pool, today=TODAY)] == ["free-weak"]
    got = pick("sql", catalog=pool, budget="any", limit=5, today=TODAY)
    ids = [p.resource.id for p in got]
    assert ids[0] == "free-weak"                       # best free first although paid has more points
    assert got[1].points > got[0].points               # the paid one really scores higher
    assert ids == ["free-weak", "paid-one"]            # only one paid item, the better one (tie by id)
    assert sum(p.resource.cost == "paid" for p in got) == 1


def test_pick_budget_any_without_free_returns_one_paid():
    pool = [make("paid-b", cost="paid", price_usd=9), make("paid-a", cost="paid", price_usd=9)]
    assert [p.resource.id for p in pick("sql", catalog=pool, budget="any", today=TODAY)] == ["paid-a"]


def test_pick_budget_any_with_fixture_catalog():
    ids = [p.resource.id for p in pick("sql", catalog=fixture_resources(), budget="any", limit=5, today=TODAY)]
    assert ids[0] == "northfield-sql-basics"
    assert "brightpath-advanced-sql" in ids and len(ids) == 3


def test_pick_never_shows_ukrainian_only_resources_to_english_readers():
    en = make("aaa-english", languages=["en"])
    uk = make("zzz-ukrainian", languages=["uk"])
    both = make("mmm-both", languages=["en", "uk"])
    ids = [p.resource.id for p in pick("sql", catalog=[en, uk, both], language="English", today=TODAY)]
    assert "zzz-ukrainian" not in ids and set(ids) == {"aaa-english", "mmm-both"}
    # Ukrainian readers still see everything, Ukrainian first.
    ids_uk = [p.resource.id for p in pick("sql", catalog=[en, uk, both], language="Ukrainian", today=TODAY)]
    assert set(ids_uk) == {"aaa-english", "zzz-ukrainian", "mmm-both"}
    # No language given: nothing is filtered.
    assert len(pick("sql", catalog=[en, uk, both], today=TODAY)) == 3


def test_pick_prefers_ukrainian_for_ukrainian_feedback_language():
    en = make("aaa-english", languages=["en"])
    uk = make("zzz-ukrainian", languages=["uk"])
    assert [p.resource.id for p in pick("sql", catalog=[en, uk], today=TODAY)] == ["aaa-english", "zzz-ukrainian"]
    got = pick("sql", catalog=[en, uk], language="Ukrainian", today=TODAY)
    assert [p.resource.id for p in got] == ["zzz-ukrainian", "aaa-english"]
    assert got[0].points == got[1].points + 2
    assert "In Ukrainian" in got[0].reasons
    # The English-language user does not get the Ukrainian course preferred.
    assert pick("sql", catalog=[en, uk], language="English", today=TODAY)[0].resource.id == "aaa-english"


def test_pick_level_match_is_a_bonus_not_a_filter():
    beginner = make("zzz-beginner", level="beginner")
    advanced = make("aaa-advanced", level="advanced")
    got = pick("sql", catalog=[advanced, beginner], level="beginner", today=TODAY)
    assert [p.resource.id for p in got] == ["zzz-beginner", "aaa-advanced"]


def test_pick_applies_votes():
    good = make("aaa-good")
    bad = make("bbb-bad")
    votes = {"aaa-good": Votes(helpful=2, not_helpful=17), "bbb-bad": Votes(helpful=20, not_helpful=0)}
    assert [p.resource.id for p in pick("sql", catalog=[good, bad], votes=votes, today=TODAY)] == ["bbb-bad", "aaa-good"]
    few = {"aaa-good": Votes(helpful=0, not_helpful=19)}
    assert [p.resource.id for p in pick("sql", catalog=[good, bad], votes=few, today=TODAY)] == ["aaa-good", "bbb-bad"]


def test_quality_points_main_topic_bonus_is_optional_and_visible():
    r = replace(base(), skills=["sql", "python"])
    plain, plain_reasons = quality_points(r, today=TODAY)
    main, main_reasons = quality_points(r, today=TODAY, skill="sql")
    other, other_reasons = quality_points(r, today=TODAY, skill="python")
    assert main == plain + 3 and "Main topic" in main_reasons
    assert other == plain and other_reasons == plain_reasons
    assert quality_points(r, today=TODAY, skill=None) == (plain, plain_reasons)


def test_pick_main_topic_beats_a_secondary_skill_resource():
    primary = make("zzz-primary", skills=["sql", "python"])
    secondary = make("aaa-secondary", skills=["python", "sql"])  # equal points otherwise, wins the id tie-break
    got = pick("sql", catalog=[secondary, primary], today=TODAY)
    assert [p.resource.id for p in got] == ["zzz-primary", "aaa-secondary"]
    assert got[0].points == got[1].points + 3
    assert "Main topic" in got[0].reasons and "Main topic" not in got[1].reasons
    # Even a secondary resource with more points than the primary one (here: 3 evidence vs 0) loses by at most 3.
    weak = make("bbb-weak-primary", skills=["sql"], hands_on=False, rating=None, evidence=evidence(1),
                updated_year=None)
    strong = make("ccc-strong-secondary", skills=["python", "sql"])
    assert pick("sql", catalog=[strong, weak], today=TODAY)[0].resource.id == "ccc-strong-secondary"


def test_pick_returns_secondary_skill_resources_when_primary_ones_run_out():
    primary = make("zzz-primary", skills=["sql"])
    secondary_a = make("aaa-secondary", skills=["python", "sql"])
    secondary_b = make("bbb-secondary", skills=["pandas", "sql"])
    got = [p.resource.id for p in pick("sql", catalog=[secondary_b, primary, secondary_a], limit=3, today=TODAY)]
    assert got == ["zzz-primary", "aaa-secondary", "bbb-secondary"]
    assert [p.resource.id for p in pick("sql", catalog=[secondary_b, primary, secondary_a], limit=1, today=TODAY)] \
        == ["zzz-primary"]
    assert [p.resource.id for p in pick("sql", catalog=[secondary_a], limit=3, today=TODAY)] == ["aaa-secondary"]


def test_pick_main_topic_stays_deterministic():
    pool = [make(f"item-{c}", skills=["sql", "python"] if c in "bd" else ["python", "sql"]) for c in "abcd"]
    expected = [p.resource.id for p in pick("sql", catalog=pool, limit=4, today=TODAY)]
    assert expected == ["item-b", "item-d", "item-a", "item-c"]
    for seed in range(5):
        shuffled = pool[:]
        random.Random(seed).shuffle(shuffled)
        assert [p.resource.id for p in pick("sql", catalog=shuffled, limit=4, today=TODAY)] == expected


def test_real_catalog_first_pick_is_a_main_topic_resource_when_one_exists():
    pool = select_mod.default_catalog()
    skills = [s.id for s in load_skills()]
    checked = 0
    for budget in ("free", "any"):
        for skill in skills:
            primary = [r for r in pool if r.status == "active" and r.skills and r.skills[0] == skill
                       and "en" in r.languages and (budget == "any" or r.cost != "paid")]
            if not primary:
                continue
            first = pick(skill, budget=budget, language="English", limit=1)
            assert first, (budget, skill)
            assert first[0].resource.skills[0] == skill, (
                f"{skill} ({budget}): first pick {first[0].resource.id} is only a secondary-skill resource")
            checked += 1
    assert checked >= 40


# --- build_plan ---

def _p(rid, level, hours, hands_on=True, output="An output"):
    r = make(rid, level=level, hours=hours, hands_on=hands_on, output=output)
    return Pick(resource=r, points=5, reasons=[])


def test_build_plan_orders_by_level_and_converts_hours_to_weeks():
    picks = [_p("adv", "advanced", 20, output="Window-function report"), _p("beg", "beginner", 8),
             _p("mid", "intermediate", 30)]
    plan = build_plan(picks, 5)
    assert [s.resource.id for s in plan[:-1]] == ["beg", "mid", "adv"]
    assert [(s.week_start, s.week_end) for s in plan[:-1]] == [(1, 2), (3, 8), (9, 12)]
    assert [s.order for s in plan] == [1, 2, 3, 4]
    assert [s.is_final for s in plan] == [False, False, False, True]


def test_build_plan_final_step_names_the_deliverable():
    picks = [_p("beg", "beginner", 8, output="A SQL repo"), _p("adv", "advanced", 20, output="Window-function report")]
    plan = build_plan(picks, 10)
    final = plan[-1]
    assert final.resource is None and final.is_final
    assert final.title == "Build and publish: Window-function report"
    assert "Window-function report" in final.cv_line
    assert final.week_start == plan[-2].week_end + 1


def test_build_plan_final_step_prefers_hands_on_output():
    picks = [_p("beg", "beginner", 8, hands_on=True, output="A SQL repo"),
             _p("adv", "advanced", 20, hands_on=False, output="A certificate")]
    assert build_plan(picks, 5)[-1].title == "Build and publish: A SQL repo"


def test_build_plan_weeks_are_ceil_and_at_least_one():
    plan = build_plan([_p("a", "beginner", 6), _p("b", "beginner", 1)], 5)
    assert (plan[0].week_start, plan[0].week_end) == (1, 2)    # ceil(6 / 5) = 2
    assert (plan[1].week_start, plan[1].week_end) == (3, 3)    # at least one week
    assert build_plan([_p("a", "beginner", 6)], 3)[0].week_end == 2
    assert build_plan([_p("a", "beginner", 6)], 0)[0].week_end == 6   # guarded: treated as 1 hour per week


def test_build_plan_same_level_keeps_rank_order_and_empty_is_empty():
    plan = build_plan([_p("second", "beginner", 5), _p("first", "beginner", 5)], 5)
    assert [s.resource.id for s in plan[:-1]] == ["second", "first"]
    assert build_plan([], 5) == []


# --- events ---

def test_resource_events_are_registered_and_payloads_filtered():
    assert events.RESOURCE_CLICKED == "resource_clicked"
    assert events.RESOURCE_RATED == "resource_rated"
    assert events.RESOURCE_MISSING == "resource_missing"
    for kind in ("resource_clicked", "resource_rated", "resource_missing"):
        assert kind in events.KINDS and kind in events.ALLOWED_FIELDS

    click = events.make_payload(events.RESOURCE_CLICKED, skill="sql", resource_id="mode-sql", rank=1,
                                cv_text="my private cv", title="not allowed")
    assert click == {"skill": "sql", "resource_id": "mode-sql", "rank": 1}
    rated = events.make_payload(events.RESOURCE_RATED, skill="sql", resource_id="mode-sql", rank=2, helpful=True,
                                url="https://x.example")
    assert rated == {"skill": "sql", "resource_id": "mode-sql", "rank": 2, "helpful": True}
    missing = events.make_payload(events.RESOURCE_MISSING, skill="cobol", gap_text="free text from a CV")
    assert missing == {"skill": "cobol"}


def test_resource_event_payload_drops_complex_values():
    assert events.make_payload(events.RESOURCE_MISSING, skill={"a": 1}) == {}
    assert len(events.make_payload(events.RESOURCE_MISSING, skill="s" * 500)["skill"]) == events.MAX_STR_CHARS


# --- skills.json vocabulary ---

SPEC_SKILL_IDS = {
    # data
    "sql", "excel", "power bi", "tableau", "statistics", "r", "data visualization", "a/b testing",
    "google analytics", "python", "pandas",
    # software
    "javascript", "typescript", "java", "git", "algorithms", "system design", "docker", "aws", "linux", "rest api",
    "react", "software testing", "machine learning", "deep learning", "prompt engineering",
    # business
    "financial modeling", "valuation", "accounting", "powerpoint", "market research", "case interview",
    "marketing analytics", "project management", "product management", "scrum", "business analysis", "acca",
    # language_career
    "english", "ielts", "toefl", "business english", "interview skills", "public speaking", "business writing",
    "linkedin profile", "github profile",
    # law_psychology
    "legal research", "legal writing", "contract law basics", "gdpr", "compliance", "research methods", "spss",
    "academic writing", "ux research", "people analytics",
}
SHARED_ALIASES = {"python", "statistics", "r", "excel"}
COMMON_WORDS = {
    "a", "an", "the", "and", "or", "of", "to", "in", "on", "for", "with", "data", "analysis", "analyst", "model",
    "models", "learning", "research", "business", "project", "projects", "management", "english", "writing",
    "speaking", "law", "legal", "interview", "skills", "skill", "design", "testing", "test", "tests", "finance",
    "marketing", "product", "code", "coding", "web", "cloud", "network", "report", "reports", "team", "work",
    "tool", "tools", "computer", "science", "software", "engineering", "analytics", "development", "experience",
    "knowledge", "communication", "presentation", "presentations", "excel", "system", "systems", "language",
    "languages", "program", "programming", "paper", "papers", "user", "users", "group", "time", "year", "years",
    "first", "new", "good", "best", "high", "low", "one", "two", "all", "use", "used", "make", "write", "read",
    # Ukrainian common words. Ukrainian aliases are stems, so a stem equal to one of these would match far too much.
    "дані", "дан", "аналіз", "аналіт", "дослідж", "дослідженн", "дослідження", "робота", "робот", "проєкт",
    "проект", "менеджмент", "менеджер", "мова", "мов", "навчанн", "навчання", "досвід", "навичк", "навички",
    "бізнес", "облік", "право", "прав", "письмо", "письм", "звіт", "звітн", "тест", "тестуванн", "знанн", "знання",
    "команда", "команд", "система", "систем", "програм", "програма", "розробк", "розробка", "комунікац", "модел",
    "модель", "ринок", "ринк", "оцінк", "оцінка", "управлінн", "управління", "виступ", "стратег", "стратегія",
    "документ", "договір", "договор", "інформац", "освіта", "курс", "урок", "вміння", "компанія", "компан",
}


def test_skills_json_is_valid_and_complete():
    skills = load_skills()
    ids = [s.id for s in skills]
    assert len(ids) == len(set(ids)), "duplicate skill ids"
    assert SPEC_SKILL_IDS <= set(ids), f"missing: {sorted(SPEC_SKILL_IDS - set(ids))}"
    assert 55 <= len(ids) <= 70
    for s in skills:
        assert s.id == s.id.lower() and s.id == s.id.strip() and s.id, s.id
        assert s.label.strip(), s.id
        assert s.programs and set(s.programs) <= set(PROGRAMS), (s.id, s.programs)
        assert len(set(s.programs)) == len(s.programs), s.id


def _is_cyrillic(alias: str) -> bool:
    return any(not c.isascii() for c in alias)


def test_skills_json_alias_rules():
    skills = load_skills()
    owners: dict[str, list[str]] = {}
    for s in skills:
        latin = [a for a in s.aliases if not _is_cyrillic(a)]
        ukrainian = [a for a in s.aliases if _is_cyrillic(a)]
        assert 3 <= len(latin) <= 8, (s.id, len(latin))
        assert len(ukrainian) <= 10, (s.id, len(ukrainian))
        assert len(set(s.aliases)) == len(s.aliases), f"repeated alias in {s.id}"
        for a in s.aliases:
            assert a == a.lower() and a == a.strip(), (s.id, a)
            assert len(a) >= 2, (s.id, a)
            assert a not in COMMON_WORDS, (s.id, a)
            owners.setdefault(a, []).append(s.id)
        for a in ukrainian:
            # Ukrainian aliases are stems of at least 4 letters, never a single common word, and no stray spaces.
            assert len(a.replace(" ", "")) >= 4 and "  " not in a, (s.id, a)
    for alias, ids in owners.items():
        if len(ids) > 1:
            assert alias in SHARED_ALIASES, f"alias {alias!r} is in several skills: {ids}"
    # An alias must not be another skill's id or label (that would make the match ambiguous).
    names = {}
    for s in skills:
        names[s.id] = s.id
        names[s.label.lower()] = s.id
    for s in skills:
        for a in s.aliases:
            owner = names.get(a)
            assert owner is None or owner == s.id or a in SHARED_ALIASES, (s.id, a, owner)


def test_skills_json_has_the_sql_aliases_from_the_spec():
    sql = next(s for s in load_skills() if s.id == "sql")
    for a in ("postgresql", "mysql", "group by", "window functions"):
        assert a in sql.aliases
    # Everyday words that also name other things were removed: "NoSQL databases" is not SQL.
    assert "databases" not in sql.aliases and "joins" not in sql.aliases


def test_skills_json_has_no_everyday_word_aliases():
    banned = {"databases", "joins", "forecasting", "risk management", "ci/cd", "interviewing", "experimentation",
              "scalability", "containers", "transformers", "prompting", "sprints", "professional profile",
              "terms and conditions"}
    present = {a for s in load_skills() for a in s.aliases} & banned
    assert not present, present


def test_skills_json_has_ukrainian_aliases_for_the_main_skills():
    by_id_ = {s.id: s for s in load_skills()}
    for sid in ("english", "statistics", "accounting", "project management", "product management", "business writing",
                "interview skills", "public speaking", "ux research", "gdpr", "legal research", "academic writing",
                "financial modeling", "valuation"):
        assert any(_is_cyrillic(a) for a in by_id_[sid].aliases), sid


def test_every_alias_matches_its_own_skill():
    for s in load_skills():
        for a in s.aliases:
            assert s.id in match_skills(f"experience with {a} in projects") or len(
                match_skills(f"experience with {a} in projects")) == 3, (s.id, a)


def test_load_skills_errors_name_the_file(tmp_path):
    with pytest.raises(CatalogError, match="skills.json"):
        load_skills(tmp_path / "skills.json")
    (tmp_path / "skills.json").write_text("{bad")
    with pytest.raises(CatalogError, match="skills.json"):
        load_skills(tmp_path / "skills.json")
    (tmp_path / "skills.json").write_text(json.dumps({"skills": [{"id": "sql", "label": "SQL"}]}))
    with pytest.raises(CatalogError, match="aliases"):
        load_skills(tmp_path / "skills.json")
    (tmp_path / "skills.json").write_text(json.dumps({"nope": []}))
    with pytest.raises(CatalogError, match="'skills' list"):
        load_skills(tmp_path / "skills.json")


def test_fixture_skills_load():
    assert {s.id for s in fixture_skills()} == {"sql", "python", "pandas", "power bi", "ielts"}


# --- the real catalog (zero files is fine; a present file must be well formed and valid) ---

def test_real_catalog_files_pass_validate():
    resources = load_catalog()  # raises CatalogError, failing this test, when a present file is malformed
    errs = validate(resources, load_skills())
    assert not errs, "Catalog problems (fix the resource named at the start of each line):\n" + "\n".join(errs)
    ids = [r.id for r in resources]
    assert len(ids) == len(set(ids)), "duplicate resource ids across catalog files"


def test_real_catalog_is_sorted_into_known_clusters():
    for f in sorted(catalog_mod.CATALOG_DIR.glob("*.json")) if catalog_mod.CATALOG_DIR.is_dir() else []:
        data = json.loads(f.read_text(encoding="utf-8"))
        assert data["cluster"] in ("data", "software", "business", "language_career", "law_psychology"), f.name
