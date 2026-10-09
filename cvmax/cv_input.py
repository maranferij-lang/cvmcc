"""Читання CV з PDF або DOCX."""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


class CVReadError(ValueError):
    pass


@dataclass
class CVFile:
    filename: str
    text: str  # Витягнутий текст: для застосування правок і для Grill me.
    pdf_bytes: bytes | None = None  # Оригінал PDF, щоб модель бачила й верстку.
    pages: int | None = None  # Для PDF: кількість сторінок.
    images: int | None = None  # Для PDF: кількість вбудованих зображень; для DOCX і тексту None.

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def as_content_blocks(self) -> list[dict]:
        """CV у форматі блоків для запиту до Claude."""
        if self.pdf_bytes is not None:
            return [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(self.pdf_bytes).decode("ascii"),
                    },
                    "title": self.filename,
                }
            ]
        return [{"type": "text", "text": f"<cv filename=\"{self.filename}\">\n{self.text}\n</cv>"}]


MAX_PAGES = 5  # CV студента: 1-2 сторінки. Більше майже завжди не CV.
MAX_TEXT_CHARS = 40_000  # ~6000 слів, утричі більше за найдовше розумне CV.
PARSE_TIMEOUT_S = 12
ROOT = Path(__file__).resolve().parents[1]
# Не більше двох розборів одночасно, щоб кілька важких файлів разом не забрали всю пам'ять сервера.
_PARSE_SLOTS = threading.BoundedSemaphore(2)


def _parse(kind: str, data: bytes) -> dict:
    """Розбирає файл в окремому процесі з лімітом пам'яті й часу."""
    label = kind.upper()
    try:
        with _PARSE_SLOTS:
            proc = subprocess.run(
                [sys.executable, "-m", "cvmax.parse_worker", kind, str(MAX_PAGES)],
                input=data,
                capture_output=True,
                timeout=PARSE_TIMEOUT_S,
                cwd=ROOT,
            )
        result = json.loads(proc.stdout or b"{}")
    except (subprocess.TimeoutExpired, ValueError) as e:
        raise CVReadError(f"Could not read {label}: the file is too large or damaged.") from e
    error = result.get("error")
    if error == "pages":
        raise CVReadError(f"The file has {result.get('pages')} pages. A CV should be 1-2 pages, {MAX_PAGES} at most.")
    if error == "too_big":
        raise CVReadError(f"Could not read {label}: the file is too large or damaged.")
    if "text" not in result:  # битий файл або процес упав через ліміт пам'яті чи CPU
        if proc.returncode < 0:  # убитий сигналом (ліміт CPU, SIGXCPU/SIGKILL): файл надто важкий, а не битий
            raise CVReadError(f"Could not read {label}: the file is too large or damaged.")
        raise CVReadError(f"Could not read {label}. The file may be damaged.")
    if len(result["text"]) > MAX_TEXT_CHARS:
        raise CVReadError("Too much text for a CV. Keep only the CV itself, without attachments.")
    return result


def _pdf_text(data: bytes) -> tuple[str, int, int | None]:
    result = _parse("pdf", data)
    images = result.get("images")
    # Метрика не критична: чужий або непередбачений формат значення просто стає None.
    if not isinstance(images, int) or isinstance(images, bool) or images < 0:
        images = None
    return result["text"], result["pages"], images


def _docx_text(data: bytes) -> str:
    return _parse("docx", data)["text"]


def load_cv(filename: str, data: bytes) -> CVFile:
    name = filename.lower()
    if name.endswith(".pdf"):
        text, pages, images = _pdf_text(data)
        # Скановані PDF не мають тексту, але модель усе одно прочитає їх як картинку.
        return CVFile(filename=filename, text=text, pdf_bytes=data, pages=pages, images=images)
    if name.endswith(".docx"):
        text = _docx_text(data)
        if not text:
            raise CVReadError("The DOCX file has no text.")
        return CVFile(filename=filename, text=text)
    if name.endswith(".doc"):
        raise CVReadError("The old .doc format is not supported. Save the file as .docx or .pdf.")
    raise CVReadError("Only PDF and DOCX are supported.")
