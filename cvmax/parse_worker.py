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


# Підрахунок картинок це лише метрика для перевірок, тому він не має права відібрати час у розбору тексту:
# якщо від початку розбору PDF уже витрачено стільки секунд CPU, решту сторінок не чіпаємо (ліміт процесу 8 с).
IMAGES_CPU_BUDGET_S = 3.0
# Форми (Form XObject) обходимо неглибоко й обмежено, щоб вкладені чи циклічні ресурси не з'їли час.
MAX_FORM_DEPTH = 2
MAX_XOBJECT_NODES = 200


def _resolve(obj):
    """Розкриває непряме посилання pypdf; для звичайних значень повертає їх самих."""
    return obj.get_object() if hasattr(obj, "get_object") else obj


def _dict_get(obj, key):
    obj = _resolve(obj)
    return _resolve(obj.get(key)) if hasattr(obj, "get") else None


def _xobject_images(resources, depth: int, nodes: list) -> int:
    """Рахує записи /Subtype /Image у /Resources /XObject без декодування зображень.

    nodes[0] це спільний лічильник вузлів, що лишилися, на всю сторінку.
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
    """Кількість зображень на сторінці без їх декодування.

    len(page.images) у pypdf повністю розпаковує кожне inline-зображення (BI/ID/EI), і одна сторінка
    може з'їсти весь ліміт CPU. Тому рахуємо XObject-зображення за словником ресурсів, а inline за
    оператором INLINE IMAGE у вже розібраному потоці команд (дані зображення не розпаковуються).
    Inline-зображення всередині форм не рахуємо.
    """
    total = _xobject_images(_dict_get(page, "/Resources"), 0, [MAX_XOBJECT_NODES])
    try:
        contents = page.get_contents()
        if contents is not None:
            total += sum(1 for _, op in contents.operations if op == b"INLINE IMAGE")
    except Exception:  # зіпсований потік команд: XObject-картинки вже пораховано
        pass
    return total


def count_images(reader, pages: int, started: float | None = None) -> int:
    """Скільки зображень на сторінках PDF. Помилка на сторінці рахується як 0 і не ламає розбір.

    started: значення time.process_time() на початку розбору; за замовчуванням момент виклику.
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
        except Exception:  # зіпсований XObject, надто глибока вкладеність тощо
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
