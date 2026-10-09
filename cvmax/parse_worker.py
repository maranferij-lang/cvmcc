"""A separate process for reading the CV.

PDF and DOCX files can be "bombs": a small file that eats gigabytes of memory and minutes of CPU when parsed.
So parsing runs in a child process with a memory and time limit, and a hang does not take down the whole site.
Run: python -m cvmax.parse_worker pdf|docx < file  ->  JSON {"text": ..., "pages": ...} on stdout.
For PDF there is also "images": the number of embedded images (DOCX has no such field).
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


# Counting images is only a metric for the checks, so it must not take time away from text parsing:
# if this many CPU seconds have been spent since the PDF parse started, we skip the remaining pages (process limit is 8 s).
IMAGES_CPU_BUDGET_S = 3.0
# Forms (Form XObject) are traversed shallowly and with a bound, so nested or cyclic resources do not eat the time.
MAX_FORM_DEPTH = 2
MAX_XOBJECT_NODES = 200


def _resolve(obj):
    """Resolves a pypdf indirect reference; for plain values returns them unchanged."""
    return obj.get_object() if hasattr(obj, "get_object") else obj


def _dict_get(obj, key):
    obj = _resolve(obj)
    return _resolve(obj.get(key)) if hasattr(obj, "get") else None


def _xobject_images(resources, depth: int, nodes: list) -> int:
    """Counts /Subtype /Image entries in /Resources /XObject without decoding the images.

    nodes[0] is a counter of the remaining nodes, shared across the whole page.
    """
    xobjects = _dict_get(resources, "/XObject")
    if not hasattr(xobjects, "values"):
        return 0
    total = 0
    for ref in list(xobjects.values()):
        if nodes[0] <= 0:
            break
        nodes[0] -= 1
        obj = _resolve(ref)
        subtype = _dict_get(obj, "/Subtype")
        if subtype == "/Image":
            total += 1
        elif subtype == "/Form" and depth < MAX_FORM_DEPTH:
            total += _xobject_images(_dict_get(obj, "/Resources"), depth + 1, nodes)
    return total


def page_images(page) -> int:
    """Number of images on a page, without decoding them.

    len(page.images) in pypdf fully unpacks every inline image (BI/ID/EI), and a single page
    can eat the whole CPU limit. So we count XObject images from the resources dictionary, and inline ones from
    the INLINE IMAGE operator in the already parsed command stream (the image data is not unpacked).
    Inline images inside forms are not counted.
    """
    total = _xobject_images(_dict_get(page, "/Resources"), 0, [MAX_XOBJECT_NODES])
    try:
        contents = page.get_contents()
        if contents is not None:
            total += sum(1 for _, op in contents.operations if op == b"INLINE IMAGE")
    except Exception:  # corrupt command stream: the XObject images are already counted
        pass
    return total


def count_images(reader, pages: int, started: float | None = None) -> int:
    """How many images are on the PDF pages. An error on a page counts as 0 and does not break parsing.

    started: the time.process_time() value at the start of parsing; defaults to the moment of the call.
    """
    import time

    if started is None:
        started = time.process_time()
    total = 0
    for i in range(pages):
        if time.process_time() - started > IMAGES_CPU_BUDGET_S:
            break
        try:
            total += page_images(reader.pages[i])
        except Exception:  # corrupt XObject, nesting too deep, etc.
            continue
    return total


def pdf_text(data: bytes, max_pages: int) -> dict:
    import time

    started = time.process_time()
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = len(reader.pages)
    if pages > max_pages:
        return {"error": "pages", "pages": pages}
    text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    return {"text": text, "pages": pages, "images": count_images(reader, pages, started)}


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
