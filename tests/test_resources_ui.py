"""The vetted resources in the UI: the "Skills to build" tab and the Learn page, in demo mode with the fixture catalog."""

from dataclasses import replace
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block, Button

import ui.resources as ui_resources
from cvmax import config
from cvmax.learning import events
from cvmax.resources import CatalogError, load_catalog
from cvmax.resources import catalog as catalog_mod
from cvmax.resources import select as select_mod

APP = str(Path(__file__).resolve().parents[1] / "app.py")
FIXTURES = Path(__file__).parent / "fixtures" / "resources"
NO_RESOURCE = "We don't have a vetted resource for this yet."


@pytest.fixture(autouse=True)
def fixture_catalog(monkeypatch):
    """Demo mode, and the fixture catalog and vocabulary instead of the shipped ones."""
    monkeypatch.setenv("CVMAX_DEMO", "1")
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    # Every test runs a review: the shared in-memory counter must not hit the site-wide daily limit.
    monkeypatch.setitem(config.LIMITS, "analysis", (None, None))
    resources = load_catalog(FIXTURES / "catalog")
    monkeypatch.setattr(select_mod, "default_catalog", lambda: resources)
    monkeypatch.setattr(select_mod, "SKILLS_PATH", FIXTURES / "skills.json")
    monkeypatch.setattr(catalog_mod, "SKILLS_PATH", FIXTURES / "skills.json")
    return resources


# --- helpers ---

def review():
    """Demo review up to the results; the Skills to build tab is part of the page."""
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/analyze.py").run()
    at.checkbox(key="consent_analyze").check().run()
    at.text_input(key="target_role").input("Business Analyst").run()
    next(b for b in at.button if b.label == "Review my CV").click().run()
    assert not at.exception
    return at


def learn_page():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.switch_page("views/learn.py").run()
    assert not at.exception
    return at


def resource_labels(at) -> list[str]:
    """Titles of the resource cards (link buttons that point to the fixture providers)."""
    return [el.proto.label for el in at.get("link_button") if "example" in el.proto.url]


def captions(at) -> list[str]:
    return [c.value for c in at.caption]


def markdown(at) -> list[str]:
    return [m.value for m in at.markdown]


def resource_events(kind: str) -> list[dict]:
    from ui.account import get_db

    return [e for e in get_db().events if e["kind"] == kind]


def set_budget(at, value: str):
    radio = next(r for r in at.radio if str(r.key).endswith("_budget"))
    radio.set_value(value).run()
    assert not at.exception


def as_button(at, el) -> Button:
    """AppTest has no link button: swap the element for a Button so that a click reaches the on_click callback."""
    def walk(block):
        for k, child in list(block.children.items()):
            if isinstance(child, Block):
                found = walk(child)
                if found is not None:
                    return found
            elif getattr(child, "type", "") == "link_button" and child.proto.id == el.proto.id:
                button = Button(child.proto, child.root)
                block.children[k] = button
                return button
        return None

    return walk(at.main)


# --- the Skills to build tab ---

def test_sql_gap_shows_cards_with_cost_badge_and_reasons():
    at = review()
    labels = resource_labels(at)
    assert "SQL Basics for Analysts" in labels
    text = "\n".join(markdown(at))
    assert ":gray-badge[Free]" in text
    assert "**Why we picked it:**" in text
    assert any("Hands\\-on" in c for c in captions(at))
    assert any(e.label == "How we checked this" for e in at.expander)
    # An expander with an icon is reported by AppTest as a "status" block.
    assert any(getattr(e.proto, "label", "") == "How we pick resources" for e in at.get("status"))
    assert any(l.proto.label == "Report a bad link or suggest one" for l in at.get("page_link"))
    assert "Not sponsored. No affiliate links." in text


def test_plan_ends_with_build_and_publish():
    at = review()
    text = "\n".join(markdown(at))
    assert "**Your plan** at 5 hours a week" in text
    assert "Build and publish\\:" in text and "Add to your CV\\:" in text


def test_free_only_hides_the_paid_resource_and_free_and_paid_adds_one_after_the_best_free():
    at = review()
    assert "Advanced SQL and Window Functions" not in resource_labels(at)
    set_budget(at, "Free and paid")
    labels = resource_labels(at)
    assert labels.count("Advanced SQL and Window Functions") == 1
    assert labels[0] == "SQL Basics for Analysts"  # the best free one is first
    assert ":red-badge[Paid \\$49 per month]" in "\n".join(markdown(at))
    set_budget(at, "Free only")
    assert "Advanced SQL and Window Functions" not in resource_labels(at)


def test_hours_a_week_changes_the_plan():
    at = review()
    next(r for r in at.radio if str(r.key).endswith("_hours")).set_value(10).run()
    assert "**Your plan** at 10 hours a week" in "\n".join(markdown(at))


def test_gap_without_a_match_says_so_and_logs_resource_missing_once():
    at = review()
    gap = at.session_state["analysis"].gaps[1]
    gap.item = "Underwater basket weaving"
    gap.how_to_close = "Join a pottery workshop."
    before = len(resource_events(events.RESOURCE_MISSING))
    at.run()
    assert NO_RESOURCE in captions(at)
    at.run()  # the same gap again in the same analysis: no second event
    new = resource_events(events.RESOURCE_MISSING)[before:]
    # The gap text is model output derived from the CV: only the constant is logged.
    assert [e["payload"] for e in new] == [{"skill": "unmatched"}]


def test_missing_gap_text_is_never_logged():
    at = review()
    gap = at.session_state["analysis"].gaps[1]
    gap.item = "Finish the Oschadbank internship report " * 4
    gap.how_to_close = "Join a pottery workshop."
    before = len(resource_events(events.RESOURCE_MISSING))
    at.run()
    new = resource_events(events.RESOURCE_MISSING)[before:]
    assert [e["payload"] for e in new] == [{"skill": "unmatched"}]
    assert "Oschadbank" not in repr(new)


def test_matched_skill_without_a_resource_logs_the_skill_id_not_the_text(monkeypatch):
    # The gap matches a skill, but the catalog has nothing vetted for it under the current settings.
    monkeypatch.setattr(select_mod, "default_catalog", lambda: [])
    at = review()
    payloads = [e["payload"] for e in resource_events(events.RESOURCE_MISSING)]
    assert payloads and all(p["skill"] in {"sql", "ielts"} for p in payloads)
    assert set(payloads[0]) == {"skill"}


def test_render_resources_ignores_missing_text_for_logging(monkeypatch):
    logged = []
    monkeypatch.setattr(ui_resources, "log_event", lambda kind, **fields: logged.append((kind, fields)))
    state: dict = {}
    monkeypatch.setattr(ui_resources.st, "session_state", state)
    monkeypatch.setattr(ui_resources.st, "caption", lambda *a, **k: None)
    for _ in range(2):  # the same call twice: still one event
        n = ui_resources.render_resources(
            [], key="k_g0", feedback_language="English", budget="free", hours_per_week=5,
            missing_text="Finish the Oschadbank internship report", scope="analysis-1",
        )
        assert n == 0
    assert logged == [(events.RESOURCE_MISSING, {"skill": "unmatched"})]


def test_helpful_click_logs_resource_rated_once_and_disables_the_buttons():
    at = review()
    before = len(resource_events(events.RESOURCE_RATED))
    next(b for b in at.button if b.label == "Helpful").click().run()
    assert not at.exception
    new = resource_events(events.RESOURCE_RATED)[before:]
    assert len(new) == 1
    assert new[0]["payload"]["helpful"] is True and new[0]["payload"]["resource_id"] == "northfield-sql-basics"
    assert new[0]["payload"]["skill"] == "sql" and new[0]["payload"]["rank"] == 1
    assert "Thanks" in captions(at)
    rated = [b for b in at.button if b.label in ("Helpful", "Not helpful") and b.disabled]
    assert len(rated) == 2  # both buttons of that card
    at.run()
    assert len(resource_events(events.RESOURCE_RATED)[before:]) == 1


def test_not_helpful_click_logs_false():
    at = review()
    before = len(resource_events(events.RESOURCE_RATED))
    next(b for b in at.button if b.label == "Not helpful").click().run()
    new = resource_events(events.RESOURCE_RATED)[before:]
    assert len(new) == 1 and new[0]["payload"]["helpful"] is False


def test_opening_the_link_logs_resource_clicked():
    at = review()
    before = len(resource_events(events.RESOURCE_CLICKED))
    link = next(el for el in at.get("link_button") if el.proto.label == "SQL Basics for Analysts")
    as_button(at, link).click().run()
    assert not at.exception
    new = resource_events(events.RESOURCE_CLICKED)[before:]
    assert [e["payload"] for e in new] == [{"skill": "sql", "resource_id": "northfield-sql-basics", "rank": 1}]


def test_events_carry_ids_and_skills_only():
    at = review()
    next(b for b in at.button if b.label == "Helpful").click().run()
    for e in resource_events(events.RESOURCE_RATED):
        assert set(e["payload"]) <= {"skill", "resource_id", "rank", "helpful"}


def test_markdown_in_a_catalog_text_is_escaped(monkeypatch, fixture_catalog):
    nasty = "**Bold** [click](https://evil.example/x) <b>x</b>"
    patched = [
        replace(r, title=nasty, provider="_Prov_ [p](https://evil.example/p)",
                why_picked="Because [here](https://evil.example/y) and **more**")
        if r.id == "northfield-sql-basics" else r
        for r in fixture_catalog
    ]
    monkeypatch.setattr(select_mod, "default_catalog", lambda: patched)
    at = review()
    label = next(el.proto.label for el in at.get("link_button") if "learn.northfield-example.org" in el.proto.url)
    assert "**Bold**" not in label and "[click](" not in label and "\\*\\*Bold" in label
    text = "\n".join(markdown(at))
    assert "[p](https://evil.example/p)" not in text and "[here](https://evil.example/y)" not in text
    assert "**more**" not in text
    assert "[click](https://evil.example/x)" not in text


def test_link_with_a_host_other_than_the_catalog_host_is_not_rendered_as_a_link(monkeypatch):
    real_pick = ui_resources.pick

    def tampered(*args, **kwargs):
        return [replace(p, resource=replace(p.resource, url="https://elsewhere-example.net/sql")) for p in real_pick(*args, **kwargs)]

    monkeypatch.setattr(ui_resources, "pick", tampered)
    at = review()
    assert not at.exception
    assert not any("elsewhere-example.net" in el.proto.url for el in at.get("link_button"))
    assert not any(l in resource_labels(at) for l in ("SQL Basics for Analysts", "Бази даних і SQL"))
    assert any("did not pass our safety check" in c for c in captions(at))
    assert any("**SQL Basics for Analysts**" in m for m in markdown(at))  # the title is still shown, as plain text


@pytest.mark.parametrize("url", ["http://learn.northfield-example.org/sql-basics/", "https://bit.ly/abc",
                                 "https://learn.northfield-example.org/sql-basics/?ref=partner"])
def test_unsafe_catalog_links_are_not_rendered(monkeypatch, fixture_catalog, url):
    patched = [replace(r, url=url) if r.id == "northfield-sql-basics" else r for r in fixture_catalog]
    monkeypatch.setattr(select_mod, "default_catalog", lambda: patched)
    at = review()
    assert not any(el.proto.url == url for el in at.get("link_button"))


def test_catalog_error_gives_a_soft_message_in_the_tab(monkeypatch):
    def broken():
        raise CatalogError("data.json: invalid JSON")

    monkeypatch.setattr(select_mod, "default_catalog", broken)
    at = review()
    assert not at.exception
    assert any(i.value == ui_resources.SOFT_ERROR for i in at.info)
    assert "invalid JSON" not in "\n".join(markdown(at) + [i.value for i in at.info])


def test_vocabulary_error_gives_a_soft_message_in_the_tab(monkeypatch, tmp_path):
    monkeypatch.setattr(select_mod, "SKILLS_PATH", tmp_path / "missing.json")
    at = review()
    assert not at.exception
    assert any(i.value == ui_resources.SOFT_ERROR for i in at.info)
    assert any(c.startswith("**SQL") or "SQL" in c for c in markdown(at))  # the gaps themselves still show


def demand_rows(sql: int, ielts: int) -> list[dict]:
    return [{"skill": "sql", "postings": sql, "total_postings": 10, "window_days": 30},
            {"skill": "ielts", "postings": ielts, "total_postings": 10, "window_days": 30}]


def gap_order(at) -> list[str]:
    heads = [m for m in markdown(at) if m.startswith(("**SQL", "**IELTS"))]
    return ["sql" if h.startswith("**SQL") else "ielts" for h in heads]


def test_gaps_keep_their_order_without_demand_data():
    assert gap_order(review()) == ["sql", "ielts"]


def test_gaps_are_reordered_by_market_demand(monkeypatch):
    import ui.account

    monkeypatch.setattr(ui.account, "skill_demand_cached", lambda program, region: demand_rows(sql=2, ielts=8))
    at = review()
    assert not at.exception
    assert gap_order(at) == ["ielts", "sql"]
    assert any("asked for in 80% of postings" in c for c in captions(at))


def test_equal_demand_keeps_the_impact_order(monkeypatch):
    import ui.account

    monkeypatch.setattr(ui.account, "skill_demand_cached", lambda program, region: demand_rows(sql=5, ielts=5))
    assert gap_order(review()) == ["sql", "ielts"]  # the SQL gap has the higher impact


# --- the Learn page ---

def test_learn_page_shows_cards_for_the_chosen_skill():
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("sql").run()
    assert not at.exception
    # English by default: the Ukrainian-only resource is not shown.
    assert resource_labels(at) == ["SQL Basics for Analysts"]
    assert "SQL: 1 shown for your settings; 2 more with Free and paid or in Ukrainian" in captions(at)
    at.radio(key="learn_language").set_value("Ukrainian").run()
    assert not at.exception
    assert resource_labels(at) == ["SQL Basics for Analysts", "Бази даних і SQL"] or \
        set(resource_labels(at)) == {"SQL Basics for Analysts", "Бази даних і SQL"}
    text = "\n".join(markdown(at))
    assert ":gray-badge[Ukrainian]" in text
    assert "Not sponsored. No affiliate links." in text
    assert any(l.proto.label == "Report a bad link or suggest one" for l in at.get("page_link"))


def test_learn_page_free_and_paid_and_another_skill():
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("sql").run()
    set_budget(at, "Free and paid")
    assert "Advanced SQL and Window Functions" in resource_labels(at)
    at.selectbox(key="learn_skill").set_value("ielts").run()
    assert resource_labels(at) == ["IELTS Practice Tests"]
    assert "1 vetted resource for IELTS" in captions(at)


def learn_caption(at) -> str:
    return next(c for c in captions(at) if "vetted resource" in c or "shown for your settings" in c
                or c.startswith(("No free resources", "No English resources")) or "the best" in c)


def test_learn_page_count_follows_the_language_and_budget_settings():
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("sql").run()
    # English, Free only: one card; the paid one and the Ukrainian one are named as hidden.
    assert len(resource_labels(at)) == 1
    assert learn_caption(at) == "SQL: 1 shown for your settings; 2 more with Free and paid or in Ukrainian"
    at.radio(key="learn_language").set_value("Ukrainian").run()
    assert len(resource_labels(at)) == 2
    assert learn_caption(at) == "SQL: 2 shown for your settings; 1 more with Free and paid"
    set_budget(at, "Free and paid")
    assert len(resource_labels(at)) == 3
    assert learn_caption(at) == "3 vetted resources for SQL"
    at.radio(key="learn_language").set_value("English").run()
    assert len(resource_labels(at)) == 2
    assert learn_caption(at) == "SQL: 2 shown for your settings; 1 more in Ukrainian"


def test_learn_page_says_when_only_paid_options_exist(monkeypatch, fixture_catalog):
    paid_only = [replace(r, cost="paid", price_usd=49, price_note="per month") if r.id == "openprep-ielts-practice" else r
                 for r in fixture_catalog]
    monkeypatch.setattr(select_mod, "default_catalog", lambda: paid_only)
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("ielts").run()
    assert not at.exception
    assert resource_labels(at) == []
    assert "No free resources for this skill yet; 1 paid option exists: switch Show to Free and paid." in captions(at)
    assert "1 vetted resource for IELTS" not in captions(at)
    set_budget(at, "Free and paid")
    assert resource_labels(at) == ["IELTS Practice Tests"]
    assert learn_caption(at) == "1 vetted resource for IELTS"


def test_learn_page_says_when_only_ukrainian_options_exist(monkeypatch, fixture_catalog):
    uk_only = [replace(r, languages=["uk"]) if r.id == "openprep-ielts-practice" else r for r in fixture_catalog]
    monkeypatch.setattr(select_mod, "default_catalog", lambda: uk_only)
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("ielts").run()
    assert resource_labels(at) == []
    assert "No English resources for this skill yet; 1 in Ukrainian: switch Language to Ukrainian." in captions(at)


def test_learn_page_count_is_capped_at_the_cards_shown(monkeypatch, fixture_catalog):
    base = next(r for r in fixture_catalog if r.id == "pycamp-python-intro")
    many = [*fixture_catalog, *(replace(base, id=f"pycamp-python-extra-{i}") for i in range(5))]
    monkeypatch.setattr(select_mod, "default_catalog", lambda: many)
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("python").run()
    assert len(resource_labels(at)) == 3
    assert "Python: the best 3 of 7 vetted resources" in captions(at)


def test_cost_badge_shows_the_billing_basis():
    base = next(r for r in load_catalog(FIXTURES / "catalog") if r.id == "northfield-sql-basics")
    paid = replace(base, cost="paid", price_usd=59, price_note="per month with Coursera Plus")
    assert ui_resources.cost_badge(paid) == ":red-badge[Paid \\$59 per month with Coursera Plus]"
    fee = replace(base, cost="paid", price_usd=72, price_note="per 6 months, plus a one-time $25 fee")
    badge = ui_resources.cost_badge(fee)
    assert badge.startswith(":red-badge[Paid \\$72 per 6 months") and "\\$25 fee]" in badge
    assert "$25" not in badge.replace("\\$25", "")  # no bare dollar sign pair that would turn into maths
    aws = replace(base, cost="freemium", price_usd=3, price_note="AWS usage per month after the free tier")
    assert "full version" not in ui_resources.cost_badge(aws)
    assert ui_resources.cost_badge(aws).startswith(":gray-badge[Freemium \\$3 AWS usage per month")
    assert ui_resources.cost_badge(replace(base, cost="paid", price_usd=49)) == ":red-badge[Paid \\$49]"
    assert ui_resources.cost_badge(replace(base, cost="free-audit", price_usd=None)) == ":gray-badge[Free to audit]"
    assert ui_resources.cost_badge(base) == ":gray-badge[Free]"


def test_learn_page_with_an_empty_catalog(monkeypatch):
    monkeypatch.setattr(select_mod, "default_catalog", lambda: [])
    at = learn_page()
    assert "0 vetted resources for SQL" in captions(at)
    assert NO_RESOURCE in captions(at)
    assert resource_labels(at) == []
    assert any(m == "**Not sponsored. No affiliate links.**" for m in markdown(at))


def test_learn_page_with_a_broken_catalog_shows_a_soft_message(monkeypatch):
    def broken():
        raise CatalogError("data.json: invalid JSON")

    monkeypatch.setattr(select_mod, "default_catalog", broken)
    at = learn_page()
    assert any(i.value == ui_resources.SOFT_ERROR for i in at.info)


def test_learn_page_does_not_need_sign_in_and_logs_with_the_anonymous_key():
    at = learn_page()
    at.selectbox(key="learn_skill").set_value("sql").run()
    next(b for b in at.button if b.label == "Helpful").click().run()
    last = resource_events(events.RESOURCE_RATED)[-1]
    assert last["user_key"].startswith("anon:")
    assert last["payload"]["resource_id"] in {"northfield-sql-basics", "kyiv-open-sql-uk"}


def test_feedback_language_is_passed_to_the_selection(monkeypatch):
    real_pick = ui_resources.pick
    seen = []

    def spy(*args, **kwargs):
        seen.append(kwargs.get("language"))
        return real_pick(*args, **kwargs)

    monkeypatch.setattr(ui_resources, "pick", spy)
    at = review()
    assert set(seen) == {"English"}
    seen.clear()
    at.session_state["profile"].feedback_language = "Ukrainian"
    at.run()
    assert set(seen) == {"Ukrainian"}


def test_navigation_has_the_learn_page_after_where_to_apply():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    assert not at.exception
    source = (Path(APP).parent / "app.py").read_text()
    assert source.index('"Where to apply"') < source.index('"Learn"') < source.index('"CV builder"')
    assert ":material/school:" in source
