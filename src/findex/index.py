"""Інвертований індекс.

Чиста логіка (build_index_from_units) окремо від читання файлів (build_index),
щоб тести могли будувати індекс просто зі списку рядків.
"""

import re
from array import array
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from itertools import islice
from pathlib import Path
from typing import Literal, NamedTuple

from findex.corpus import Document, iter_documents
from findex.decorators import timed
from findex.models import (
    AnyPosting,
    ArrayPostings,
    DocId,
    DocMeta,
    Index,
    Kind,
    PlainPosting,
    Posting,
)
from findex.tokenize import tokenize

type Unit = Literal["para", "book"]

# абзаци розділені порожнім рядком (може бути з пробілами)
PARA_RE = re.compile(r"\n\s*\n")


class TextUnit(NamedTuple):
    """Один документ для індексу: звідки він і його текст."""

    path: str
    title: str
    text: str
    start: int
    end: int


def split_paragraphs(text: str) -> Iterator[tuple[int, int, int]]:
    """Генератор (k, start, end) - номер абзацу і його межі в тексті.

    Те саме, що PARA_RE.split(text), але ще знаємо де абзац лежить (для сніпетів).
    """
    start = 0
    k = 0
    for m in PARA_RE.finditer(text):
        yield k, start, m.start()
        start = m.end()
        k += 1
    yield k, start, len(text)


def iter_units(
    docs: Iterable[Document], unit: Unit = "para", part: int = 0, parts: int = 1
) -> Iterator[TextUnit]:
    """Книги -> документи для індексу.

    unit="book" - одна книга = один документ (як в Lab 1, але тоді документів лише 18).
    unit="para" - один абзац = один документ (так індекс схожий на справжній).
    part/parts - взяти тільки part-ту з parts частин абзаців книги (для паралельної
    збірки, Lab 5). Номери абзаців #k при цьому ті самі, що й без поділу.
    """
    for doc in docs:
        # абсолютний шлях: сніпети читають файл книги, а findex може бути запущений
        # з іншої папки. Один рядок на книгу, а не новий на кожен абзац
        path = str(doc.path.resolve())
        if unit == "book":
            yield TextUnit(path, doc.title or doc.path.stem, doc.text, 0, len(doc.text))
            continue
        paras = list(split_paragraphs(doc.text))
        lo = len(paras) * part // parts
        hi = len(paras) * (part + 1) // parts
        for k, start, end in paras[lo:hi]:
            para = doc.text[start:end]
            stripped = para.strip()
            if not stripped:
                continue
            # зсуваємо межі, щоб не було пробілів на краях
            start = start + (len(para) - len(para.lstrip()))
            end = start + len(stripped)
            first_line = stripped.splitlines()[0][:60]
            name = doc.title or doc.path.name  # сторінка з краулера - її <title>
            yield TextUnit(path, f"{name} #{k}: {first_line}", stripped, start, end)


def build_index_from_units(
    units: Iterable[TextUnit], kind: Kind = "slots", positions: bool = False
) -> Index:
    """Будує індекс за один прохід. Без файлів - тільки з готових текстів.

    Для кожного документа рахуємо Counter термів, а коли документ закінчився -
    скидаємо його в postings. doc_id ростуть, тому кожен список postings
    одразу відсортований, sort не потрібен.
    """
    if kind == "array" and positions:
        raise ValueError("positions are not supported for kind='array'")

    postings: defaultdict[str, list[AnyPosting]] = defaultdict(list)
    # два паралельні масиви: doc_ids і tfs, по 4 байти на число
    arrays: defaultdict[str, ArrayPostings] = defaultdict(lambda: (array("I"), array("I")))
    posting_cls = PlainPosting if kind == "plain" else Posting

    doc_lengths: dict[DocId, int] = {}
    doc_meta: dict[DocId, DocMeta] = {}

    for doc_id, u in enumerate(units):
        if positions:
            # терм -> список номерів токенів
            pos: defaultdict[str, list[int]] = defaultdict(list)
            n_tokens = 0
            for i, term in enumerate(tokenize(u.text)):
                pos[term].append(i)
                n_tokens = i + 1
            for term, lst in pos.items():
                postings[term].append(posting_cls(doc_id, len(lst), tuple(lst)))
        else:
            counts = Counter(tokenize(u.text))
            n_tokens = counts.total()
            for term, tf in counts.items():
                if kind == "array":
                    ids, tfs = arrays[term]
                    ids.append(doc_id)
                    tfs.append(tf)
                else:
                    postings[term].append(posting_cls(doc_id, tf))

        doc_lengths[doc_id] = n_tokens
        doc_meta[doc_id] = DocMeta(u.path, u.title, u.start, u.end)

    # звичайний dict: defaultdict з lambda не можна запікліти,
    # і щоб пошук неіснуючого терма не додавав порожні списки
    return Index(dict(postings), doc_lengths, doc_meta, kind, positions, dict(arrays))


def _notify(docs: Iterable[Document], on_book: Callable[[Path], None]) -> Iterator[Document]:
    # on_book викликається, коли книгу вже обробили (споживач попросив наступну)
    for doc in docs:
        yield doc
        on_book(doc.path)


@timed
def build_index(
    root: Path,
    unit: Unit = "para",
    kind: Kind = "slots",
    positions: bool = False,
    limit: int | None = None,
    on_book: Callable[[Path], None] | None = None,
) -> Index:
    """Читає корпус з диска і будує індекс. limit - скільки книг взяти."""
    docs: Iterable[Document] = islice(iter_documents(root), limit)
    if on_book is not None:
        docs = _notify(docs, on_book)
    return build_index_from_units(iter_units(docs, unit), kind, positions)
