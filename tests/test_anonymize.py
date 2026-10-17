import time

from cvmax.anonymize import CONSENT_VERSION, MAX_CHARS, anonymize_obj, anonymize_text


def test_email_phone_links_are_removed():
    text = ("Contact: jane.example@example.com, +380 50 123 45 67, (555) 010-1234\n"
            "linkedin.com/in/jane-example https://github.com/jane-example www.jane-example.io")
    out = anonymize_text(text)
    assert "@" not in out and "555" not in out and "380" not in out
    assert "linkedin" not in out and "github" not in out and "jane-example" not in out
    assert out.count("[email]") == 1 and out.count("[phone]") == 2 and out.count("[link]") == 3


def test_year_ranges_dates_and_tech_names_survive():
    text = "2019 - 2023, 2019 2021 2023 2024; 12.03.2020 15.04.2021. Node.js, Next.js, ASP.NET. Revenue up 20%."
    assert anonymize_text(text) == text


def test_names_whole_words_case_insensitive_latin_and_cyrillic():
    out = anonymize_text("JANE Example led a team. Janitor joined. Ярослава Іваненко, ярослава.", ["Jane Example", "Ярослава Іваненко"])
    assert out.startswith("[name] [name] led a team. Janitor joined.")
    assert "Ярослава" not in out and "Іваненко" not in out and "ярослава" not in out


def test_short_name_words_are_ignored():
    assert anonymize_text("A B went", ["A B"]) == "A B went"


def test_date_of_birth_only_the_date_is_replaced():
    for line in ("Date of birth: 12 March 1999", "DOB 12.03.1999", "Born: March 12, 1999",
                 "Дата народження: 5 травня 2000", "Народження 1999-03-12"):
        out = anonymize_text(line)
        assert out.endswith("[date of birth]"), out
        assert "1999" not in out and "2000" not in out
    assert anonymize_text("Studied 2018 - 2022") == "Studied 2018 - 2022"


def test_anonymize_obj_walks_dicts_and_lists_and_keeps_other_types():
    obj = {"summary": "Mail me: a@example.com", "edits": [{"before": "Jane did x", "n": 3, "ok": True, "none": None}],
           "t": ("www.example.com",)}
    out = anonymize_obj(obj, ["Jane"])
    assert out == {"summary": "Mail me: [email]", "edits": [{"before": "[name] did x", "n": 3, "ok": True, "none": None}],
                   "t": ["[link]"]}
    assert obj["summary"] == "Mail me: a@example.com"  # the input is not changed


def test_input_is_capped_and_non_strings_are_safe():
    assert len(anonymize_text("x" * (MAX_CHARS + 500))) == MAX_CHARS
    assert anonymize_text(None) == ""  # type: ignore[arg-type]
    assert anonymize_text("hi", [None, 5]) == "hi"  # type: ignore[list-item]


def test_regexes_are_linear_on_hostile_input():
    hostile = ["a." * 20000, "1 " * 20000, "-" * 40000, "a@" * 20000, "a" * 40000, "(" * 40000,
               "http://" + "a" * 40000, "born " * 8000, "0." * 20000, "x-" * 20000 + ".com"]
    start = time.perf_counter()
    for text in hostile:
        anonymize_text(text, ["Jane Example"])
    assert time.perf_counter() - start < 5


def test_consent_version_is_a_date_string():
    assert CONSENT_VERSION == "2026-10-17"


def test_urls_and_domains_are_case_insensitive_and_telegram_links():
    out = anonymize_text("LinkedIn.com/in/olena-k-123 GitHub.com/olenak t.me/olenak HTTPS://X.IO/a")
    assert out == "[link] [link] [link] [link]"


def test_messenger_handles():
    text = ("@olenak_dev | Telegram: @ivan_petrenko | GitHub: ivanp | Skype: ivan.petrenko | "
            "Viber/WhatsApp: ivanp_x | Instagram: ivan.p | Behance: ivanp | Dribbble: ivanp")
    out = anonymize_text(text)
    for leak in ("olenak", "ivan", "ivanp", "Telegram", "Skype"):
        assert leak not in out
    assert out.count("[link]") == 8
    assert anonymize_text("mail a.b@example.com") == "mail [email]"
    assert anonymize_text("a @b x") == "a @b x"  # too short for a handle


def test_first_line_is_a_name_and_transliterations_and_inflections():
    cv = "Іван Петренко\nPetrenko Ivan led a team. Івана Петренка, Івану Петренку, Ivan.\nPython"
    out = anonymize_text(cv)
    for leak in ("Іван", "Петренк", "Petrenko", "Ivan", "Івану", "Петренку"):
        assert leak not in out
    assert "led a team" in out and "Python" in out
    # Google name in the other script
    out = anonymize_text("Olena Kovalenko worked. Олена Коваленко, Коваленка.", ["Олена Коваленко"])
    assert "Olena" not in out and "Kovalenko" not in out and "Коваленка" not in out
    out = anonymize_text("Yuliia and Iuliia", ["Юлія"])
    assert out == "[name] and [name]"


def test_first_line_rules():
    from cvmax.anonymize import cv_first_line_name
    assert cv_first_line_name("\n\n Jane Example \nx") == "Jane Example"
    assert cv_first_line_name("jane example") == ""
    assert cv_first_line_name("Jane") == ""
    assert cv_first_line_name("A B C D E") == ""
    assert cv_first_line_name("Jane Example 2") == ""
    assert cv_first_line_name("Email: Jane Example") == ""
    assert cv_first_line_name("Summary\nJane Example") == ""
    assert cv_first_line_name("Aaaa Bbbb " + "c" * 60) == ""


def test_name_words_are_capped_and_obj_does_not_guess_names_from_lines():
    names = [f"Word{chr(97 + i)}x" for i in range(30)]
    anonymize_text("hello", names)
    assert anonymize_obj({"s": "Strong Summary here"}) == {"s": "Strong Summary here"}
