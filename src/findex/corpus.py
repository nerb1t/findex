import logging
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)


class Document(NamedTuple):
    doc_id: int
    path: Path
    text: str


def iter_documents(root: Path) -> Iterator[Document]:
    """Генератор: читає .txt файли по одному і віддає Document."""
    doc_id = 0
    for path in sorted(Path(root).rglob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # у деяких книгах є биті байти, щоб не падати - читаємо з заміною
            log.warning("bad utf-8 in %s, using errors='replace'", path)
            text = path.read_text(encoding="utf-8", errors="replace")
        yield Document(doc_id, path, text)
        doc_id += 1
