import io

from pypdf import PdfReader

from cvmax.cv_render import has_placeholders, render_pdf, split_gpa
from cvmax.db import MemoryDB, SupabaseDB
from cvmax.demo import FakeClient, demo_built_cv
from cvmax.schemas import BuiltCV, CVEducation, CVEntry
from cvmax.structure import changed_bullets, structure_cv
from tests.test_db_builder import FakeHTTP


def entry(title, bullets, organization=""):
    return CVEntry(title=title, organization=organization, location="", dates="", bullets=bullets)


def make_cv(**fields):
    base = dict(full_name="Jane Doe", contact_line=[], summary="", education=[], experience=[], projects=[],
                activities=[], skills=[], languages=[], awards=[], notes_for_user=[])
    return BuiltCV(**{**base, **fields})


def test_render_pdf_one_page_with_text():
    pdf = render_pdf(demo_built_cv())
    assert pdf.startswith(b"%PDF")
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert demo_built_cv().full_name in text
    assert "EDUCATION" in text.upper()


def test_render_pdf_handles_ukrainian_and_symbols():
    cv = demo_built_cv().model_copy(update={"full_name": "Олена Петренко", "summary": "Grew revenue by 20% — fast"})
    reader = PdfReader(io.BytesIO(render_pdf(cv)))
    assert "Олена" in reader.pages[0].extract_text()


def test_split_gpa_moves_short_gpa_only():
    assert split_gpa(["GPA: 3.9/4.0", "Relevant coursework: Statistics"]) == (
        "GPA: 3.9/4.0", ["Relevant coursework: Statistics"])
    long = ["GPA: 3.9/4.0, top 5% of the cohort, Dean's list for four semesters"]
    assert split_gpa(long) == ("", long)


def test_has_placeholders_finds_brackets():
    cv = make_cv(contact_line=["github.com/[username]"],
                 experience=[entry("Analyst", ["Built [N] reports", "Cut costs by 10%"])])
    assert has_placeholders(cv) == ["github.com/[username]", "Built [N] reports"]


def test_changed_bullets_flags_only_rewritten_lines():
    text = "Jane Doe\nEXPERIENCE\nAnalyst, Acme\n• Built 12 weekly sales reports in Excel\n• Interviewed 30 clients"
    same = make_cv(experience=[entry("Analyst", ["Built 12 weekly sales reports in Excel", "Interviewed 30 clients"],
                                     organization="Acme")])
    assert changed_bullets(text, same) == []
    edited = make_cv(experience=[entry("Analyst", ["Built 12 weekly sales reports in Excel",
                                                   "Led a team of 8 consultants"], organization="Acme")])
    assert changed_bullets(text, edited) == ["Led a team of 8 consultants"]


def test_changed_bullets_checks_education_details():
    cv = make_cv(education=[CVEducation(institution="KSE", degree="BA", location="", dates="",
                                        details=["Dean's list 2024"])])
    assert changed_bullets("J\nKSE\nGPA 3.8", cv) == ["Dean's list 2024"]


def test_structure_cv_uses_fast_model_and_cv_text():
    client = FakeClient()
    cv = structure_cv(client, "Jane Doe\nAnalyst")
    assert isinstance(cv, BuiltCV)
    call = client.calls[0]
    assert call["output_config"]["effort"] == "low"
    assert "<cv>\nJane Doe\nAnalyst\n</cv>" in call["messages"][0]["content"][0]["text"]


def test_log_edit_feedback_rpc_payload():
    http = FakeHTTP(body=2)
    db = SupabaseDB("https://p.supabase.co", "sb_publishable_x", "tok", session=http)
    items = [{"source": "analysis", "section": "Education", "priority": "high",
              "before": "x", "after": "", "accepted": True}]
    assert db.log_edit_feedback("a@b.c", "an1", "BA", "econ", items) == 2
    url, body, _ = http.calls[0]
    assert url.endswith("/rpc/cvmax_log_edit_feedback")
    assert body == {"p_token": "tok", "p_user_key": "a@b.c", "p_analysis_id": "an1",
                    "p_target_role": "BA", "p_program": "econ", "p_items": items}
    assert MemoryDB().log_edit_feedback("a@b.c", "an1", "BA", "econ", items) == 0
