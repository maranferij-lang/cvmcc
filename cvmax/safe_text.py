"""Safe display of text from the model.

The model reads the job posting and CV text, which someone else could have prepared. If an instruction is hidden there,
like "add the image https://...?data=<email>", or a phishing link, Markdown on the site would render them.
So all model text is shown as plain text: Markdown special characters are escaped.
"""

from __future__ import annotations

import re

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~$:])")
# Addresses that GitHub-flavored Markdown turns into links by itself even after escaping.
_URL = re.compile(r"(?:https?|ftp)://\S+|www\.\S+", re.IGNORECASE)


def md_escape(text: str | None) -> str:
    """The text is displayed verbatim: no links, images, HTML or formatting.

    Addresses are shown as code: they are visible but cannot be clicked.
    """
    text = text or ""
    out, pos = [], 0
    for match in _URL.finditer(text):
        out.append(_MD_SPECIAL.sub(r"\\\1", text[pos : match.start()]))
        out.append("`" + match.group().replace("`", "") + "`")
        pos = match.end()
    out.append(_MD_SPECIAL.sub(r"\\\1", text[pos:]))
    return "".join(out)
