"""Share card: valid PNGs in every size, stable bytes, clamped scores, a cleaned and cut role line."""

import hashlib
import io
import subprocess
import sys
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageFont

from cvmax import share_card
from cvmax.share_card import CARD_SIZES, ROLE_MAX_CHARS, clean_role, render_card

ROOT = Path(__file__).resolve().parents[1]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PAPER = (255, 255, 255)
RED = (224, 38, 27)
INK = (21, 23, 28)
SOFT_INK = (96, 97, 101)


@lru_cache(maxsize=None)
def card(after=84, before=None, role="", size="story") -> bytes:
    """Render once per distinct arguments: several tests compare the same cards."""
    return render_card(after, before=before, role=role, size=size)


def open_png(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image.load()
    return image


def chunk_types(data: bytes) -> list[bytes]:
    """Chunk names of a PNG, in order."""
    assert data[:8] == PNG_SIGNATURE
    pos, kinds = 8, []
    while pos < len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        kinds.append(data[pos + 4 : pos + 8])
        pos += 12 + length  # length, type, data, crc
    assert pos == len(data)
    return kinds


def count_pixels(image: Image.Image, color, tolerance=12) -> int:
    """How many pixels are within `tolerance` of `color` in every channel."""
    rgb = image.convert("RGB")
    r, g, b = ImageChops.difference(rgb, Image.new("RGB", rgb.size, color)).split()
    worst = ImageChops.lighter(ImageChops.lighter(r, g), b)
    return worst.point(lambda v: 255 if v <= tolerance else 0).histogram()[255]


# ---------------- Format ----------------


@pytest.mark.parametrize("name", list(CARD_SIZES))
@pytest.mark.parametrize("before", [None, 58])
def test_valid_png_of_the_exact_size(name, before):
    data = card(84, before=before, role="Senior Product Manager", size=name)
    assert data.startswith(PNG_SIGNATURE)
    image = open_png(data)
    assert image.format == "PNG"
    assert image.size == CARD_SIZES[name]
    assert image.mode == "RGB"


def test_sizes_match_the_plan():
    assert CARD_SIZES == {"story": (1080, 1350), "square": (1080, 1080), "og": (1200, 630)}
    assert set(share_card._SPECS) == set(CARD_SIZES)


@pytest.mark.parametrize("size", ["huge", "", "STORY", None, 5, ["story"]])
def test_unknown_size_falls_back_to_story(size):
    data = render_card(84, size=size)  # type: ignore[arg-type]
    assert open_png(data).size == CARD_SIZES["story"]
    assert data == card(84, size="story")


def test_card_is_white_paper_with_ink_and_red_pen():
    image = open_png(card(84, role="Data Analyst"))
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((image.width - 1, image.height - 1)) == (255, 255, 255)
    assert count_pixels(image, RED) > 1000  # the ellipse and the dot of the wordmark
    assert count_pixels(image, INK) > 1000  # the score, the wordmark, the role


@pytest.mark.parametrize("name", list(CARD_SIZES))
@pytest.mark.parametrize("before", [None, 88, 100])
@pytest.mark.parametrize("after", [100, 88, 0])
def test_content_stays_inside_the_margin(name, before, after):
    """The widest cases (three digits, a full row, 40 wide letters) never touch the edges."""
    image = open_png(card(after, before=before, role="W" * 80, size=name))
    white = Image.new("RGB", image.size, (255, 255, 255))
    left, top, right, bottom = ImageChops.difference(image.convert("RGB"), white).getbbox()
    width, height = image.size
    assert min(left, top, width - right, height - bottom) >= 32


# ---------------- Determinism and metadata ----------------


def test_same_arguments_give_identical_bytes():
    first = render_card(84, role="Product Manager")
    assert first == render_card(84, role="Product Manager")
    progress = render_card(84, before=58, role="Product Manager", size="square")
    assert progress == render_card(84, before=58, role="Product Manager", size="square")


def test_bytes_are_stable_across_processes():
    """No dependence on hash seeds, time or process state."""
    script = (
        "import sys, hashlib\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from cvmax.share_card import render_card\n"
        "print(hashlib.sha256(render_card(84, before=58, role='Product Manager', size='og')).hexdigest())\n"
    )
    hashes = set()
    for seed in ("0", "12345"):
        env = {"PYTHONHASHSEED": seed, "PATH": ""}
        out = subprocess.run(
            [sys.executable, "-I", "-c", script, str(ROOT)], capture_output=True, text=True, check=True, env=env
        )
        hashes.add(out.stdout.strip())
    assert hashes == {hashlib.sha256(card(84, 58, "Product Manager", "og")).hexdigest()}


@pytest.mark.parametrize("before", [None, 58])
def test_png_has_no_timestamp_or_text_chunks(before):
    kinds = chunk_types(card(84, before=before, role="Product Manager"))
    assert kinds[0] == b"IHDR" and kinds[-1] == b"IEND"
    assert b"IDAT" in kinds
    assert not {b"tIME", b"tEXt", b"zTXt", b"iTXt", b"eXIf"} & set(kinds)


# ---------------- Scores ----------------


def test_scores_are_clamped_to_0_100():
    assert card(150) == card(100)
    assert card(-3) == card(0)
    assert card(84, before=150) == card(84, before=100)
    assert card(100, before=-3) == card(100, before=0)
    assert card(100) != card(99)
    assert card(0) != card(1)


@pytest.mark.parametrize("bad", [None, "x", "", float("nan"), float("inf"), float("-inf"), [], 10**30])
def test_odd_scores_never_raise(bad):
    assert open_png(render_card(bad)).size == CARD_SIZES["story"]
    assert open_png(render_card(84, before=bad)).size == CARD_SIZES["story"]


def test_unreadable_scores_become_zero_and_a_missing_before_is_ignored():
    assert card(float("nan")) == card(0)
    assert card(float("inf")) == card(100)
    assert render_card(84, before=float("nan")) == card(84)


def test_numeric_strings_and_floats_are_accepted():
    assert render_card("84") == card(84)
    assert render_card(83.6) == card(84)


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0),
        (7, 7),
        (84, 84),
        (100, 100),
        (101, 100),
        (-1, 0),
        (10**30, 100),
        (-(10**30), 0),
        (10**400, 100),  # too large for a float: used to raise OverflowError
        (-(10**400), 0),
        (10**4000, 100),
        (84.4, 84),
        (83.6, 84),
        (-0.4, 0),
        (1e308, 100),
        (-1e308, 0),
        (float("inf"), 100),
        (float("-inf"), 0),
        (float("nan"), None),
        ("84", 84),
        (" 84.6 ", 85),
        ("1e999", 100),
        ("-1e999", 0),
        ("9" * 5000, 100),
        ("nan", None),
        ("x", None),
        ("", None),
        (None, None),
        ([], None),
        ({}, None),
        (object(), None),
        (True, 1),
        (Fraction(10**400), 100),
        (Fraction(-(10**400)), 0),
        (Decimal("1e400"), 100),
        (Decimal("NaN"), None),
    ],
)
def test_clamp_score_covers_every_kind_of_input(value, expected):
    assert share_card._clamp_score(value) == expected


@pytest.mark.parametrize("name", list(CARD_SIZES))
def test_huge_numbers_do_not_raise_and_clamp_by_sign(name):
    assert render_card(10**400, size=name) == render_card(100, size=name)
    assert render_card(-(10**400), size=name) == render_card(0, size=name)
    assert render_card(50, before=10**400, size=name) == render_card(50, before=100, size=name)
    assert render_card(50, before=-(10**400), size=name) == render_card(50, before=0, size=name)
    assert render_card(10**400, before=-(10**400), size=name) == render_card(100, before=0, size=name)


def test_render_card_survives_every_odd_score_kind():
    odd = [10**400, -(10**400), 1e400, float("nan"), float("-inf"), "1e999", "9" * 5000, None, "x", [], {}, object()]
    for value in odd:
        assert open_png(render_card(value, before=value)).size == CARD_SIZES["story"]


# ---------------- Before / after ----------------


def test_before_after_differs_from_single():
    single = card(84, role="Product Manager")
    progress = card(84, before=58, role="Product Manager")
    assert single != progress
    assert open_png(single).size == open_png(progress).size
    assert progress != card(84, before=59, role="Product Manager")
    assert progress != card(85, before=58, role="Product Manager")


def test_before_none_is_the_single_variant():
    assert card(84, before=None, role="Product Manager") == card(84, role="Product Manager")


def test_progress_variant_shows_the_old_score_in_soft_ink():
    single, progress = open_png(card(84)), open_png(card(84, before=58))
    width, height = single.size
    left_middle = (0, int(height * 0.35), width // 2, int(height * 0.6))  # where the old number sits
    # The single card only has antialiasing pixels of this colour there; the old number is a solid shape.
    assert count_pixels(single.crop(left_middle), SOFT_INK) < 500
    assert count_pixels(progress.crop(left_middle), SOFT_INK) > 5000


# ---------------- Role line ----------------


@pytest.mark.parametrize(
    "role, expected",
    [
        ("Product Manager", "Product Manager"),
        ("  Data \t Analyst\n", "Data Analyst"),
        ("Data Analyst", "Data Analyst"),
        ("Data\x00 Ana\x07lyst\x1b[31m", "Data Analyst[31m"),
        ("Dev​Ops‮", "DevOps"),
        ("\ud800Engineer", "Engineer"),
        ("Ведучий інженер", "Ведучий інженер"),
        ("\x00\x01\t\n", ""),
        ("", ""),
        (None, ""),
        (123, ""),
        ("x" * ROLE_MAX_CHARS, "x" * ROLE_MAX_CHARS),
        ("x" * (ROLE_MAX_CHARS + 1), "x" * (ROLE_MAX_CHARS - 1) + "…"),
    ],
)
def test_clean_role(role, expected):
    assert clean_role(role) == expected


@pytest.mark.parametrize(
    "role, expected",
    [
        ("数据分析师 Data Analyst", "Data Analyst"),
        ("محلل بيانات Data", "Data"),
        ("Data Analyst 🚀🚀", "Data Analyst"),
        ("Data 🚀 Analyst", "Data Analyst"),
        ("שלום Data Analyst", "Data Analyst"),
        ("ข้อมูล Data", "Data"),
        ("Ведучий інженер Їжак ґ є", "Ведучий інженер Їжак ґ є"),
        ("Ελληνικά Άλφα", "Ελληνικά Άλφα"),
        ("Café Ünïcödé Đạt Ångström Łódź", "Café Ünïcödé Đạt Ångström Łódź"),
        ("Dev\u00a0Ops", "Dev Ops"),
        ("Sales & R&D / QA (EMEA) #1 100% 5€ ™", "Sales & R&D / QA (EMEA) #1 100% 5€ ™"),
        ("Lead – UX \u201cDesign\u201d \u2022 Web", "Lead – UX \u201cDesign\u201d \u2022 Web"),
        ("e\u0301" * 3, "éééé"[:3]),  # combining marks compose into the Latin-1 letter, nothing is left over
    ],
)
def test_clean_role_keeps_only_what_the_font_can_draw(role, expected):
    assert clean_role(role) == expected


@pytest.mark.parametrize("role", ["数据分析师", "محلل بيانات", "🚀🚀", "日本語のエンジニア", "\u0301\u0302", "— … !!", "   ", "\u200b"])
def test_role_with_nothing_readable_is_dropped_entirely(role):
    assert clean_role(role) == ""
    assert render_card(84, role=role) == card(84)  # no empty boxes, no stray punctuation: no role line at all


def _drawable_font_masks():
    """Pillow's view of the bundled fonts: a glyph mask per character, and the mask of the missing-glyph box."""
    for name in (share_card.FONT_BOLD, share_card.FONT_REGULAR):
        font = ImageFont.truetype(str(share_card.FONT_DIR / name), 48)
        missing = bytes(font.getmask("\U0010fffe"))  # unassigned: always the .notdef box
        assert any(missing)
        yield font, missing


def test_every_allowed_character_has_a_real_glyph_in_both_fonts():
    for font, missing in _drawable_font_masks():
        for low, high in share_card._DRAWABLE_RANGES:
            for code in range(low, high + 1):
                char = chr(code)
                assert share_card._drawable(char)
                if char != " ":
                    assert bytes(font.getmask(char)) != missing, f"U+{code:04X} {char!r} is drawn as an empty box"


@pytest.mark.parametrize("char", ["数", "日", "あ", "ع", "ก", "🚀", "😀", "\ue000", "\u0530"])
def test_characters_the_font_lacks_are_not_allowed(char):
    """These are drawn as the empty missing-glyph box, so the filter must drop them."""
    assert not share_card._drawable(char)
    for font, missing in _drawable_font_masks():
        assert bytes(font.getmask(char)) == missing


@pytest.mark.parametrize("char", ["ש", "\u0301", "\u200b", "\u202e", "\u00ad", "\u00a0"])
def test_characters_that_would_draw_badly_are_not_allowed(char):
    """Hebrew is drawn left to right, lone combining marks float, the others are invisible controls."""
    assert not share_card._drawable(char)


def test_ranges_are_sorted_and_do_not_overlap():
    ranges = share_card._DRAWABLE_RANGES
    assert all(low <= high for low, high in ranges)
    assert all(a[1] < b[0] for a, b in zip(ranges, ranges[1:]))


def test_clean_role_returns_exactly_what_is_drawn():
    for role in ("数据分析师 Data Analyst", "Data Analyst 🚀🚀", "محلل بيانات Data"):
        assert render_card(84, role=role) == render_card(84, role=clean_role(role))
    assert card(84, role="数据分析师 Data Analyst") == card(84, role="Data Analyst")


def test_clean_role_cut_does_not_leave_a_space_before_the_ellipsis():
    assert clean_role("a" * 38 + " bbbbbb") == "a" * 38 + "…"


def test_stack_of_combining_marks_is_limited():
    assert len(clean_role("a" + "́̂̃" * 30)) <= 2


def test_long_role_with_control_characters_is_cut():
    role = ("Senior\x00 Product\x07\tManager\n\x1b" * 12)[:200]
    assert len(role) == 200
    data = card(84, role=role)
    assert open_png(data).size == CARD_SIZES["story"]
    cleaned = clean_role(role)
    assert len(cleaned) <= ROLE_MAX_CHARS
    assert cleaned.endswith("…") and cleaned.isprintable()
    assert data == card(84, role=cleaned)  # exactly the cut text is what gets drawn
    assert data != card(84, role="")


def test_role_changes_the_card_and_empty_role_draws_nothing():
    assert card(84, role="Data Analyst") != card(84)
    assert card(84, role="\x00\x01") == card(84)
    assert card(84, role="Data Analyst") != card(84, role="Data Engineer")


@pytest.mark.parametrize("name", list(CARD_SIZES))
def test_wide_letters_and_other_alphabets_render(name):
    for role in ("W" * 200, "М" * 200, "日本語のエンジニア", "\U0001f600 Engineer"):
        assert open_png(card(84, before=58, role=role, size=name)).size == CARD_SIZES[name]


def test_non_string_role_never_raises():
    assert render_card(84, role=None) == card(84)  # type: ignore[arg-type]
    assert render_card(84, role=["x"]) == card(84)  # type: ignore[arg-type]


# ---------------- Layout ----------------


@pytest.mark.parametrize("name", list(CARD_SIZES))
@pytest.mark.parametrize("text", ["7", "1", "0", "9", "84", "100"])
def test_circle_is_never_a_tall_portrait_oval(name, text):
    circled = share_card._measure_circled(text, share_card._SPECS[name].score)
    assert circled.rx >= circled.ry * share_card.ELLIPSE_MIN_ASPECT - 1e-9
    assert circled.outer_width >= circled.outer_height * 0.85  # at most slightly taller than wide


def test_two_digit_circle_is_still_wider_than_tall():
    circled = share_card._measure_circled("84", share_card._SPECS["story"].score)
    assert circled.outer_width > circled.outer_height * 1.2


def _red_box(image: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of the red pen below the wordmark (the wordmark's dot is red too)."""
    rgb = image.convert("RGB")
    r, g, b = ImageChops.difference(rgb, Image.new("RGB", rgb.size, RED)).split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v <= 40 else 0)
    top = int(image.height * 0.15)
    box = mask.crop((0, top, image.width, image.height)).getbbox()
    assert box is not None
    return box[0], box[1] + top, box[2], box[3] + top


@pytest.mark.parametrize("name", list(CARD_SIZES))
def test_single_digit_circle_is_not_a_tall_oval_on_the_card(name):
    left, top, right, bottom = _red_box(open_png(card(7, role="Data Analyst", size=name)))
    assert (right - left) >= (bottom - top) * 0.85


@pytest.mark.parametrize("name", list(CARD_SIZES))
@pytest.mark.parametrize("before", [None, 58])
def test_card_without_a_role_line_is_centred_not_lopsided(name, before):
    """With no role the score block sits in the middle of the free band, not higher with a hole below it."""
    image = open_png(card(84, before=before, role="数据分析师", size=name))
    spec = share_card._SPECS[name]
    top, bottom = spec.margin + spec.wordmark, image.height - spec.margin - spec.footer
    white = Image.new("RGB", image.size, PAPER)
    box = ImageChops.difference(image.convert("RGB"), white).crop((0, top, image.width, bottom)).getbbox()
    assert box is not None
    free_above, free_below = box[1], (bottom - top) - box[3]
    assert abs(free_above - free_below) <= (bottom - top) * 0.06
    assert free_above > 0 and free_below > 0


def test_single_score_without_role_has_the_ellipse_centred_on_the_card():
    image = open_png(card(84, size="square"))
    left, top, right, bottom = _red_box(image)
    middle_of_band = (share_card._SPECS["square"].margin + 50 + image.height - share_card._SPECS["square"].margin - 28) / 2
    assert abs((top + bottom) / 2 - middle_of_band) < 40


# ---------------- Fonts ----------------


def test_bundled_fonts_exist():
    for name in (share_card.FONT_BOLD, share_card.FONT_REGULAR):
        assert (share_card.FONT_DIR / name).is_file()


def test_missing_font_file_falls_back_instead_of_failing(monkeypatch):
    share_card._font.cache_clear()
    monkeypatch.setattr(share_card, "FONT_DIR", ROOT / "no-such-fonts")
    try:
        data = render_card(84, before=58, role="Product Manager")
    finally:
        monkeypatch.undo()
        share_card._font.cache_clear()
    assert open_png(data).size == CARD_SIZES["story"]
