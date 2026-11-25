"""Корпус: .txt книги (Lab 1) і .jsonl зі сторінками від краулера (Lab 6)."""

import json
import logging
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

CORPUS_SUFFIXES = (".txt", ".jsonl")


class Document(NamedTuple):
    doc_id: int
    path: Path
    text: str
    title: str = ""  # для сторінок з краулера - <title>; для книг порожній


def read_text(path: Path) -> str:
    """Читає файл як utf-8. Потрібна і для корпусу, і для сніпетів (щоб позиції збігались)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # у деяких книгах є биті байти, щоб не падати - читаємо з заміною
        log.warning("bad utf-8 in %s, using errors='replace'", path)
        return path.read_text(encoding="utf-8", errors="replace")


def corpus_files(root: Path) -> list[Path]:
    """Усі файли корпусу по порядку. root може бути папкою або одним файлом."""
    root = Path(root)
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*") if p.suffix in CORPUS_SUFFIXES and p.is_file())


def iter_jsonl(path: Path) -> Iterator[Document]:
    """Сторінки краулера: один JSON на рядок {"url", "title", "text", ...}.

    path кожного документа - "файл.jsonl#номер_рядка", щоб сніпет міг знайти текст.
    """
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if line.strip():
                page = json.loads(line)
                yield Document(0, Path(f"{path.resolve()}#{i}"), page["text"], page["title"])


def iter_file(path: Path) -> Iterator[Document]:
    if path.suffix == ".jsonl":
        yield from iter_jsonl(path)
    else:
        yield Document(0, path, read_text(path))


def iter_documents(root: Path) -> Iterator[Document]:
    """Генератор: читає файли по одному і віддає Document."""
    doc_id = 0
    for path in corpus_files(root):
        for doc in iter_file(path):
            yield doc._replace(doc_id=doc_id)
            doc_id += 1


@lru_cache(maxsize=4)
def _jsonl_texts(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line)["text"] if line.strip() else "" for line in f]


def read_source(path: str) -> str:
    """Текст документа за DocMeta.path: файл книги або "crawl.jsonl#N"."""
    base, sep, line = path.rpartition("#")
    if sep and base.endswith(".jsonl") and line.isdigit():
        return _jsonl_texts(base)[int(line)]
    return read_text(Path(path))
