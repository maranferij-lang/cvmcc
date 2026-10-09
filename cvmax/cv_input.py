"""Reading a CV from PDF or DOCX."""

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
    text: str  # Extracted text: for applying edits and for Grill me.
    pdf_bytes: bytes | None = None  # The original PDF, so the model also sees the layout.
    pages: int | None = None  # For PDF: the number of pages.

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    def as_content_blocks(self) -> list[dict]:
        """The CV as blocks for a request to Claude."""
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


MAX_PAGES = 5  # A student CV is 1-2 pages. More is almost always not a CV.
MAX_TEXT_CHARS = 40_000  # ~6000 words, three times more than the longest reasonable CV.
PARSE_TIMEOUT_S = 12
ROOT = Path(__file__).resolve().parents[1]
# No more than two parsings at once, so several heavy files together do not take all the server memory.
_PARSE_SLOTS = threading.BoundedSemaphore(2)


def _parse(kind: str, data: bytes) -> dict:
    """Parses the file in a separate process with a memory and time limit."""
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
    if "text" not in result:  # a broken file, or the process died because of a memory or CPU limit
        raise CVReadError(f"Could not read {label}. The file may be damaged.")
    if len(result["text"]) > MAX_TEXT_CHARS:
        raise CVReadError("Too much text for a CV. Keep only the CV itself, without attachments.")
    return result


def _pdf_text(data: bytes) -> tuple[str, int]:
    result = _parse("pdf", data)
    return result["text"], result["pages"]


def _docx_text(data: bytes) -> str:
    return _parse("docx", data)["text"]


def load_cv(filename: str, data: bytes) -> CVFile:
    name = filename.lower()
    if name.endswith(".pdf"):
        text, pages = _pdf_text(data)
        # Scanned PDFs have no text, but the model will still read them as an image.
        return CVFile(filename=filename, text=text, pdf_bytes=data, pages=pages)
    if name.endswith(".docx"):
        text = _docx_text(data)
        if not text:
            raise CVReadError("The DOCX file has no text.")
        return CVFile(filename=filename, text=text)
    if name.endswith(".doc"):
        raise CVReadError("The old .doc format is not supported. Save the file as .docx or .pdf.")
    raise CVReadError("Only PDF and DOCX are supported.")
