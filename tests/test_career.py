from cvmax.career import CareerPrefs, career_system, match_careers
from cvmax.cv_input import CVFile
from cvmax.demo import DEMO_CV_TEXT, FakeClient
from cvmax.profile import COMPANY_TYPES
from cvmax.schemas import CareerMatch, Direction


def prefs(**over):
    base = dict(program="business_economics", status="2 курс", interests="фінанси", avoid="продажі",
                level="Стажування", region="Європа", feedback_language="Ukrainian")
    base.update(over)
    return CareerPrefs(**base)


def test_prompt_lists_company_types_and_preferences():
    system = career_system(prefs())
    assert all(c in system for c in COMPANY_TYPES)
    assert "Business Economics" in system
    assert "Does not want: продажі" in prefs().to_prompt()


def test_match_sorts_clamps_and_fixes_company_type():
    class Client(FakeClient):
        pass

    client = Client()
    bad = CareerMatch(
        candidate_summary="s", strongest_assets=[], general_advice="a",
        directions=[
            Direction(role="A", company_type="Made up", fit_score=30, why_fits=[], gaps=[], first_steps=[], search_keywords=[]),
            Direction(role="B", company_type=COMPANY_TYPES[3], fit_score=150, why_fits=[], gaps=[], first_steps=[], search_keywords=[]),
        ],
    )
    client.messages.parse = lambda **kw: type("R", (), {"stop_reason": "end_turn", "parsed_output": bad})()
    result = match_careers(client, prefs(), CVFile("cv.txt", DEMO_CV_TEXT))
    assert [d.role for d in result.directions] == ["B", "A"]
    assert result.directions[0].fit_score == 100
    assert result.directions[1].company_type == COMPANY_TYPES[0]


def test_demo_career_is_valid():
    result = match_careers(FakeClient(), prefs(), CVFile("cv.txt", DEMO_CV_TEXT))
    assert len(result.directions) == 3
    assert all(d.company_type in COMPANY_TYPES for d in result.directions)
