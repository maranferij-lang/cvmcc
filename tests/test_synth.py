"""Synthetic evals: the flaw catalogue, the cases in evals/synth, the generator (without a model) and the new run_evals checks."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from cvmax import checks
from cvmax.llm import LLMError
from cvmax.profile import COMPANY_TYPES, LEVELS, PROGRAMS, REGIONS, STATUSES
from cvmax.schemas import Analysis, Edit

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_evals = _load("run_evals_synth_test", ROOT / "evals" / "run_evals.py")
gs = _load("generate_synthetic_test", ROOT / "evals" / "generate_synthetic.py")

SYNTH_DIR = ROOT / "evals" / "synth"
FLAWS = json.loads((ROOT / "evals" / "flaws.json").read_text(encoding="utf-8"))
PLAN_CODES = {
    "weak_opener", "no_result", "topics_only", "duplicate", "generic_interests", "long_coursework",
    "personal_data", "references_line", "objective_paragraph", "self_description", "skill_no_evidence",
    "first_person", "cv_title", "not_reverse_chronological", "over_length", "missing_contact", "strong_keep",
}
VERDICTS = {"keep", "cut", "shorten", "rewrite", "move"}
CYRILLIC = re.compile(r"[а-яіїєґА-ЯІЇЄҐ]")


# ---------------- Flaw catalogue ----------------


def test_flaw_catalogue_has_every_code_of_the_plan():
    assert set(FLAWS) == PLAN_CODES


def test_flaw_catalogue_entries_are_well_formed():
    for code, flaw in FLAWS.items():
        assert CYRILLIC.search(flaw["description"]), f"{code}: description must be in Ukrainian"
        assert flaw["plant"].strip() and not CYRILLIC.search(flaw["plant"]), f"{code}: plant must be in English"
        expect = flaw["expect"]
        if expect.get("type") == "length":
            assert expect["min_cut_words"] == 100, code
        elif expect.get("type") == "mentions":
            assert expect["any_of"] == ["email", "e-mail"], code
        else:
            assert expect["verdicts"] and set(expect["verdicts"]) <= VERDICTS, code


def test_duplicate_plant_text_keeps_both_occurrences_out_of_the_controls():
    assert "Neither occurrence may be one of the clean_lines" in FLAWS["duplicate"]["plant"]


def test_flaw_catalogue_verdicts_follow_the_plan():
    assert FLAWS["weak_opener"]["expect"]["verdicts"] == ["rewrite", "cut"]
    assert FLAWS["duplicate"]["expect"]["verdicts"] == ["cut", "shorten", "rewrite"]
    assert FLAWS["personal_data"]["expect"]["verdicts"] == ["cut"]
    assert FLAWS["not_reverse_chronological"]["expect"]["verdicts"] == ["move"]
    assert FLAWS["strong_keep"]["expect"]["verdicts"] == ["keep"]


# ---------------- Cases in evals/synth ----------------


def synth_cases() -> list[Path]:
    return sorted(SYNTH_DIR.glob("*.json")) if SYNTH_DIR.exists() else []


def load_synth(path: Path) -> tuple[dict, str]:
    """(the case JSON, the CV text). A clear message if the file is broken or the TXT is missing."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        pytest.fail(f"{path.name}: not valid JSON ({exc})")
    assert isinstance(data, dict), f"{path.name}: top level must be an object"
    assert isinstance(data.get("cv_file"), str), f"{path.name}: cv_file is missing"
    txt = path.parent / data["cv_file"]
    assert txt.exists(), f"{path.name}: cv_file {data['cv_file']} does not exist"
    return data, txt.read_text(encoding="utf-8")


def test_synth_cases_are_well_formed():
    """An empty folder passes, a broken file fails the test."""
    for path in synth_cases():
        data, text = load_synth(path)
        assert data.get("synthetic") is True, f"{path.name}: synthetic must be true"
        assert isinstance(data.get("profile"), dict), f"{path.name}: profile is missing"
        expect = data.get("expect")
        assert isinstance(expect, list) and expect, f"{path.name}: expect is empty"
        ids = [e.get("id") for e in expect]
        assert len(set(ids)) == len(ids) and all(ids), f"{path.name}: expect ids must be unique and not empty: {ids}"
        lower = text.lower()
        for item in expect:
            flaw = item.get("flaw")
            assert flaw in FLAWS, f"{path.name}: {item.get('id')} has unknown flaw code {flaw!r}"
            kind = FLAWS[flaw]["expect"].get("type")
            assert item.get("type") == kind, f"{path.name}: {item['id']} has type {item.get('type')!r}, expected {kind!r}"
            if kind == "length":
                assert isinstance(item.get("min_cut_words"), int), f"{path.name}: {item['id']} needs min_cut_words"
            elif kind == "mentions":
                assert item.get("any_of"), f"{path.name}: {item['id']} needs any_of"
            else:
                assert set(item.get("verdicts", [])) <= VERDICTS and item.get("verdicts"), \
                    f"{path.name}: {item['id']} has bad verdicts {item.get('verdicts')}"
                assert set(item["verdicts"]) == set(FLAWS[flaw]["expect"]["verdicts"]), \
                    f"{path.name}: {item['id']} verdicts differ from flaws.json"
                phrases = item.get("line_contains")
                assert isinstance(phrases, list) and phrases, f"{path.name}: {item['id']} needs line_contains"
                for phrase in phrases:
                    count = lower.count(phrase.lower())
                    assert count == 1, (
                        f"{path.name}: phrase {phrase!r} occurs {count} times in {data['cv_file']}, expected exactly 1")


def test_synth_cases_pass_the_generator_validation():
    for path in synth_cases():
        data, text = load_synth(path)
        errors = gs.validate_case(gs.saved_to_case(data, text))
        assert not errors, f"{path.name}: {errors}"


def test_synth_cv_contacts_are_example_only():
    """A fictional candidate must not point to a real profile: the LinkedIn slug ends with -example, the email domain contains example."""
    for path in synth_cases():
        _, text = load_synth(path)
        for slug in re.findall(r"linkedin\.com/in/([\w%-]+)", text, flags=re.IGNORECASE):
            assert slug.lower().endswith("-example"), f"{path.name}: LinkedIn slug {slug!r} must end with -example"
        for domain in re.findall(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", text):
            assert "example" in domain.lower(), f"{path.name}: email domain {domain!r} must contain 'example'"


def test_synth_profiles_are_loadable():
    from cvmax.profile import Profile

    for path in synth_cases():
        data, _ = load_synth(path)
        Profile(**data["profile"])


# ---------------- validate_case ----------------


def filler(count: int, start: int = 0) -> list[str]:
    """Unique bullets of 15 words each: to reach the needed word count without repeating lines."""
    return [f"- Delivered module {i} of the course project with {i % 5 + 2} teammates and {i * 3 + 10} automated tests"
            for i in range(start, start + count)]


GOOD_PROFILE = {
    "program": "law", "status": "Graduate", "background": "LLB in Law, Fictional University, 2025.",
    "target_role": "Junior Paralegal", "company_type": "Law firm", "company_details": "", "level": "Junior / first job",
    "region": "UK", "vacancy_text": "", "feedback_language": "English",
}
SELF = "Hard-working team player with strong communication skills"
WEAK = "Responsible for weekly status reports for the whole department"
CLEAN_A = "Reduced report preparation time from 6 hours to 2 hours"
CLEAN_B = "Led a team of 5 students to first place among 30 teams"


def make_text(bullets: int = 25, email: bool = True) -> str:
    contact = "Lviv, Ukraine | +380 50 000 00 00 | " + ("olena.test@example.test | " if email else "") \
        + "linkedin.com/in/olena-test-example"
    lines = ["Olena Test", contact, "", "SUMMARY", SELF, "", "EXPERIENCE", "Data Intern, Fictional Labs, Jun 2025 - Aug 2025",
             f"- {WEAK}", f"- {CLEAN_A} by building a template for 12 teams", f"- {CLEAN_B} in a case competition",
             "", "EDUCATION", *filler(bullets)]
    return "\n".join(lines) + "\n"


def make_case(**over) -> dict:
    case = {
        "name": "Вигаданий кандидат: self_description, weak_opener",
        "profile": dict(GOOD_PROFILE),
        "cv_text": make_text(),
        "planted": [{"code": "self_description", "line_contains": SELF},
                    {"code": "weak_opener", "line_contains": "Responsible for weekly status reports"}],
        "clean_lines": [CLEAN_A, CLEAN_B],
    }
    case.update(over)
    return case


def test_good_case_has_no_errors():
    case = make_case()
    assert 300 <= len(case["cv_text"].split()) <= 1200
    assert gs.validate_case(case) == []
    assert gs.validate_case(case, required=["self_description", "weak_opener"]) == []


def errors_text(case: dict, **kw) -> str:
    return " | ".join(gs.validate_case(case, **kw)).lower()


def test_validate_catches_phrase_that_occurs_twice():
    case = make_case(cv_text=make_text() + f"- {WEAK} again\n")
    msg = errors_text(case)
    assert "responsible for weekly status reports" in msg and "more than 1" in msg


def test_validate_phrase_count_ignores_case():
    case = make_case(cv_text=make_text() + SELF.upper() + "\n")
    assert "more than 1" in errors_text(case)


def test_validate_catches_phrase_that_is_absent():
    case = make_case(planted=[{"code": "weak_opener", "line_contains": "Responsible for something never written"}])
    assert "0 times" in errors_text(case)


def test_validate_catches_phrase_that_is_too_long():
    long_line = "- " + " ".join(f"word{i}" for i in range(30))  # over 120 characters
    assert len(long_line) > 120
    case = make_case(cv_text=make_text() + long_line + "\n",
                     planted=[{"code": "weak_opener", "line_contains": long_line[2:]}])
    assert "expected 15-120" in errors_text(case)


def test_validate_catches_phrase_that_is_too_short():
    case = make_case(planted=[{"code": "weak_opener", "line_contains": "Responsible"}])
    assert "expected 15-120" in errors_text(case)


def test_validate_catches_banned_company():
    for company in ("Google", "Deloitte", "EPAM", "Grammarly"):
        case = make_case(cv_text=make_text() + f"- Interned at {company} for 3 months\n")
        assert company.lower() in errors_text(case), company


def test_banned_company_ignores_meta_analysis_and_longer_words():
    assert gs.find_banned("Wrote a meta-analysis of 40 studies; the Amazonian forest; Keys") == []
    assert gs.find_banned("Intern at Meta Platforms and EY") == ["ey", "meta"]
    case = make_case(cv_text=make_text() + "- Co-authored a meta-analysis of 40 studies on memory\n")
    assert gs.validate_case(case) == []


@pytest.mark.parametrize("contact, phrase", [
    ("linkedin.com/in/olena-test", "linkedin slug"),
    ("LinkedIn.com/in/Olena-Test-Media", "linkedin slug"),
    ("olena.test@gmail.com", "email domain"),
    ("Olena.Test@mail.ua", "email domain"),
])
def test_validate_rejects_real_looking_contacts(contact, phrase):
    case = make_case(cv_text=make_text() + f"Contact: {contact}\n")
    assert phrase in errors_text(case)


def test_validate_accepts_example_contacts_and_plain_hosts():
    ok = ("Contact: olena@mail.example.com | linkedin.com/in/olena-test-EXAMPLE | github.com/olena-test-example "
          "| https://linkedin.com/company/fictional-labs")
    assert gs.validate_case(make_case(cv_text=make_text() + ok + "\n")) == []
    # The hosts linkedin.com and github.com themselves, like a lone "@" without an address, are not an error.
    assert gs.find_bad_contacts("see linkedin.com and github.com; tweet @olena or a @ b") == []


def test_find_bad_contacts_reports_each_contact_once_and_stays_fast():
    text = "a@gmail.com b@gmail.com linkedin.com/in/x linkedin.com/in/x"
    assert gs.find_bad_contacts(text) == ["LinkedIn slug 'x' must end with -example",
                                          "email domain 'gmail.com' must contain 'example'"]
    import time

    started = time.perf_counter()
    gs.find_bad_contacts("a" * 39_000 + " " + "a." * 500 + "@" * 400)  # a word without "@" and "@" without an address
    assert time.perf_counter() - started < 1


def test_system_prompt_and_task_ask_for_example_contacts():
    prompt = gs.build_prompt("law", gs.plan_profile(1, "law", 1), ["weak_opener"], "Test Person")
    assert "-example" in gs.SYSTEM and '"example"' in gs.SYSTEM
    assert "-example" in prompt and "'example'" in prompt


def test_banned_company_in_profile_is_caught():
    case = make_case(profile=dict(GOOD_PROFILE, background="Worked at KPMG in 2024."))
    assert "kpmg" in errors_text(case)


def test_validate_catches_word_count_outside_range():
    short = make_case(cv_text="\n".join(["Olena Test", SELF, f"- {WEAK}", f"- {CLEAN_A}", f"- {CLEAN_B}"]))
    assert "words, expected 300-1200" in errors_text(short)
    huge = make_case(cv_text=make_text(bullets=100))  # about 1500 words
    assert len(huge["cv_text"].split()) > 1200
    assert "words, expected 300-1200" in errors_text(huge)


def test_validate_over_length_needs_800_words_and_otherwise_forbids_them():
    case = make_case(planted=[{"code": "over_length", "line_contains": ""}])
    assert "over_length is planted" in errors_text(case)
    long_case = make_case(cv_text=make_text(bullets=60))
    assert len(long_case["cv_text"].split()) >= 800
    assert "over_length is not planted" in errors_text(long_case)
    ok = make_case(cv_text=make_text(bullets=60),
                   planted=[{"code": "over_length", "line_contains": ""}, {"code": "weak_opener", "line_contains": WEAK}])
    assert gs.validate_case(ok) == []


def padded_text(words: int) -> str:
    """make_text padded with a filler line to the exact word count (a line starting with a capital letter, not a bullet continuation)."""
    text = make_text(bullets=30)
    pad = words - len(text.split())
    assert pad > 0
    return text + " ".join(["Note"] * pad) + "\n"


def test_validate_unplanted_cv_must_fit_the_one_page_limit_of_the_app():
    """Without over_length the limit is 650 words, like checks.JUNIOR_MAX_WORDS: the app advises shortening the CV from 651 words."""
    assert gs.JUNIOR_MAX_WORDS == checks.JUNIOR_MAX_WORDS == 650
    ok = make_case(cv_text=padded_text(650))
    assert len(ok["cv_text"].split()) == 650 and gs.validate_case(ok) == []
    for text in (padded_text(651), make_text(bullets=40), make_text(bullets=44)):  # 651, 678 and 738 words
        words = len(text.split())
        assert gs.JUNIOR_MAX_WORDS < words < gs.OVER_LENGTH_WORDS
        msg = errors_text(make_case(cv_text=text))
        assert "over_length is not planted" in msg and "at or under 650" in msg, words


def test_validate_planted_over_length_still_needs_800_words():
    planted = [{"code": "over_length", "line_contains": ""}, {"code": "weak_opener", "line_contains": WEAK}]
    mid = make_case(cv_text=make_text(bullets=44), planted=planted)  # 738 words: too few for a planted flaw
    assert len(mid["cv_text"].split()) < gs.OVER_LENGTH_WORDS
    assert "over_length is planted" in errors_text(mid)


def test_prompt_asks_for_fewer_words_than_the_one_page_limit():
    profile = gs.plan_profile(1, "law", 1)
    plain = gs.build_prompt("law", profile, ["weak_opener"], "Test Person")
    assert f"400-{gs.UNPLANTED_WORDS_HINT} words" in plain and gs.UNPLANTED_WORDS_HINT < gs.JUNIOR_MAX_WORDS
    assert "850-1100 words" in gs.build_prompt("law", profile, ["over_length"], "Test Person")


def test_validate_missing_contact_forbids_an_email():
    planted = [{"code": "missing_contact", "line_contains": ""}, {"code": "weak_opener", "line_contains": WEAK}]
    assert "contains an email" in errors_text(make_case(planted=planted))
    assert gs.validate_case(make_case(planted=planted, cv_text=make_text(email=False))) == []


def test_validate_catches_unknown_code_and_strong_keep_in_planted():
    msg = errors_text(make_case(planted=[{"code": "made_up", "line_contains": WEAK}]))
    assert "unknown flaw code" in msg
    assert "belongs in clean_lines" in errors_text(make_case(planted=[{"code": "strong_keep", "line_contains": CLEAN_A}]))


def test_validate_required_codes_must_match():
    case = make_case()
    assert "not planted: duplicate" in errors_text(case, required=["self_description", "weak_opener", "duplicate"])
    assert "not requested: weak_opener" in errors_text(case, required=["self_description"])


def test_validate_clean_lines():
    assert "expected 2" in errors_text(make_case(clean_lines=[CLEAN_A]))
    assert "has no number" in errors_text(make_case(clean_lines=[CLEAN_A, "Organised the annual law society debate"]))


LONG_CLEAN = "Built a reporting pipeline for 12 regional teams " + " ".join(["metric"] * 40)
FLAGGED_CLEAN = (  # (checks finding code, CV line, control fragment)
    ("weak_opener", "Supported the regional sales team of 9 people by building a reporting template in 2025",
     "Supported the regional sales team of 9 people"),
    ("first_person", "I led a team of 5 students to first place among 30 teams in 2025",
     "I led a team of 5 students to first place"),
    ("long_bullet", LONG_CLEAN, "Built a reporting pipeline for 12 regional teams"),
)


def test_validate_clean_line_must_not_be_flagged_by_the_automatic_checks():
    """A strong_keep control that checks flags as weak, long or first-person would fail the evals by itself."""
    for code, line, fragment in FLAGGED_CLEAN:
        case = make_case(cv_text=make_text() + f"- {line}\n", clean_lines=[CLEAN_A, fragment])
        report = checks.run_checks(checks.CVFile(filename="cv.txt", text=case["cv_text"]), checks.Profile(**GOOD_PROFILE))
        assert code in {f.code for f in report.findings}, code  # the finding is really there
        assert f"({code})" in errors_text(case), code
    # a duplicated line: checks flags the second occurrence, and a control on it is rejected
    twice = "Won the cup with 12 teammates among 40 teams in 2024"
    case = make_case(cv_text=make_text() + f"- {twice}\n- {twice}.\n", clean_lines=[CLEAN_A, twice + "."])
    assert "(duplicate_line)" in errors_text(case)


def test_validate_clean_line_flagged_check_does_not_touch_planted_flaws_or_clean_controls():
    line = "Supported the regional sales team of 9 people by building a reporting template in 2025"
    planted = [{"code": "weak_opener", "line_contains": line[:45]}]  # checks flags this line, but it is a planted flaw
    case = make_case(cv_text=make_text() + f"- {line}\n", planted=planted)
    assert gs.validate_case(case) == []
    assert gs.flagged_lines(make_text(), {}) == []  # without a profile we do not run checks


def test_validate_clean_line_must_not_be_the_first_occurrence_of_the_duplicate():
    first = "Won the regional cup with 12 teammates among 40 teams"
    second = "Regional cup winner among 40 teams with 12 teammates in 2024"
    planted = [{"code": "duplicate", "line_contains": second}]
    text = make_text() + f"- {first}\n- {second}\n"
    msg = errors_text(make_case(cv_text=text, planted=planted, clean_lines=[CLEAN_A, first]))
    assert "with the duplicate" in msg and "figures 12, 40" in msg
    assert gs.validate_case(make_case(cv_text=text, planted=planted)) == []  # the controls are far from the duplicate
    one = "Reached 40 finalists in a national hackathon with 9 mentors"  # only one shared number
    case = make_case(cv_text=text + f"- {one}\n", planted=planted, clean_lines=[CLEAN_A, one])
    assert gs.validate_case(case) == []


def test_number_groups_ignore_trailing_punctuation():
    assert gs._number_groups("Raised 2,500 users, 3.5 points, then 12. In 2024,") == {"2,500", "3.5", "12", "2024"}
    assert gs._number_groups("no figures here") == set()


def test_system_prompt_keeps_controls_out_of_the_duplicate_and_the_flagged_openers():
    assert "planted duplicate" in gs.SYSTEM and '"Supported"' in gs.SYSTEM and "under 40 words" in gs.SYSTEM


def test_validate_fragments_must_not_share_a_line():
    msg = errors_text(make_case(clean_lines=[CLEAN_A, "Responsible for weekly status reports for"]))
    assert "same line" in msg


def test_validate_profile_values_come_from_the_lists():
    assert "profile.status" in errors_text(make_case(profile=dict(GOOD_PROFILE, status="5th year")))
    assert "profile.program" in errors_text(make_case(profile=dict(GOOD_PROFILE, program="astrology")))
    assert "profile.target_role is empty" in errors_text(make_case(profile=dict(GOOD_PROFILE, target_role=" ")))
    assert "profile is missing" in errors_text(make_case(profile=None))


def test_validate_semantic_checks_for_title_and_references():
    text = make_text() + "References available upon request\n"
    ok = make_case(cv_text=text, planted=[{"code": "references_line", "line_contains": "References available upon request"}])
    assert gs.validate_case(ok) == []
    bad = make_case(cv_text=text, planted=[{"code": "references_line", "line_contains": "Hard-working team player with"}])
    assert "must contain the word 'references'" in errors_text(bad)
    title = make_case(planted=[{"code": "cv_title", "line_contains": "Experience Data Intern"}])
    assert "first line" in errors_text(title)


def test_validate_does_not_crash_on_garbage():
    for case in (None, {}, {"cv_text": 5}, make_case(planted="x"), make_case(clean_lines=[1]), make_case(cv_text="x" * 50_000)):
        assert isinstance(gs.validate_case(case), list) and gs.validate_case(case)


def test_count_occurrences_is_overlap_aware_and_bounded():
    assert gs.count_occurrences("aaaa", "aa") == 2
    assert gs.count_occurrences("abc", "x") == 0 and gs.count_occurrences("abc", "") == 0
    assert gs.count_occurrences("ab " * 1000, "ab", limit=2) == 2


# ---------------- Plan of flaws and profile ----------------


def test_plan_flaws_is_deterministic_and_in_range():
    assert gs.plan_flaws(1, "law", 1, 3) == gs.plan_flaws(1, "law", 1, 3)
    for seed in (0, 1, 7, 42):
        for program in PROGRAMS:
            mixes = [gs.plan_flaws(seed, program, i, 3) for i in (1, 2, 3)]
            assert sum("over_length" in mix for mix in mixes) == 1, (seed, program)
            for mix in mixes:
                base = [c for c in mix if c != "over_length"]
                assert gs.MIN_FLAWS <= len(base) <= gs.MAX_FLAWS and len(set(mix)) == len(mix)
                assert set(mix) <= set(FLAWS) - {"strong_keep"}


def test_plan_flaws_differs_between_seeds():
    mixes = {tuple(gs.plan_flaws(seed, "law", 1, 2)) for seed in range(1, 8)}
    assert len(mixes) > 1


def test_plan_profile_uses_values_from_the_lists():
    for seed in range(1, 6):
        for program in PROGRAMS:
            p = gs.plan_profile(seed, program, 1)
            assert p["program"] == program and p["status"] in STATUSES and p["level"] in LEVELS
            assert p["region"] in REGIONS and p["company_type"] in COMPANY_TYPES
            assert p["feedback_language"] in ("English", "Ukrainian")
            assert gs.plan_profile(seed, program, 1) == p


# ---------------- Case files ----------------


def test_case_to_files_and_back_round_trip():
    case = make_case()
    data, text = gs.case_to_files(case, "law_s1_1")
    assert data["cv_file"] == "law_s1_1.txt" and data["synthetic"] is True
    assert [e["id"] for e in data["expect"]] == ["self_description-1", "weak_opener-1", "strong_keep-1", "strong_keep-2"]
    for item in data["expect"]:
        assert item["flaw"] in FLAWS and item["description"] == FLAWS[item["flaw"]]["description"]
    keep = data["expect"][2]
    assert keep["verdicts"] == ["keep"] and keep["line_contains"] == [CLEAN_A]
    back = gs.saved_to_case(data, text)
    assert back["planted"] == case["planted"] and back["clean_lines"] == case["clean_lines"]
    assert gs.validate_case(back) == []


def test_global_flaws_get_type_and_no_line_contains():
    case = make_case(cv_text=make_text(bullets=60, email=False),
                     planted=[{"code": "over_length", "line_contains": ""}, {"code": "missing_contact", "line_contains": ""}])
    data, _ = gs.case_to_files(case, "x_s1_1")
    length, mentions = data["expect"][0], data["expect"][1]
    assert length["type"] == "length" and length["min_cut_words"] == 100 and "line_contains" not in length
    assert mentions["type"] == "mentions" and mentions["any_of"] == ["email", "e-mail"] and "line_contains" not in mentions
    assert FLAWS["over_length"]["expect"] == {"type": "length", "min_cut_words": 100}  # the template is unchanged


def test_write_case_does_not_overwrite_without_force(tmp_path):
    case = make_case()
    assert gs.write_case(case, "law_s1_1", tmp_path) is True
    first = (tmp_path / "law_s1_1.json").read_text(encoding="utf-8")
    assert gs.write_case(make_case(name="Інший"), "law_s1_1", tmp_path) is False
    assert (tmp_path / "law_s1_1.json").read_text(encoding="utf-8") == first
    assert gs.write_case(make_case(name="Інший"), "law_s1_1", tmp_path, force=True) is True
    assert "Інший" in (tmp_path / "law_s1_1.json").read_text(encoding="utf-8")


# ---------------- Generation with a substituted model ----------------


class StubLLM:
    """A model stand-in: ask_structured calls .ask; the reply is built by make(prompt, call number)."""

    def __init__(self, make):
        self.make = make
        self.calls: list[dict] = []

    def ask(self, *, system, content, output_model, effort):
        self.calls.append({"system": system, "text": content[0]["text"], "model": output_model, "effort": effort})
        return self.make(self.calls[-1]["text"], len(self.calls))


def requested_codes(prompt: str) -> list[str]:
    block = prompt.split("Flaws to plant", 1)[1].split("Also give", 1)[0]
    return re.findall(r"^- (\w+): ", block, re.M)


def synthetic_for(prompt: str, extra: str = ""):
    """A fit "model" reply for the flaws named in the request."""
    codes = requested_codes(prompt)
    lines = ["Test Person", "Lviv | +380 50 000 00 00 | " + ("" if "missing_contact" in codes else "test.person@example.test | ")
             + "linkedin.com/in/test-person-example", "", "EXPERIENCE"]
    planted = []
    for code in codes:
        if code in ("over_length", "missing_contact"):
            planted.append(gs.PlantedFlaw(code=code, line_contains=""))
            continue
        if code == "cv_title":
            lines.insert(0, "Curriculum Vitae")
            phrase = "Curriculum Vitae"
        elif code == "references_line":
            phrase = "References available upon request"
            lines.append(phrase)
        else:
            phrase = f"Planted marker for {code} item"
            lines.append(f"- {phrase} with details")
        planted.append(gs.PlantedFlaw(code=code, line_contains=phrase))
    lines += [f"- {CLEAN_A} by building a template for 12 teams", f"- {CLEAN_B} in a case competition"]
    lines += filler(60 if "over_length" in codes else 25)
    if extra:
        lines.append(extra)
    return gs.SyntheticCase(
        name="Вигаданий кандидат", profile=gs.SyntheticProfile(**dict(GOOD_PROFILE, status="not a real status")),
        cv_text="\n".join(lines), planted=planted, clean_lines=[CLEAN_A, CLEAN_B])


def test_generate_case_accepts_a_valid_answer_on_the_first_try():
    llm = StubLLM(lambda prompt, n: synthetic_for(prompt))
    case, errors = gs.generate_case(llm, "law", 3, 1, 2)
    assert errors == [] and case is not None and len(llm.calls) == 1
    call = llm.calls[0]
    assert call["effort"] == "high" and call["model"] is gs.SyntheticCase
    codes = gs.plan_flaws(3, "law", 1, 2)
    assert requested_codes(call["text"]) == codes
    for code in codes:
        assert FLAWS[code]["plant"] in call["text"]
    assert gs.validate_case(case, required=codes) == []
    planned = gs.plan_profile(3, "law", 1)
    assert all(case["profile"][k] == v for k, v in planned.items())  # the values from the lists are set by the plan


def test_generate_case_regenerates_after_a_rejected_answer():
    llm = StubLLM(lambda prompt, n: synthetic_for(prompt, extra="- Interned at Google" if n == 1 else ""))
    case, errors = gs.generate_case(llm, "law", 3, 1, 2)
    assert case is not None and errors == [] and len(llm.calls) == 2
    assert "previous attempt was rejected" not in llm.calls[0]["text"]
    assert "previous attempt was rejected" in llm.calls[1]["text"] and "google" in llm.calls[1]["text"].lower()


def test_generate_case_gives_up_after_the_regeneration_limit():
    llm = StubLLM(lambda prompt, n: synthetic_for(prompt, extra="- Interned at Google"))
    case, errors = gs.generate_case(llm, "law", 3, 1, 2)
    assert case is None and errors and len(llm.calls) == 1 + gs.MAX_REGENERATIONS


def test_generate_case_does_not_retry_a_model_error():
    def boom(prompt, n):
        raise LLMError("quota")

    llm = StubLLM(boom)
    case, errors = gs.generate_case(llm, "law", 3, 1, 2)
    assert case is None and errors == ["model error: quota"] and len(llm.calls) == 1


def test_main_writes_cases_and_skips_existing(tmp_path, monkeypatch, capsys):
    llm = StubLLM(lambda prompt, n: synthetic_for(prompt))
    monkeypatch.setattr(gs, "make_llm", lambda: llm)
    args = ["--program", "law", "--n", "2", "--seed", "5", "--out", str(tmp_path)]
    assert gs.main(args) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["law_s5_1.json", "law_s5_1.txt", "law_s5_2.json", "law_s5_2.txt"]
    for path in tmp_path.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        text = (tmp_path / data["cv_file"]).read_text(encoding="utf-8")
        assert data["synthetic"] is True and all("flaw" in e for e in data["expect"])
        assert gs.validate_case(gs.saved_to_case(data, text)) == []
    over = [p for p in tmp_path.glob("*.json") if '"over_length"' in p.read_text(encoding="utf-8")]
    assert len(over) == 1  # one over_length per program
    calls = len(llm.calls)
    capsys.readouterr()
    assert gs.main(args) == 0
    assert len(llm.calls) == calls and "exists, skipped" in capsys.readouterr().out
    assert gs.main(args + ["--force"]) == 0 and len(llm.calls) == 2 * calls


def test_main_reports_failures_and_bad_arguments(tmp_path, monkeypatch, capsys):
    assert gs.main(["--program", "astrology", "--out", str(tmp_path)]) == 1
    monkeypatch.setattr(gs, "make_llm", lambda: None)
    assert gs.main(["--program", "law", "--out", str(tmp_path)]) == 1
    assert "API_KEY" in capsys.readouterr().out
    bad = StubLLM(lambda prompt, n: synthetic_for(prompt, extra="- Interned at Google"))
    monkeypatch.setattr(gs, "make_llm", lambda: bad)
    assert gs.main(["--program", "law", "--n", "1", "--seed", "9", "--out", str(tmp_path)]) == 1
    assert not list(tmp_path.glob("*.json")) and "skipped, no valid case" in capsys.readouterr().out


# ---------------- run_evals: the length and mentions types ----------------


def make_analysis(edits=(), summary="", missing_info=()) -> Analysis:
    return Analysis(
        overall_score=50, summary=summary, target_assumptions=[], scores=[], strengths=[], line_review=[],
        edits=[Edit(section=s, before=b, after=a, reason="", priority="high") for s, b, a in edits],
        gaps=[], missing_info=list(missing_info),
    )


def words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


LENGTH = {"id": "over_length-1", "flaw": "over_length", "type": "length", "min_cut_words": 100}
MENTIONS = {"id": "missing_contact-1", "flaw": "missing_contact", "type": "mentions", "any_of": ["email", "e-mail"]}


def test_length_check_sums_the_cut_words():
    ok, detail = run_evals.check(LENGTH, make_analysis(edits=[("X", words(150), words(30))]))
    assert ok and "120" in detail
    two = [("X", words(80), words(20)), ("Y", words(70), words(25))]  # 60 + 45 = 105
    assert run_evals.check(LENGTH, make_analysis(edits=two))[0]
    assert not run_evals.check(LENGTH, make_analysis(edits=[("X", words(120), words(30))]))[0]  # 90 words


def test_length_check_ignores_edits_that_make_text_longer():
    edits = [("X", words(105), words(5)), ("Y", words(10), words(300)), ("Z", "", words(50))]
    ok, _ = run_evals.check(LENGTH, make_analysis(edits=edits))
    assert ok  # 100 words cut, a lengthening is not subtracted
    assert not run_evals.check(LENGTH, make_analysis(edits=[("X", words(99), words(0))]))[0]
    assert not run_evals.check(LENGTH, make_analysis())[0]


def test_length_check_uses_min_cut_words_from_the_case():
    edits = [("X", words(60), words(20))]
    assert run_evals.check(dict(LENGTH, min_cut_words=40), make_analysis(edits=edits))[0]
    assert not run_evals.check(dict(LENGTH, min_cut_words=41), make_analysis(edits=edits))[0]


def test_mentions_check_looks_in_all_four_places():
    assert run_evals.check(MENTIONS, make_analysis(edits=[("Contact", "", "Add your email to the header")]))[0]
    assert run_evals.check(MENTIONS, make_analysis(edits=[("Contact email", "", "Add contact details")]))[0]
    assert run_evals.check(MENTIONS, make_analysis(missing_info=["Your E-mail address"]))[0]
    assert run_evals.check(MENTIONS, make_analysis(summary="There is no email in the header."))[0]
    ok, detail = run_evals.check(MENTIONS, make_analysis(summary="Looks fine", edits=[("Skills", "a", "b")]))
    assert not ok and "email" in detail


def test_mentions_check_does_not_read_before_or_reason():
    analysis = make_analysis(edits=[("Skills", "email me", "Python")])
    assert not run_evals.check(MENTIONS, analysis)[0]
    assert not run_evals.check(dict(MENTIONS, any_of=[]), make_analysis(summary="email"))[0]


def test_unknown_check_type_fails_loudly():
    ok, detail = run_evals.check({"id": "x", "type": "vibes"}, make_analysis())
    assert not ok and "vibes" in detail


def test_old_format_without_type_still_works():
    exp = {"id": "a", "line_contains": ["Date of birth"], "verdicts": ["cut"]}
    from cvmax.schemas import LineVerdict

    analysis = make_analysis()
    analysis.line_review = [LineVerdict(line="Date of birth: 14 March 2005", verdict="cut", reason="")]
    assert run_evals.check(exp, analysis)[0]


# ---------------- run_evals: the invented-numbers check ----------------

CV = "Sales Intern, Jun 2025 - Aug 2025\n- Prepared 12 weekly reports for 3 regional managers\n"


def test_hallucination_flags_a_number_that_is_not_in_the_cv():
    analysis = make_analysis(edits=[("Experience", "Prepared 12 weekly reports", "Cut report time by 45%")])
    ok, detail = run_evals.check_no_invented_numbers(analysis, CV)
    assert not ok and "45%" in detail


def test_hallucination_passes_bracketed_placeholders_and_known_numbers():
    edits = [
        ("Experience", "Prepared 12 weekly reports", "Prepared 12 weekly reports, cutting time by [X]%"),
        ("Experience", "reports for 3 regional managers", "Built reports for [N] managers and 3 regions"),
        ("Experience", "Sales Intern", "Sales Intern, Jun 2025 - Aug 2025"),
        ("Experience", "", "Led a team of [5] interns"),
        ("Experience", "Prepared 12 weekly reports", ""),
    ]
    assert run_evals.check_no_invented_numbers(make_analysis(edits=edits), CV) == (True, "ok")


def test_hallucination_allows_numbers_from_before_and_profile_background():
    analysis = make_analysis(edits=[("Experience", "scored 98 points", "Scored 98 points in the exam")])
    assert run_evals.check_no_invented_numbers(analysis, CV)[0]  # the number is in the quote
    analysis = make_analysis(edits=[("Education", "BSc Economics", "BSc Economics, 2027")])
    assert not run_evals.check_no_invented_numbers(analysis, CV)[0]
    assert run_evals.check_no_invented_numbers(analysis, CV, "Studies Economics, graduates in 2027")[0]


def test_hallucination_reports_the_first_bad_edit():
    edits = [("E", "a", "Raised sales by 70%"), ("E", "b", "Trained 40 people")]
    ok, detail = run_evals.check_no_invented_numbers(make_analysis(edits=edits), CV)
    assert not ok and "70%" in detail


# ---------------- run_evals: summary by flaw ----------------


def test_by_flaw_aggregates_passes_over_runs_and_cases():
    results = {
        ("a", "weak_opener-1"): [True, False],
        ("a", "strong_keep-1"): [True, True],
        ("b", "weak_opener-1"): [True, True],
        ("b", "_no_invented_numbers"): [False, True],
        ("b", "no_flaw_here"): [True, True],
    }
    flaws = {
        ("a", "weak_opener-1"): "weak_opener", ("a", "strong_keep-1"): "strong_keep",
        ("b", "weak_opener-1"): "weak_opener", ("b", "_no_invented_numbers"): "hallucination",
    }
    assert run_evals.summarize_by_flaw(results, flaws) == {
        "hallucination": {"passed": 1, "total": 2},
        "strong_keep": {"passed": 2, "total": 2},
        "weak_opener": {"passed": 3, "total": 4},
    }
    result = run_evals.build_result("M", 2, results, flaws)
    assert result["by_flaw"]["weak_opener"] == {"passed": 3, "total": 4}
    assert result["total"] == 10 and result["passed"] == 8  # the overall counters stayed as they were


def test_by_flaw_is_absent_when_no_expect_has_a_flaw():
    results = {("a", "x"): [True, False]}
    assert "by_flaw" not in run_evals.build_result("M", 2, results)
    assert "by_flaw" not in run_evals.build_result("M", 2, results, {})
    assert "by_flaw" not in run_evals.build_result("M", 2, results, {("z", "y"): "weak_opener"})


def test_load_flaw_map_adds_hallucination_only_when_a_flaw_is_present(tmp_path):
    (tmp_path / "old.json").write_text(json.dumps({"expect": [{"id": "a", "line_contains": ["x"], "verdicts": ["cut"]}]}))
    assert run_evals.load_flaw_map([tmp_path / "old.json"]) == {}
    (tmp_path / "new.json").write_text(json.dumps({"expect": [{"id": "a-1", "flaw": "duplicate"}, {"id": "b"}]}))
    (tmp_path / "broken.json").write_text("{not json")
    flaws = run_evals.load_flaw_map([tmp_path / "old.json", tmp_path / "new.json", tmp_path / "broken.json"])
    assert flaws == {("new", "a-1"): "duplicate", ("new", run_evals.HALLUCINATION_ID): "hallucination",
                     ("old", run_evals.HALLUCINATION_ID): "hallucination"}


def test_print_by_flaw_lists_the_worst_first(capsys):
    run_evals.print_by_flaw({"a_good": {"passed": 2, "total": 2}, "b_bad": {"passed": 0, "total": 3},
                             "c_mid": {"passed": 1, "total": 2}})
    out = capsys.readouterr().out
    assert out.index("b_bad") < out.index("c_mid") < out.index("a_good")
    assert "0/3" in out and "100%" in out


def test_real_synth_cases_give_a_flaw_map_with_every_expect():
    cases = synth_cases()
    flaws = run_evals.load_flaw_map(cases)
    for path in cases:
        data, _ = load_synth(path)
        for item in data["expect"]:
            assert flaws[(path.stem, item["id"])] == item["flaw"]
        assert flaws[(path.stem, run_evals.HALLUCINATION_ID)] == "hallucination"
