"""A separate process for reading the CV.

PDF and DOCX files can be "bombs": a small file that eats gigabytes of memory and minutes of CPU when parsed.
So parsing runs in a child process with a memory and time limit, and a hang does not take down the whole site.
Run: python -m cvmax.parse_worker pdf|docx < file  ->  JSON {"text": ..., "pages": ...} on stdout.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile

MAX_ZIP_ENTRIES = 500
MAX_UNZIPPED_BYTES = 15 * 1024 * 1024
MAX_MEMORY_BYTES = 512 * 1024 * 1024
MAX_CPU_SECONDS = 8


def limit_resources() -> None:
    """The process limits its own memory and CPU. The parent process also stops it on a timeout."""
    try:
        import resource
    except ImportError:  # Windows: only the timeout remains
        return
    resource.setrlimit(resource.RLIMIT_AS, (MAX_MEMORY_BYTES, MAX_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (MAX_CPU_SECONDS, MAX_CPU_SECONDS))


def pdf_text(data: bytes, max_pages: int) -> dict:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    if pages > max_pages:
        return {"error": "pages", "pages": pages}
    text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    return {"text": text, "pages": pages}


def docx_text(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos = z.infolist()
        if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNZIPPED_BYTES:
            return {"error": "too_big"}
    from docx import Document

    doc = Document(io.BytesIO(data))
    lines = [p.text for p in doc.paragraphs]
    # Many CV templates keep text in tables.
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                lines.append(" | ".join(dict.fromkeys(cells)))
    return {"text": "\n".join(lines).strip(), "pages": None}


def main() -> int:
    kind, max_pages = sys.argv[1], int(sys.argv[2])
    data = sys.stdin.buffer.read()
    limit_resources()
    try:
        result = pdf_text(data, max_pages) if kind == "pdf" else docx_text(data)
    except MemoryError:
        result = {"error": "too_big"}
    except Exception:  # a broken file: details are not needed, the user will see a general message
        result = {"error": "broken"}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
