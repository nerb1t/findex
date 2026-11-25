"""Статистика: Lab 1 (по корпусу) і Lab 4 (по готовому індексу)."""

from collections import Counter
from dataclasses import dataclass
from itertools import islice
from pathlib import Path

from findex.corpus import iter_documents
from findex.models import Index
from findex.tokenize import tokenize


def lazy_stats(root: Path, limit: int | None = None) -> tuple[int, Counter[str]]:
    """Lab 1: через генератори, в пам'яті тільки один документ."""
    n_docs = 0
    counts: Counter[str] = Counter()
    for doc in islice(iter_documents(root), limit):
        n_docs += 1
        counts.update(tokenize(doc.text))
    return n_docs, counts


def eager_stats(root: Path, limit: int | None = None) -> tuple[int, Counter[str]]:
    """Lab 1: спеціально погана версія - все в списки."""
    docs = list(islice(iter_documents(root), limit))
    all_tokens = [list(tokenize(doc.text)) for doc in docs]
    counts: Counter[str] = Counter()
    for tokens in all_tokens:
        counts.update(tokens)
    return len(docs), counts


@dataclass(frozen=True)
class IndexStats:
    documents: int
    books: int
    tokens: int
    vocabulary: int
    postings: int
    avg_doc_length: float
    top: list[tuple[str, int]]  # найчастіші терми (за сумою tf)


def index_stats(index: Index, top: int = 20) -> IndexStats:
    # частота терма в усьому корпусі = сума tf по всіх його postings
    freq = Counter({term: sum(p.tf for p in index[term]) for term in index})
    return IndexStats(
        documents=index.num_docs,
        books=len({m.path for m in index.doc_meta.values()}),
        tokens=sum(index.doc_lengths.values()),
        vocabulary=len(index),
        postings=index.num_postings(),
        avg_doc_length=index.avg_doc_length,
        top=freq.most_common(top),
    )
