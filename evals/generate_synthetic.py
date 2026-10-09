"""Generation of synthetic CVs with planted flaws for evals.

A heavy model writes a fictional CV in which the flaws from evals/flaws.json are deliberately planted, and
states which line fragment to use to look for each flaw. The case is saved in the same format as evals/cases
(JSON + TXT), so `python evals/run_evals.py evals/synth` works without format changes.
Extra in the JSON: "synthetic": true, and a "flaw" field in every expect item.

Run:  python evals/generate_synthetic.py --program all --n 2 --seed 7
      python evals/generate_synthetic.py --program law --n 3 --seed 1 --out evals/synth
Needs GEMINI_API_KEY or ANTHROPIC_API_KEY. Existing files are not overwritten without --force.

After generation every case goes through validate_case (it can be called without a model):
line fragments occur in the text exactly once, a fragment is 15-120 characters long,
the text has 300-1200 words, no real company from the banned list, the LinkedIn slug ends with
-example, and the email domain contains example. Without over_length the CV is no longer than
JUNIOR_MAX_WORDS words (the one-page limit in the app), and the clean_lines controls have no findings
weak_opener, first_person, long_bullet, duplicate_line from cvmax.checks and do not repeat the duplicate's figures.
An unfit case is regenerated up to MAX_REGENERATIONS times, then skipped with a message.
"""

# No `from __future__ import annotations`: the pydantic schemas below must work even if the module
# is loaded by path (importlib) without being registered in sys.modules, as tests/test_evals.py does.
import argparse
import copy
import functools
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, List, Sequence

from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cvmax.checks import JUNIOR_MAX_WORDS, run_checks  # noqa: E402
from cvmax.cv_input import CVFile  # noqa: E402
from cvmax.llm import LLMError, ask_structured, make_llm  # noqa: E402
from cvmax.profile import (  # noqa: E402
    COMPANY_TYPES, FEEDBACK_LANGUAGES, LEVELS, PROGRAMS, REGIONS, STATUSES, Profile,
)

FLAWS_PATH = ROOT / "evals" / "flaws.json"
DEFAULT_OUT = ROOT / "evals" / "synth"

MIN_WORDS, MAX_WORDS = 300, 1200  # word limits of a CV
OVER_LENGTH_WORDS = 800  # threshold of the planted over_length flaw; without it the limit is JUNIOR_MAX_WORDS (as in the app)
UNPLANTED_WORDS_HINT = 600  # how many words we ask the model for without over_length: a margin below JUNIOR_MAX_WORDS for counting error
PHRASE_MIN, PHRASE_MAX = 15, 120  # length of line_contains, in characters
MAX_CV_CHARS = 40_000  # same as the CV text limit in cv_input: longer texts never reach the regular expressions
MIN_FLAWS, MAX_FLAWS = 4, 6  # how many flaws we plant in one case (without over_length)
CLEAN_LINES = 2  # how many strong control bullets (strong_keep) a case has
# cvmax.checks findings that the analysis prompt tells the model to answer with a verdict: a strong_keep control cannot have them.
CLEAN_FORBIDDEN_FINDINGS = frozenset({"weak_opener", "first_person", "long_bullet", "duplicate_line"})
DUPLICATE_SHARED_NUMBERS = 2  # this many numbers shared with the duplicate's line make a control the duplicate's first occurrence
MAX_REGENERATIONS = 2  # how many times we regenerate an unfit case
MAX_FEEDBACK_ERRORS = 8  # how many errors of the previous attempt we show the model

# Real companies that must not appear in the fictional CVs.
BANNED_COMPANIES = (
    "Google", "Amazon", "Microsoft", "Meta", "McKinsey", "BCG", "Bain", "Deloitte", "PwC", "KPMG", "EY",
    "SoftServe", "EPAM", "GlobalLogic", "Grammarly",
)
# Short names that coincide with ordinary words (meta-analysis) are matched with exact case only.
_EXACT_CASE = {"Meta", "EY", "BCG"}

# Fictional candidate names: so that cases do not repeat the same name.
FIRST_NAMES = ("Illia", "Daria", "Taras", "Olena", "Maksym", "Sofiia", "Andrii", "Kateryna", "Oliver", "Emma",
               "Lukas", "Anna", "Nikita", "Marta", "Jonas", "Yaroslava")
LAST_NAMES = ("Moroz", "Bondar", "Kravets", "Lysenko", "Tkach", "Hrytsenko", "Savchuk", "Melnyk", "Hartley",
              "Novak", "Berger", "Kowalski", "Petrenko", "Shevchuk", "Dmytrenko", "Vasylenko")

_PROFILE_FIELDS = ("program", "status", "background", "target_role", "company_type", "company_details", "level",
                   "region", "vacancy_text", "feedback_language")


# ---------------- Schema of the model's reply ----------------


class SyntheticProfile(BaseModel):
    program: str
    status: str
    background: str
    target_role: str
    company_type: str
    company_details: str
    level: str
    region: str
    vacancy_text: str
    feedback_language: str


class PlantedFlaw(BaseModel):
    code: str  # key from evals/flaws.json
    line_contains: str  # fragment of a CV line; empty for whole-CV flaws (over_length, missing_contact)


class SyntheticCase(BaseModel):
    name: str
    profile: SyntheticProfile
    cv_text: str
    planted: List[PlantedFlaw]
    clean_lines: List[str]  # fragments of strong bullets that must not be touched


# ---------------- Flaw catalogue ----------------


@functools.lru_cache(maxsize=1)
def load_flaws() -> dict[str, dict]:
    """The flaw catalogue from evals/flaws.json (do not modify the returned dict)."""
    return json.loads(FLAWS_PATH.read_text(encoding="utf-8"))


def is_global(flaw: dict) -> bool:
    """A flaw of the whole CV (length, missing email): it has no line."""
    return flaw.get("expect", {}).get("type") in ("length", "mentions")


# ---------------- Plans: which flaws and which profile ----------------


def over_length_index(seed: int, program: str, n: int) -> int:
    """Number of the case (from 1) in which over_length is added for this program."""
    return 1 + random.Random(f"{seed}:{program}:over_length").randrange(max(n, 1))


def plan_flaws(seed: int, program: str, index: int, n: int = 1) -> list[str]:
    """Flaws for case `index` (from 1): 4-6 random ones, and one case of the program also gets over_length.

    Deterministic: the same seed gives the same set. strong_keep is not included, those are the separate clean_lines.
    """
    flaws = load_flaws()
    order = list(flaws)
    pool = [code for code in order if code not in ("over_length", "strong_keep")]
    rng = random.Random(f"{seed}:{program}:{index}")
    picked = rng.sample(pool, rng.randint(MIN_FLAWS, MAX_FLAWS))
    if index == over_length_index(seed, program, n):
        picked.append("over_length")
    return sorted(picked, key=order.index)


def plan_profile(seed: int, program: str, index: int) -> dict[str, str]:
    """Profile values from the lists in cvmax/profile.py. The free fields (background, target_role) are written by the model."""
    rng = random.Random(f"{seed}:{program}:{index}:profile")
    level = rng.choice(LEVELS[:2])  # for the Mid-level these are not student CVs
    statuses = ["1st year", "2nd year", "3rd year", "4th year", "Master's"] if level == "Internship" \
        else ["4th year", "Master's", "Graduate"]
    return {
        "program": program,
        "status": rng.choice([s for s in statuses if s in STATUSES]),
        "level": level,
        "region": rng.choice(REGIONS),
        "company_type": rng.choice(COMPANY_TYPES),
        "feedback_language": rng.choice(list(FEEDBACK_LANGUAGES.values())),
    }


def plan_name(seed: int, program: str, index: int) -> str:
    rng = random.Random(f"{seed}:{program}:{index}:name")
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


# ---------------- Validation ----------------


def count_occurrences(text: str, phrase: str, limit: int = 2) -> int:
    """How many times phrase occurs in text (with overlaps), but at most limit."""
    if not phrase:
        return 0
    found = start = 0
    while found < limit:
        pos = text.find(phrase, start)
        if pos < 0:
            break
        found += 1
        start = pos + 1
    return found


_BANNED_ANY_CASE = re.compile(
    r"(?<![\w-])(?:%s)(?![\w-])" % "|".join(re.escape(c) for c in BANNED_COMPANIES if c not in _EXACT_CASE),
    re.IGNORECASE,
)
_BANNED_EXACT = re.compile(r"(?<![\w-])(?:%s)(?![\w-])" % "|".join(sorted(_EXACT_CASE)))


def find_banned(text: str) -> list[str]:
    """Real companies from the banned list that are mentioned in the text (the text is cut to MAX_CV_CHARS)."""
    text = text[:MAX_CV_CHARS]
    found = {m.group(0).lower() for m in _BANNED_ANY_CASE.finditer(text)}
    found |= {m.group(0).lower() for m in _BANNED_EXACT.finditer(text)}
    return sorted(found)


_LINKEDIN_SLUG = re.compile(r"linkedin\.com/in/([\w%-]+)", re.IGNORECASE)
_EMAIL_DOMAIN = re.compile(r"[\w-]+(?:\.[\w-]+)+")
_ADDRESS_SPLIT = re.compile(r"[\s|,;<>()\[\]]+")


def find_bad_contacts(text: str) -> list[str]:
    """Contacts that may lead to a real person: a LinkedIn slug without -example, an email domain without example.

    Only LinkedIn slugs and email domains; linkedin.com or github.com themselves are not touched. The text is cut to MAX_CV_CHARS.
    """
    text = text[:MAX_CV_CHARS]
    errors: list[str] = []
    for slug in _LINKEDIN_SLUG.findall(text):
        message = f"LinkedIn slug {slug[:50]!r} must end with -example"
        if not slug.lower().endswith("-example") and message not in errors:
            errors.append(message)
    # Emails are searched word by word, not with a single regular expression with "[\w.+-]+@": on a long word without "@" that one is quadratic.
    for word in _ADDRESS_SPLIT.split(text):
        _, at, rest = word.partition("@")
        match = _EMAIL_DOMAIN.match(rest) if at else None
        if match and "example" not in match.group(0).lower():
            message = f"email domain {match.group(0)[:50]!r} must contain 'example'"
            if message not in errors:
                errors.append(message)
    return errors


def _norm_line(line: str) -> str:
    """A line in lower case with a single space between words: this is how we compare a CV line with the quote from checks."""
    return " ".join(line.lower().split())


def _number_groups(line: str) -> set[str]:
    """The numbers of a line (12, 2,500, 3.5, 2024) without a trailing period or comma."""
    return {m.rstrip(",.") for m in re.findall(r"\d[\d,.]*", line)}


def flagged_lines(text: str, profile: dict) -> list[tuple[str, str]]:
    """(finding code, start of the line) for the cvmax.checks findings in CLEAN_FORBIDDEN_FINDINGS.

    The start of the line is normalised with _norm_line: checks quotes up to 120 characters of a line.
    Without a usable profile we do not run checks (profile errors are already in the validate_case list).
    """
    if not all(isinstance(profile.get(f), str) for f in _PROFILE_FIELDS):
        return []
    report = run_checks(CVFile(filename="cv.txt", text=text), Profile(**{f: profile[f] for f in _PROFILE_FIELDS}))
    return [(f.code, _norm_line(f.line)) for f in report.findings if f.code in CLEAN_FORBIDDEN_FINDINGS and f.line]


def _validate_profile(profile: Any) -> list[str]:
    if not isinstance(profile, dict):
        return ["profile is missing"]
    errors = []
    missing = [f for f in _PROFILE_FIELDS if not isinstance(profile.get(f), str)]
    if missing:
        errors.append(f"profile fields missing or not text: {', '.join(missing)}")
    allowed = {
        "program": list(PROGRAMS), "status": STATUSES, "level": LEVELS, "region": REGIONS,
        "company_type": COMPANY_TYPES, "feedback_language": list(FEEDBACK_LANGUAGES.values()),
    }
    for field, values in allowed.items():
        value = profile.get(field)
        if isinstance(value, str) and value not in values:
            errors.append(f"profile.{field} {value!r} is not one of the allowed values")
    for field in ("background", "target_role"):
        value = profile.get(field)
        if isinstance(value, str) and not value.strip():
            errors.append(f"profile.{field} is empty")
    return errors


def validate_case(case: dict, required: Sequence[str] | None = None) -> list[str]:
    """The list of errors of a case (empty if the case is fit). No model needed.

    `case`: a dict shaped like SyntheticCase (name, profile, cv_text, planted, clean_lines).
    `required`: codes of the flaws that were supposed to be planted (we check that the model followed the plan).
    """
    if not isinstance(case, dict):
        return ["case is not an object"]
    flaws = load_flaws()
    errors: list[str] = []
    if not isinstance(case.get("name"), str) or not case["name"].strip():
        errors.append("name is empty")
    errors += _validate_profile(case.get("profile"))

    text = case.get("cv_text")
    if not isinstance(text, str) or not text.strip():
        return errors + ["cv_text is empty"]
    if len(text) > MAX_CV_CHARS:
        return errors + [f"cv_text is longer than {MAX_CV_CHARS} characters"]
    words = len(text.split())
    if not MIN_WORDS <= words <= MAX_WORDS:
        errors.append(f"cv_text has {words} words, expected {MIN_WORDS}-{MAX_WORDS}")

    profile = case["profile"] if isinstance(case.get("profile"), dict) else {}
    around = " ".join(str(profile.get(f, "")) for f in ("background", "target_role", "company_details", "vacancy_text"))
    banned = find_banned(text + "\n" + around)
    if banned:
        errors.append(f"real companies are not allowed: {', '.join(banned)}")
    errors += find_bad_contacts(text)

    planted, clean = case.get("planted"), case.get("clean_lines")
    if not isinstance(planted, list) or not all(isinstance(p, dict) for p in planted):
        return errors + ["planted is not a list of {code, line_contains}"]
    if not isinstance(clean, list) or not all(isinstance(c, str) for c in clean):
        return errors + ["clean_lines is not a list of text"]

    lower = text.lower()
    lines = lower.split("\n")
    first_line = next((ln for ln in lines if ln.strip()), "")
    codes: list[str] = []
    phrases: list[tuple[str, str]] = []  # (whose flaw, line fragment)
    for item in planted:
        code = str(item.get("code", "")).strip()
        phrase = str(item.get("line_contains", "")).strip()
        if code not in flaws:
            errors.append(f"unknown flaw code {code!r}")
            continue
        if code == "strong_keep":
            errors.append("strong_keep belongs in clean_lines, not in planted")
            continue
        codes.append(code)
        if is_global(flaws[code]):
            continue
        phrases.append((code, phrase))
        if code == "references_line" and "references" not in phrase.lower():
            errors.append("references_line: the fragment must contain the word 'references'")
        if code == "cv_title" and phrase and phrase.lower() not in first_line:
            errors.append("cv_title: the fragment must be part of the first line of the CV")
    for code, count in Counter(codes).items():
        if count > 1:
            errors.append(f"flaw {code} is planted {count} times, expected once")
    if len(clean) != CLEAN_LINES:
        errors.append(f"clean_lines has {len(clean)} items, expected {CLEAN_LINES}")
    for line in clean:
        phrases.append(("clean_lines", line.strip()))
        if not re.search(r"\d", line):
            errors.append(f"clean_lines: {line.strip()[:50]!r} has no number, a strong line needs one")

    flagged = flagged_lines(text, profile) if clean else []
    used_lines: dict[int, str] = {}
    clean_found: list[str] = []  # full lines of the controls
    duplicate_line = None  # full line of the duplicate (second occurrence)
    for label, phrase in phrases:
        shown = phrase[:50]
        if "\n" in phrase:
            errors.append(f"{label}: the fragment {shown!r} spans several lines")
            continue
        if not PHRASE_MIN <= len(phrase) <= PHRASE_MAX:
            errors.append(f"{label}: the fragment {shown!r} is {len(phrase)} characters, expected {PHRASE_MIN}-{PHRASE_MAX}")
        n = count_occurrences(lower, phrase.lower())
        if n != 1:
            errors.append(f"{label}: the fragment {shown!r} occurs {'0' if n == 0 else 'more than 1'} times, expected exactly 1")
            continue
        index = next(i for i, ln in enumerate(lines) if phrase.lower() in ln)
        if index in used_lines:
            errors.append(f"{label}: the fragment {shown!r} is on the same line as the fragment of {used_lines[index]}")
        used_lines[index] = label
        if label == "duplicate":
            duplicate_line = lines[index]
        elif label == "clean_lines":
            clean_found.append(lines[index])
            norm = _norm_line(lines[index])
            hit = next((code for code, start in flagged if norm.startswith(start)), None)
            if hit:
                errors.append(f"clean_lines: the line of {shown!r} is flagged by the automatic checks ({hit}), "
                              "a control must be a clean strong bullet")

    if duplicate_line is not None:
        dup_numbers = _number_groups(duplicate_line)
        for line in clean_found:
            shared = dup_numbers & _number_groups(line)
            if len(shared) >= DUPLICATE_SHARED_NUMBERS:
                errors.append(f"clean_lines: {line.strip()[:50]!r} shares the figures {', '.join(sorted(shared))} with the "
                              "duplicate, a control must not be an occurrence of the duplicate")

    if "over_length" in codes and words < OVER_LENGTH_WORDS:
        errors.append(f"over_length is planted, but cv_text has only {words} words, expected {OVER_LENGTH_WORDS}+")
    if "over_length" not in codes and words > JUNIOR_MAX_WORDS:  # the app already suggests shortening the CV from this threshold
        errors.append(f"cv_text has {words} words, but over_length is not planted: keep it at or under {JUNIOR_MAX_WORDS}")
    if "missing_contact" in codes and "@" in text:
        errors.append("missing_contact is planted, but the CV contains an email address")
    if required is not None:
        want, got = set(required), set(codes)
        if want - got:
            errors.append(f"flaws not planted: {', '.join(sorted(want - got))}")
        if got - want:
            errors.append(f"flaws planted that were not requested: {', '.join(sorted(got - want))}")
    return errors


# ---------------- Case files ----------------


def build_expect(case: dict) -> list[dict]:
    """expect items in the evals/cases format: one per planted flaw, then the strong_keep controls."""
    flaws = load_flaws()
    counts: Counter = Counter()
    expect: list[dict] = []

    def add(code: str, phrase: str) -> None:
        counts[code] += 1
        item: dict[str, Any] = {"id": f"{code}-{counts[code]}", "flaw": code, "description": flaws[code]["description"]}
        if not is_global(flaws[code]):
            item["line_contains"] = [phrase]
        item.update(copy.deepcopy(flaws[code]["expect"]))
        expect.append(item)

    for p in case["planted"]:
        add(p["code"], p.get("line_contains", ""))
    for line in case["clean_lines"]:
        add("strong_keep", line)
    return expect


def case_to_files(case: dict, stem: str) -> tuple[dict, str]:
    """(the case JSON, the TXT text) to be saved."""
    profile = {f: case["profile"][f] for f in _PROFILE_FIELDS}
    data = {
        "name": case["name"].strip(),
        "cv_file": f"{stem}.txt",
        "synthetic": True,
        "profile": profile,
        "expect": build_expect(case),
    }
    return data, case["cv_text"].strip() + "\n"


def saved_to_case(data: dict, cv_text: str) -> dict:
    """The reverse conversion: the saved JSON + TXT back into the SyntheticCase shape (for validate_case)."""
    planted, clean = [], []
    for item in data.get("expect", []):
        flaw = item.get("flaw")
        phrase = (item.get("line_contains") or [""])[0]
        if flaw == "strong_keep":
            clean.append(phrase)
        else:
            planted.append({"code": flaw, "line_contains": phrase})
    return {"name": data.get("name", ""), "profile": data.get("profile"), "cv_text": cv_text,
            "planted": planted, "clean_lines": clean}


def write_case(case: dict, stem: str, out_dir: Path, force: bool = False) -> bool:
    """Writes stem.txt and stem.json. Existing files are left alone without force (False). The JSON is written last."""
    txt_path, json_path = out_dir / f"{stem}.txt", out_dir / f"{stem}.json"
    if not force and (txt_path.exists() or json_path.exists()):
        return False
    data, text = case_to_files(case, stem)
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return True


# ---------------- Request to the model ----------------

SYSTEM = f"""You write fictional student CVs that are used to test a CV-review tool. Every CV is plain text, \
as if extracted from a PDF: the candidate's name and contact line first, then sections with upper-case \
headings (SUMMARY, EDUCATION, EXPERIENCE, PROJECTS, SKILLS, LANGUAGES and so on) and bullets that start with "- ".

Hard rules:
- Everything is invented: people, universities, employers, projects, emails, links. Every LinkedIn slug ends with \
-example (linkedin.com/in/olena-test-example) and every email domain contains "example" (olena.test@mail.example.com). \
Never mention real companies or products of them, in particular: {", ".join(BANNED_COMPANIES)}.
- Plant exactly the flaws you are asked to plant, and no other deliberate flaws, otherwise the test is unfair. \
Apart from the requested flaws the CV is a normal, decent student CV: bullets start with strong action verbs and \
most carry a number, the order is reverse chronological, there is no self-praise summary, no objective, no \
personal data such as date of birth or marital status, no "References" line, no first-person bullets, no \
"Curriculum Vitae" title, no generic interests, no long lists of courses, and every skill in SKILLS is backed by \
a bullet. Keep a phone number and a LinkedIn link in the header, and an email unless the task says otherwise.
- The numbers in the CV must be plausible and consistent (dates, grades, counts).
- Output fields. name: a short label of the case in Ukrainian, starting with "Вигаданий". profile: copy the given \
values exactly; background, target_role and company_details are short free text consistent with the CV; \
vacancy_text is an empty string. cv_text: the whole CV. planted: one item per requested flaw, with its code. \
clean_lines: the fragments of the strong bullets.
- A fragment (line_contains, clean_lines item) is copied verbatim from ONE line of cv_text, case does not matter, \
{PHRASE_MIN}-{PHRASE_MAX} characters, and it occurs exactly once in the whole cv_text. Pick a distinctive \
fragment and prefer one that starts at the beginning of the line, because the reviewing tool may quote only the \
first words of a line. Two fragments never share a line.
- clean_lines: exactly {CLEAN_LINES} strong bullets (a clear action verb, a concrete number and a result) that have \
none of the planted flaws, are never one of the two lines of a planted duplicate, never start with a duty verb \
such as "Supported", "Helped" or "Worked on", and stay under 40 words. They are the false-alarm control: a good reviewer leaves them alone.
- Flaws that apply to the whole CV (over_length, missing_contact) get an empty line_contains."""


def build_prompt(program: str, profile: dict, codes: list[str], candidate: str, errors: Sequence[str] = ()) -> str:
    """The request to the model: the profile, the flaws with instructions, the length and (on a retry) the errors of the last attempt."""
    flaws = load_flaws()
    over = "over_length" in codes
    words = "850-1100" if over else f"400-{UNPLANTED_WORDS_HINT}"
    plan = "\n".join(f"- {code}: {flaws[code]['plant']}" for code in codes)
    values = "\n".join(f"{field}: {profile[field]}" for field in
                       ("program", "status", "level", "region", "company_type", "feedback_language"))
    text = (
        f"Write one fictional CV for a student in the field \"{PROGRAMS[program]}\".\n"
        f"Candidate name: {candidate} (invent a matching email whose domain contains 'example' and a LinkedIn slug that ends with -example).\n\n"
        f"Profile values to copy exactly into the profile field:\n{values}\n\n"
        f"The CV text must have {words} words.\n\n"
        f"Flaws to plant (one planted item each):\n{plan}\n\n"
        f"Also give {CLEAN_LINES} strong control bullets in clean_lines."
    )
    if errors:
        shown = "\n".join(f"- {e[:200]}" for e in list(errors)[:MAX_FEEDBACK_ERRORS])
        text += f"\n\nYour previous attempt was rejected for these reasons, fix all of them:\n{shown}"
    return text


def normalise(case: dict, planned: dict[str, str]) -> dict:
    """Brings the model reply to a usable form: line breaks, trimmed fragments, the profile as planned."""
    flaws = load_flaws()
    case = copy.deepcopy(case)
    case["cv_text"] = str(case.get("cv_text", "")).replace("\r\n", "\n").replace("\r", "\n").strip()
    profile = dict(case.get("profile") or {})
    profile.update(planned)  # the values from the lists are set by the plan, not by the model
    case["profile"] = profile
    for item in case.get("planted", []):
        item["code"] = str(item.get("code", "")).strip()
        phrase = str(item.get("line_contains", "")).strip()
        flaw = flaws.get(item["code"])
        item["line_contains"] = "" if flaw and is_global(flaw) else phrase
    case["clean_lines"] = [str(c).strip() for c in case.get("clean_lines", [])]
    return case


def generate_case(llm: Any, program: str, seed: int, index: int, n: int = 1) -> tuple[dict | None, list[str]]:
    """One case: request to the model, validation, up to MAX_REGENERATIONS retries. Returns (case, errors of the last attempt)."""
    codes = plan_flaws(seed, program, index, n)
    planned = plan_profile(seed, program, index)
    candidate = plan_name(seed, program, index)
    errors: list[str] = []
    for attempt in range(1 + MAX_REGENERATIONS):
        prompt = build_prompt(program, planned, codes, candidate, errors)
        try:
            result = ask_structured(
                llm, system=SYSTEM, content=[{"type": "text", "text": prompt}],
                output_model=SyntheticCase, effort="high",
            )
        except LLMError as exc:  # a model error (limit, network) is not cured by retries
            return None, [f"model error: {exc}"]
        case = normalise(result.model_dump(), planned)
        errors = validate_case(case, required=codes)
        if not errors:
            return case, []
        print(f"  attempt {attempt + 1} rejected: {len(errors)} problem(s), first: {errors[0]}")
    return None, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic CVs with planted flaws")
    parser.add_argument("--program", default="all", help="program key from cvmax/profile.py, or 'all'")
    parser.add_argument("--n", type=int, default=3, help="cases per program")
    parser.add_argument("--seed", type=int, default=1, help="seed of the flaw mix and of the file names")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    args = parser.parse_args(argv)
    if args.program != "all" and args.program not in PROGRAMS:
        print(f"Unknown program {args.program!r}. Use one of: all, {', '.join(PROGRAMS)}.")
        return 1
    if args.n < 1:
        print("--n must be at least 1.")
        return 1
    programs = list(PROGRAMS) if args.program == "all" else [args.program]
    try:
        llm = make_llm()
    except LLMError as exc:
        print(exc)
        return 1
    if llm is None:
        print("GEMINI_API_KEY or ANTHROPIC_API_KEY is required.")
        return 1

    written = skipped = failed = 0
    for program in programs:
        for index in range(1, args.n + 1):
            stem = f"{program}_s{args.seed}_{index}"
            if not args.force and any((args.out / f"{stem}{ext}").exists() for ext in (".json", ".txt")):
                print(f"[{stem}] exists, skipped (use --force to overwrite)")
                skipped += 1
                continue
            print(f"[{stem}] generating: {', '.join(plan_flaws(args.seed, program, index, args.n))}")
            case, errors = generate_case(llm, program, args.seed, index, args.n)
            if case is None:
                print(f"[{stem}] skipped, no valid case: {'; '.join(e[:120] for e in errors[:3])}")
                failed += 1
                continue
            write_case(case, stem, args.out, force=args.force)
            print(f"[{stem}] written")
            written += 1
    print(f"Done: {written} written, {skipped} already existed, {failed} failed.")
    return 1 if failed and not written else 0


if __name__ == "__main__":
    raise SystemExit(main())
