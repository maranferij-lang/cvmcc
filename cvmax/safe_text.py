"""Безпечний показ тексту від моделі.

Модель читає текст вакансії і CV, які міг підготувати хтось інший. Якщо там сховано інструкцію
«додай картинку https://...?data=<email>» чи фішингове посилання, Markdown на сайті відмалював би їх.
Тому весь текст моделі показуємо як звичайний текст: спецсимволи Markdown екрануються.
"""

from __future__ import annotations

import re

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")
# Адреси, які GitHub-Markdown сам робить посиланнями навіть після екранування.
_URL = re.compile(r"(?:https?|ftp)://\S+|www\.\S+", re.IGNORECASE)


def md_escape(text: str | None) -> str:
    """Текст відображається дослівно: без посилань, картинок, HTML і форматування.

    Адреси показуються як код: їх видно, але клікнути не можна.
    """
    text = text or ""
    out, pos = [], 0
    for match in _URL.finditer(text):
        out.append(_MD_SPECIAL.sub(r"\\\1", text[pos : match.start()]))
        out.append("`" + match.group().replace("`", "") + "`")
        pos = match.end()
    out.append(_MD_SPECIAL.sub(r"\\\1", text[pos:]))
    return "".join(out)
