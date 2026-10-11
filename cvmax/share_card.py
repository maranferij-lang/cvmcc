"""Share card: a PNG with the review score, made to be posted.

White paper, ink text and a red pen: the score sits in a hand-drawn red ellipse, like the app's metric.
Pillow only, fonts from `assets/fonts`, no network. The card shows a score, an optional previous score, a role line
and the site name; it never shows names, dates or anything else from the CV.

The output is deterministic: the same arguments give byte-identical PNGs (everything is computed, nothing is random,
and the PNG has no metadata chunks, so no timestamp).
`render_card` never raises on odd input: scores are clamped to 0..100, an unknown size falls back to "story".
"""

from __future__ import annotations

import io
import math
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Output sizes in pixels (width, height): Instagram/LinkedIn story-like portrait, square post, link preview.
CARD_SIZES: dict[str, tuple[int, int]] = {"story": (1080, 1350), "square": (1080, 1080), "og": (1200, 630)}
DEFAULT_SIZE = "story"

# Brand tokens, the same as in `assets/style.css`. INK_SOFT is ink at 68% over white (the app's `--ink-2`).
PAPER = (255, 255, 255)
INK = (21, 23, 28)
INK_SOFT = (96, 97, 101)
RED = (224, 38, 27)

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_BOLD = "LiberationSans-Bold.ttf"
FONT_REGULAR = "LiberationSans-Regular.ttf"

# Texts on the card.
WORDMARK = ("Get", "CVmax")  # regular + bold, followed by a red dot, like the logo
CAPTION_PROGRESS = "after one review"
FOOTER = "Checked with GetCVmax · getcvmax.com"
ROLE_MAX_CHARS = 40
# Code point ranges (inclusive) that both bundled Liberation Sans fonts draw with a real glyph: Basic Latin, Latin-1,
# Latin Extended-A/B and Additional, Greek, Cyrillic (with Ukrainian letters), common punctuation, currency and
# symbols. Anything else would be drawn as an empty box. `tests/test_share_card.py` checks every range against the fonts.
_DRAWABLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x0020, 0x007E),  # Basic Latin
    (0x00A1, 0x00AC),  # Latin-1 (without NBSP, which becomes a space, and the soft hyphen)
    (0x00AE, 0x00FF),
    (0x0100, 0x024F),  # Latin Extended-A and B
    (0x0384, 0x038A),  # Greek (modern letters and accented capitals)
    (0x038C, 0x038C),
    (0x038E, 0x03A1),
    (0x03A3, 0x03CE),
    (0x0400, 0x04FF),  # Cyrillic, incl. Ukrainian і ї є ґ
    (0x1E00, 0x1E9B),  # Latin Extended Additional (Vietnamese and others)
    (0x1EA0, 0x1EF9),
    (0x2010, 0x2010),  # punctuation: hyphen, dashes, quotes, daggers, bullet, ellipsis, per mille, primes, guillemets
    (0x2012, 0x2015),
    (0x2018, 0x201E),
    (0x2020, 0x2022),
    (0x2026, 0x2026),
    (0x2030, 0x2030),
    (0x2032, 0x2034),
    (0x2039, 0x203A),
    (0x20A0, 0x20B5),  # currency signs incl. the euro
    (0x20BF, 0x20BF),
    (0x2116, 0x2116),  # numero sign
    (0x2122, 0x2122),  # trade mark
    (0x2190, 0x2195),  # arrows
    (0x2212, 0x2212),  # minus
    (0x221E, 0x221E),  # infinity
    (0x2248, 0x2248),  # almost equal
    (0x2260, 0x2260),
    (0x2264, 0x2265),
)

# Pen drawing: stroke width in output pixels, supersampling for smooth edges.
STROKE = 6
SUPERSAMPLE = 3
ELLIPSE_STEPS = 270
ELLIPSE_TILT = 6.0  # degrees counter-clockwise, like the app's `rotate(-6deg)`
ELLIPSE_MIN_ASPECT = 0.92  # smallest width / height of the ellipse around a score
SPIRAL = 1.05  # the pen ends a little outside where it started; this is the largest radius factor


@dataclass(frozen=True)
class _Spec:
    """Font sizes and margins of one card size, in pixels."""

    margin: int
    wordmark: int
    footer: int
    score: int  # the circled score, before it is fitted to the width
    caption: int
    role: int


_SPECS = {
    "story": _Spec(margin=72, wordmark=56, footer=30, score=380, caption=40, role=50),
    "square": _Spec(margin=64, wordmark=50, footer=28, score=330, caption=36, role=46),
    "og": _Spec(margin=44, wordmark=36, footer=22, score=190, caption=26, role=32),
}


# ---------------- Input cleaning ----------------


def _clamp_score(value: object) -> int | None:
    """A score as an int in 0..100, or None when the value is missing or not a number.

    Huge numbers do not raise: an int too large for a float clamps by its sign (to 100 or to 0).
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except OverflowError:
        # Integers (and fractions) beyond the float range: only the sign matters.
        try:
            return 100 if value > 0 else 0  # type: ignore[operator]
        except Exception:  # noqa: BLE001 - whatever a strange object does, the card must still render
            return None
    except (TypeError, ValueError, ArithmeticError):
        return None
    if math.isnan(number):
        return None
    return int(round(max(0.0, min(100.0, number))))


def _drawable(char: str) -> bool:
    """Whether the bundled font has a real glyph for `char` (checked against the font files in the tests)."""
    code = ord(char)
    return any(low <= code <= high for low, high in _DRAWABLE_RANGES)


def clean_role(role: object) -> str:
    """The role line as drawn: only characters the bundled font can draw, one line, at most ROLE_MAX_CHARS.

    Characters outside the font's coverage (CJK, Arabic, Hebrew, emoji, combining marks...) are dropped, because the
    font would draw an empty box for them. A role with no letter or digit left gives "": the card has no role line.
    A long role is cut with an ellipsis.
    """
    if not isinstance(role, str):
        return ""
    chars: list[str] = []
    for char in unicodedata.normalize("NFC", role[:1000]):
        category = unicodedata.category(char)
        if char in "\t\n\r\v\f" or category in ("Zs", "Zl", "Zp"):
            chars.append(" ")
        elif _drawable(char):
            chars.append(char)
        # Everything else is dropped: control, zero-width and bidi characters, surrogates, private use, unassigned,
        # combining marks (a stack of them is an attempt to draw over the card) and scripts the font lacks.
    text = " ".join("".join(chars).split())
    if not any(char.isalnum() for char in text):
        return ""  # nothing readable is left: only punctuation and symbols are not a role
    if len(text) > ROLE_MAX_CHARS:
        text = text[: ROLE_MAX_CHARS - 1].rstrip() + "…"
    return text


# ---------------- Fonts and text measuring ----------------


@lru_cache(maxsize=64)
def _font(bold: bool, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """A Liberation Sans font from the repository; Pillow's built-in font if the file is missing."""
    try:
        return ImageFont.truetype(str(FONT_DIR / (FONT_BOLD if bold else FONT_REGULAR)), size)
    except OSError:
        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10.1 has no sized default font
            return ImageFont.load_default()


def _cap_height(font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
    """Height of a capital letter above the baseline."""
    return -font.getbbox("H", anchor="ls")[1]


def _fit_text(text: str, bold: bool, size: int, max_width: float, min_size: int):
    """Shrink the font down to `min_size` until the text fits; if it still does not, cut it with an ellipsis."""
    font = _font(bold, size)
    while font.getlength(text) > max_width and size > min_size:
        size -= 2
        font = _font(bold, size)
    if font.getlength(text) > max_width:
        body = text.rstrip("…").rstrip()
        while body and font.getlength(body + "…") > max_width:
            body = body[:-1].rstrip()
        text = body + "…"
    return font, text


# ---------------- Pen strokes ----------------


def _rotate(x: float, y: float, degrees: float) -> tuple[float, float]:
    """Rotate a point around the origin; positive degrees turn counter-clockwise on screen (y points down)."""
    angle = math.radians(degrees)
    return x * math.cos(angle) + y * math.sin(angle), -x * math.sin(angle) + y * math.cos(angle)


class _Pen:
    """Antialiased strokes in solid colours.

    Strokes are drawn on one mask per colour at SUPERSAMPLE times the size, then scaled down and pasted over the card.
    """

    def __init__(self, size: tuple[int, int]) -> None:
        self._size = size
        self._masks: dict[tuple[int, int, int], tuple[Image.Image, ImageDraw.ImageDraw]] = {}

    def _mask_draw(self, color: tuple[int, int, int]) -> ImageDraw.ImageDraw:
        if color not in self._masks:
            mask = Image.new("L", (self._size[0] * SUPERSAMPLE, self._size[1] * SUPERSAMPLE), 0)
            self._masks[color] = (mask, ImageDraw.Draw(mask))
        return self._masks[color][1]

    def line(self, points: list[tuple[float, float]], color: tuple[int, int, int], width: float = STROKE) -> None:
        """A polyline with round caps and joints."""
        draw = self._mask_draw(color)
        scaled = [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in points]
        draw.line(scaled, fill=255, width=round(width * SUPERSAMPLE))
        radius = width * SUPERSAMPLE / 2
        for x, y in scaled:
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)

    def dot(self, center: tuple[float, float], diameter: float, color: tuple[int, int, int]) -> None:
        draw = self._mask_draw(color)
        x, y = center[0] * SUPERSAMPLE, center[1] * SUPERSAMPLE
        radius = diameter * SUPERSAMPLE / 2
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=255)

    def apply(self, image: Image.Image) -> None:
        for color, (mask, _draw) in self._masks.items():
            image.paste(color, (0, 0), mask.resize(self._size, Image.Resampling.BOX))


def _ellipse_path(cx: float, cy: float, rx: float, ry: float) -> list[tuple[float, float]]:
    """Points of a pen-drawn ellipse.

    It starts at the upper left, goes round once and overshoots its start, spiralling out a little with a slight
    wobble, like a circle drawn with a marker. The whole shape is tilted by ELLIPSE_TILT degrees.
    """
    start = math.radians(205)
    sweep = math.radians(360 * 1.07)
    points = []
    for i in range(ELLIPSE_STEPS + 1):
        t = i / ELLIPSE_STEPS
        theta = start + sweep * t
        grow = 0.985 + 0.045 * t + 0.012 * math.sin(2 * theta + 0.7) + 0.006 * math.sin(3 * theta + 2.1)
        x, y = _rotate(rx * grow * math.cos(theta), ry * grow * math.sin(theta), ELLIPSE_TILT)
        points.append((cx + x, cy + y))
    return points


def _curve(p0: tuple[float, float], p1: tuple[float, float], p2: tuple[float, float], steps: int = 24):
    """A quadratic Bezier curve from p0 to p2 with the control point p1."""
    points = []
    for i in range(steps + 1):
        t = i / steps
        a, b, c = (1 - t) ** 2, 2 * t * (1 - t), t**2
        points.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return points


# ---------------- The card ----------------


@dataclass
class _Card:
    """What every drawing helper needs: the canvas, its text and pen layers, and the size's spec."""

    width: int
    height: int
    spec: _Spec
    image: Image.Image
    draw: ImageDraw.ImageDraw
    pen: _Pen

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.spec.margin


@dataclass(frozen=True)
class _Circled:
    """A number and the ellipse that circles it, measured for a given font size."""

    text: str
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    ink: tuple[float, float, float, float]  # ink box left, top, right, bottom, relative to the left end of the baseline
    rx: float
    ry: float

    @property
    def outer_width(self) -> float:
        return 2 * self.rx * SPIRAL + STROKE

    @property
    def outer_height(self) -> float:
        return 2 * self.ry * SPIRAL + STROKE


def _measure_circled(text: str, size: int) -> _Circled:
    font = _font(True, size)
    ink = font.getbbox(text, anchor="ls")
    rx = (ink[2] - ink[0]) / 2 * 1.3 + size * 0.06
    ry = (ink[3] - ink[1]) / 2 * 1.45 + size * 0.06
    rx = max(rx, ry * ELLIPSE_MIN_ASPECT)  # a single digit gets a round-ish loop, not a tall portrait oval
    return _Circled(text, font, ink, rx, ry)


def _draw_number(card: _Card, text: str, font, cx: float, cy: float, color) -> None:
    """Draw digits so that their ink box is centred on (cx, cy)."""
    left, top, right, bottom = font.getbbox(text, anchor="ls")
    card.draw.text((cx - (left + right) / 2, cy - (top + bottom) / 2), text, font=font, fill=color, anchor="ls")


def _draw_circled(card: _Card, circled: _Circled, cx: float, cy: float) -> None:
    """The score in ink, circled by the red pen."""
    _draw_number(card, circled.text, circled.font, cx, cy, INK)
    card.pen.line(_ellipse_path(cx, cy, circled.rx, circled.ry), RED)


def _draw_wordmark(card: _Card) -> int:
    """"GetCVmax" with the red dot, top left. Returns the y of the baseline."""
    size = card.spec.wordmark
    regular, bold = _font(False, size), _font(True, size)
    x, baseline = card.spec.margin, card.spec.margin + _cap_height(bold)
    card.draw.text((x, baseline), WORDMARK[0], font=regular, fill=INK, anchor="ls")
    x += regular.getlength(WORDMARK[0])
    card.draw.text((x, baseline), WORDMARK[1], font=bold, fill=INK, anchor="ls")
    x += bold.getlength(WORDMARK[1])
    dot = size * 0.17
    card.pen.dot((x + size * 0.05 + dot / 2, baseline - dot / 2), dot, RED)
    return baseline


def _draw_footer(card: _Card) -> int:
    """The site line, centred at the bottom. Returns the y of its baseline."""
    font, text = _fit_text(FOOTER, False, card.spec.footer, card.content_width, card.spec.footer - 8)
    baseline = card.height - card.spec.margin
    card.draw.text((card.width / 2, baseline), text, font=font, fill=INK_SOFT, anchor="ms")
    return baseline


def _draw_score(card: _Card, score: int, cy: float) -> float:
    """The single variant: the score in a red ellipse, centred. Returns the bottom of the ellipse."""
    size = card.spec.score
    circled = _measure_circled(str(score), size)
    if circled.outer_width > card.content_width:
        size = int(size * card.content_width / circled.outer_width)
        circled = _measure_circled(str(score), size)
    _draw_circled(card, circled, card.width / 2, cy)
    return cy + circled.outer_height / 2


def _draw_arrow(card: _Card, x0: float, x1: float, cy: float) -> None:
    """A hand-drawn arrow in ink: a slightly bowed shaft and two strokes for the head."""
    length = x1 - x0
    control = ((x0 + x1) / 2, cy - length * 0.08)
    card.pen.line(_curve((x0, cy), control, (x1, cy)), INK)
    dx, dy = x1 - control[0], cy - control[1]  # direction of the shaft at the tip
    norm = math.hypot(dx, dy) or 1.0
    head = length * 0.34
    for turn in (150, -150):
        hx, hy = _rotate(dx / norm * head, dy / norm * head, turn)
        card.pen.line([(x1, cy), (x1 + hx, cy + hy)], INK)


def _draw_struck(card: _Card, text: str, font, left: float, baseline: float) -> None:
    """The old score in soft ink with an ink strike-through, its ink box starting at x=`left`."""
    ink = font.getbbox(text, anchor="ls")
    x = left - ink[0]
    card.draw.text((x, baseline), text, font=font, fill=INK_SOFT, anchor="ls")
    mid = baseline + (ink[1] + ink[3]) / 2
    half = (ink[2] - ink[0]) / 2 * 1.12
    tilt = (ink[3] - ink[1]) * 0.12
    cx = left + (ink[2] - ink[0]) / 2
    card.pen.line([(cx - half, mid + tilt), (cx + half, mid - tilt)], INK)


def _draw_progress(card: _Card, before: int, after: int, cy: float) -> float:
    """The before/after variant: "before -> after", the old score struck through and the new one circled.

    The row is scaled down until it fits the card width. Returns the bottom of the new score's ellipse.
    """
    old_text, new_text = str(before), str(after)
    size = card.spec.score
    available = card.content_width * 0.94  # room for the strike-through, which is wider than the old number
    for _ in range(4):
        circled = _measure_circled(new_text, size)
        old_font = _font(True, round(size * 0.62))
        old_ink = old_font.getbbox(old_text, anchor="ls")
        old_width = old_ink[2] - old_ink[0]
        gap, arrow = size * 0.14, size * 0.42
        total = old_width + gap + arrow + gap + circled.outer_width
        if total <= available:
            break
        size = max(int(size * available / total) - 1, 40)
    x = (card.width - total) / 2
    new_cx = x + total - circled.outer_width / 2
    # The old number sits on the new number's baseline; the new number is centred on `cy`.
    baseline = cy - (circled.ink[1] + circled.ink[3]) / 2
    _draw_struck(card, old_text, old_font, x, baseline)
    arrow_x0 = x + old_width + gap
    _draw_arrow(card, arrow_x0, arrow_x0 + arrow, cy - (circled.ink[3] - circled.ink[1]) * 0.05)
    _draw_circled(card, circled, new_cx, cy)
    return cy + circled.outer_height / 2


def _draw_caption(card: _Card, bottom: float) -> float:
    """"after one review", centred under the score. Returns its baseline."""
    font = _font(False, card.spec.caption)
    baseline = bottom + card.spec.caption * 0.8 + _cap_height(font)
    card.draw.text((card.width / 2, baseline), CAPTION_PROGRESS, font=font, fill=INK_SOFT, anchor="ms")
    return baseline


def _draw_role(card: _Card, role: str, top: float) -> None:
    """The role line in bold ink, centred; shrunk or cut by width if the letters are wide."""
    if not role:
        return
    size = card.spec.role
    font, text = _fit_text(role, True, size, card.content_width, int(size * 0.6))
    baseline = top + _cap_height(font)
    card.draw.text((card.width / 2, baseline), text, font=font, fill=INK, anchor="ms")


def render_card(after: int, *, before: int | None = None, role: str = "", size: str = "story") -> bytes:
    """Render the share card as PNG bytes.

    `after` is the score to show (clamped to 0..100). With `before`, the card shows the progress "before -> after"
    and the caption "after one review". `role` is the target role line (see `clean_role`); `size` is a key of
    CARD_SIZES, an unknown name gives "story". Never raises on odd input.
    """
    name = size if isinstance(size, str) and size in CARD_SIZES else DEFAULT_SIZE
    width, height = CARD_SIZES[name]
    after_score = _clamp_score(after) or 0
    before_score = _clamp_score(before)

    image = Image.new("RGB", (width, height), PAPER)
    card = _Card(width, height, _SPECS[name], image, ImageDraw.Draw(image), _Pen((width, height)))

    wordmark_baseline = _draw_wordmark(card)
    footer_baseline = _draw_footer(card)
    band_top = wordmark_baseline
    band_bottom = footer_baseline - _cap_height(_font(False, card.spec.footer))
    role_text = clean_role(role)
    if role_text:
        cy = band_top + (band_bottom - band_top) * 0.46
    else:
        # No role line: centre the score (and the caption under it, if any) in the band instead of leaving a hole.
        caption_height = card.spec.caption * 0.8 + _cap_height(_font(False, card.spec.caption))
        below = 0.0 if before_score is None else caption_height
        cy = (band_top + band_bottom) / 2 - below / 2

    if before_score is None:
        bottom = _draw_score(card, after_score, cy)
        role_top = bottom + card.spec.role * 0.9
    else:
        bottom = _draw_progress(card, before_score, after_score, cy)
        caption_baseline = _draw_caption(card, bottom)
        role_top = caption_baseline + card.spec.role * 0.9
    _draw_role(card, role_text, role_top)

    card.pen.apply(image)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)  # no pnginfo: no text or time chunks, so the bytes are stable
    return buffer.getvalue()
