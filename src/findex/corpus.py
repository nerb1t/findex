import logging
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)


class Document(NamedTuple):
    doc_id: int
    path: Path
    text: str


def read_text(path: Path) -> str:
    """Читає файл як utf-8. Потрібна і для корпусу, і для сніпетів (щоб позиції збігались)."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # у деяких книгах є биті байти, щоб не падати - читаємо з заміною
        log.warning("bad utf-8 in %s, using errors='replace'", path)
        return path.read_text(encoding="utf-8", errors="replace")


def iter_documents(root: Path) -> Iterator[Document]:
    """Генератор: читає .txt файли по одному і віддає Document."""
    doc_id = 0
    for path in sorted(Path(root).rglob("*.txt")):
        yield Document(doc_id, path, read_text(path))
        doc_id += 1
