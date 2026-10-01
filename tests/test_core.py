import io

import pytest
from docx import Document

from cvmax import config
from cvmax.analyze import analyze_cv
from cvmax.cv_input import CVReadError, load_cv
from cvmax.demo import DEMO_CV_TEXT, FakeClient, demo_analysis
from cvmax.edits import apply_edits, changes_markdown, text_to_docx
from cvmax.grill import GrillSession, answer, finalize, next_question
from cvmax.profile import Profile
from cvmax.prompts import analysis_system, load_rubric
from cvmax.schemas import Edit


def make_profile(**over):
    base = dict(
        program="economics_big_data", status="3 курс", background="", target_role="Data Analyst",
        company_type="Фінтех / банк", company_details="", level="Стажування", region="Європа",
        vacancy_text="", feedback_language="Ukrainian",
    )
    base.update(over)
    return Profile(**base)


def cv():
    return load_cv("cv.docx", _docx_bytes(DEMO_CV_TEXT))


def _docx_bytes(text):
    buf = io.BytesIO()
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(buf)
    return buf.getvalue()


def test_target_clarity_levels():
    assert make_profile(company_type="Не знаю / будь-яка").target_clarity()[0] == "низька"
    assert make_profile().target_clarity()[0] == "середня"
    assert make_profile(vacancy_text="x" * 300).target_clarity()[0] == "висока"


def test_every_program_has_rubric():
    from cvmax.profile import PROGRAMS
    for key in PROGRAMS:
        if key != "other":
            assert key in load_rubric(key) or len(load_rubric(key)) > len(load_rubric("other"))


def test_prompt_mentions_language_and_rubric():
    p = analysis_system(make_profile(feedback_language="English"))
    assert "English" in p and "Role disambiguation" in p and "Economics and Big Data" in p


def test_load_docx_and_blocks():
    c = cv()
    assert "Responsible for making reports in Excel" in c.text
    assert c.as_content_blocks()[0]["type"] == "text"


def test_load_rejects_bad_types():
    with pytest.raises(CVReadError):
        load_cv("cv.doc", b"x")
    with pytest.raises(CVReadError):
        load_cv("cv.pdf", b"not a pdf")


def test_analyze_sends_expected_request():
    client = FakeClient()
    a = analyze_cv(client, make_profile(), cv())
    assert a.overall_score == 58
    call = client.calls[0]
    assert call["model"] == config.MODEL
    assert call["output_config"] == {"effort": config.EFFORT_ANALYSIS}
    assert call["extra_body"] == {"fallbacks": "default"}
    assert "<vacancy_text>" in call["messages"][0]["content"][-1]["text"]


def test_apply_edits_replace_remove_add_and_loose_match():
    text = "Responsible for making\n reports in Excel\nDate of birth: 01.01.2004\nEnd"
    report = apply_edits(text, demo_analysis().edits)
    assert "Built [N] weekly Excel reports" in report.text
    assert "Date of birth" not in report.text
    assert "SQL (joins" in report.text and "NEW ITEMS" in report.text
    assert not report.not_found


def test_apply_edits_reports_missing():
    e = Edit(section="X", before="nonexistent line", after="y", reason="r", priority="low")
    assert apply_edits("abc", [e]).not_found == [e]


def test_grill_flow_stops_and_finalizes():
    client, p, c = FakeClient(), make_profile(), cv()
    g = GrillSession()
    q = next_question(client, p, c, g)
    assert q and g.pending is q
    for _ in range(10):
        if g.pending is None:
            break
        answer(g, "I organised 6 events for 300 students")
        next_question(client, p, c, g)
    assert g.finished and len(g.turns) == 3
    assert "Q1:" in g.transcript()
    result = finalize(client, p, c, g)
    assert result.edits[0].before == "Member of Student Council"


def test_grill_respects_question_limit():
    client, g = FakeClient(), GrillSession(max_questions=1)
    next_question(client, make_profile(), cv(), g)
    answer(g, "")
    assert next_question(client, make_profile(), cv(), g) is None
    assert g.finished and g.turns[0].answer == "(skipped)"


def test_exports():
    md = changes_markdown(demo_analysis().edits)
    assert "**Було:**" in md and "(прибрати)" in md
    assert text_to_docx("a\nb")[:2] == b"PK"


def test_unverified_terms_flags_invented_tools_and_numbers():
    from cvmax.edits import unverified_terms
    known = "Skills: Excel, Python, SQL. Made 4 reports in 12 branches. Kyiv School of Economics"
    after = "Built 4 reports for 12 branches using Python (BeautifulSoup, Pandas), SQL (PostgreSQL), VLOOKUP and Power BI, saving 30%"
    flagged = unverified_terms(after, known)
    assert {"BeautifulSoup", "Pandas", "PostgreSQL", "VLOOKUP", "Power Bi", "30"} <= set(flagged)
    assert not {"Python", "SQL", "4", "12", "Built"} & set(flagged)


def test_unverified_terms_ignores_placeholders_and_known_facts():
    from cvmax.edits import unverified_terms
    assert unverified_terms("Used [Pandas?] to clean [X] rows in Excel", "Excel") == []
    assert unverified_terms("Analysed 20,000 listings", "20000 оголошень") == []
    assert unverified_terms("a | linkedin.com/in/andrii", "a") == ["linkedin.com/in/andrii"]


def test_unverified_terms_understands_number_words():
    from cvmax.edits import unverified_terms
    assert unverified_terms("Automated ~50% of daily posts", "roughly half of the posts") == []
