"""Ранжування: TF-IDF і BM25.

Scorer - це typing.Protocol: будь-який об'єкт, який можна викликати як
scorer(term, posting, index) -> float. Наслідуватись від Scorer НЕ треба:
pyright сам перевіряє, що в TfIdf і BM25 є __call__ з такою сигнатурою
(структурна типізація, "duck typing" для type checker).
"""

import math
from dataclasses import dataclass
from typing import Final, Literal, Protocol

from findex.models import AnyPosting, Index

type ScorerName = Literal["bm25", "tfidf"]


class Scorer(Protocol):
    def __call__(self, term: str, posting: AnyPosting, index: Index) -> float: ...


@dataclass(frozen=True)
class TfIdf:
    """score = tf * idf, idf = log(N / df)."""

    def idf(self, term: str, index: Index) -> float:
        return math.log(index.num_docs / index.df(term))

    def __call__(self, term: str, posting: AnyPosting, index: Index) -> float:
        return posting.tf * self.idf(term, index)


@dataclass(frozen=True)
class BM25:
    """BM25 (як у Lucene / Elasticsearch).

    k1 - насичення tf: чим менше k1, тим швидше повтори перестають щось давати.
    b  - нормалізація за довжиною: b=0 довжина не важлива, b=1 повна нормалізація.
    """

    k1: float = 1.5
    b: float = 0.75

    def idf(self, term: str, index: Index) -> float:
        n = index.num_docs
        df = index.df(term)
        # +1 всередині log, щоб idf не був від'ємним для дуже частих слів
        return math.log(1 + (n - df + 0.5) / (df + 0.5))

    def __call__(self, term: str, posting: AnyPosting, index: Index) -> float:
        tf = posting.tf
        # довгий документ -> norm > 1 -> менший бал
        norm = 1 - self.b + self.b * index.doc_length(posting.doc_id) / index.avg_doc_length
        return self.idf(term, index) * tf * (self.k1 + 1) / (tf + self.k1 * norm)


def make_scorer(name: ScorerName) -> Scorer:
    # тут pyright і перевіряє, що TfIdf()/BM25() підходять під Scorer
    if name == "tfidf":
        return TfIdf()
    return BM25()


SCORER_NAMES: Final[tuple[ScorerName, ...]] = ("bm25", "tfidf")
