"""Застосування прийнятих правок «було / стало» до тексту CV і експорт."""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from docx import Document

from .schemas import Edit


@dataclass
class ApplyReport:
    text: str
    applied: list[Edit] = field(default_factory=list)
    added: list[Edit] = field(default_factory=list)
    not_found: list[Edit] = field(default_factory=list)


def _loose_pattern(snippet: str) -> re.Pattern[str]:
    # Текст із PDF часто має зайві переноси й пробіли, тому шукаємо з гнучкими пропусками.
    words = snippet.split()
    return re.compile(r"\s+".join(re.escape(w) for w in words))


def apply_edits(cv_text: str, edits: list[Edit]) -> ApplyReport:
    report = ApplyReport(text=cv_text)
    for edit in edits:
        before = edit.before.strip()
        if not before:
            if edit.after.strip():
                report.added.append(edit)
            continue
        if before in report.text:
            report.text = report.text.replace(before, edit.after.strip(), 1)
            report.applied.append(edit)
            continue
        match = _loose_pattern(before).search(report.text)
        if match:
            report.text = report.text[: match.start()] + edit.after.strip() + report.text[match.end() :]
            report.applied.append(edit)
        else:
            report.not_found.append(edit)

    # Прибрані пункти лишають порожні рядки, чистимо їх.
    report.text = re.sub(r"\n{3,}", "\n\n", report.text).strip()

    if report.added:
        by_section: dict[str, list[str]] = {}
        for e in report.added:
            by_section.setdefault(e.section.strip() or "Other", []).append(e.after.strip())
        extra = ["", "", "=== NEW ITEMS (move them into the right section) ==="]
        for section, items in by_section.items():
            extra.append(f"\n{section}")
            extra.extend(f"- {item}" for item in items)
        report.text += "\n".join(extra)
    return report


def changes_markdown(edits: list[Edit]) -> str:
    lines = ["# CVMAX: прийняті правки", ""]
    for i, e in enumerate(edits, 1):
        lines.append(f"## {i}. {e.section}")
        if e.before.strip():
            lines.append(f"**Було:** {e.before.strip()}")
        lines.append(f"**Стало:** {e.after.strip() or '(прибрати)'}")
        lines.append(f"_Чому:_ {e.reason.strip()}")
        lines.append("")
    return "\n".join(lines)


def text_to_docx(text: str) -> bytes:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
