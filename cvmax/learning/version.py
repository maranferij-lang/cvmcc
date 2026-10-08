"""Версія знань: хеш рубрик, уроків, ринку й варіантів промпту."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

_PATTERNS = (
    "rubrics/*.md",
    "rubrics/learned/*.md",
    "rubrics/market/*.md",
    "variants/*.json",
)


_LESSON_PATTERNS = ("rubrics/learned/*.md",)


@lru_cache(maxsize=8)
def knowledge_version(root: Path | None = None) -> str:
    """12 hex-символів sha256 над відсортованими (шлях, байти) файлів знань."""
    return _hash_files(root, _PATTERNS)


@lru_cache(maxsize=8)
def lessons_version(root: Path | None = None) -> str:
    """Те саме, але лише над rubrics/learned/*.md: для оцінки впливу самих уроків."""
    return _hash_files(root, _LESSON_PATTERNS)


def _hash_files(root: Path | None, patterns: tuple[str, ...]) -> str:
    base = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    files: set[Path] = set()
    for pattern in patterns:
        files.update(p for p in base.glob(pattern) if p.is_file())
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(base).as_posix()):
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:12]
