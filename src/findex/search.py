"""Ранжований пошук (тільки логіка, без виводу - вивід у cli.py)."""

import functools
import heapq
import logging
from dataclasses import dataclass

from findex.decorators import timed
from findex.merge import Engine
from findex.models import DocId, Index
from findex.query import Node, parse
from findex.scoring import BM25, Scorer

log = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class SearchResult:
    # order=True порівнює поля по черзі, тому score перший:
    # sorted(results) - за балом, при однаковому балі - за doc_id
    score: float
    doc_id: DocId
    title: str


class Searcher:
    def __init__(self, index: Index, engine: Engine = "merge") -> None:
        self.index = index
        self.engine: Engine = engine
        # lru_cache на дерево запиту -> doc_ids. Дерево - frozen dataclass,
        # тому хешується. Кеш свій для кожного Searcher (бо в кожного свій індекс).
        self.match = functools.lru_cache(maxsize=256)(self._match)

    def __repr__(self) -> str:
        return f"Searcher({self.index!r}, engine={self.engine!r})"

    @timed
    def _match(self, tree: Node) -> tuple[DocId, ...]:
        # tuple, а не list: результат лежить у кеші, його не можна змінювати
        return tuple(tree.evaluate(self.index, self.engine))

    @timed
    def search(
        self, query: str | Node, scorer: Scorer | None = None, k: int = 10
    ) -> list[SearchResult]:
        """Повертає топ-k SearchResult. query - рядок або вже готове дерево (Node)."""
        if scorer is None:
            scorer = BM25()
        tree = query if isinstance(query, Node) else parse(query)

        hits = self.match.cache_info().hits
        ids = self.match(tree)
        hit = self.match.cache_info().hits > hits
        log.info("match %s -> %d docs (%s)", tree, len(ids), "cache hit" if hit else "cache miss")

        # бали рахуємо тільки для документів, які пройшли булевий фільтр
        scores = dict.fromkeys(ids, 0.0)
        for term in set(tree.terms()):
            if term not in self.index:
                continue
            for p in self.index[term]:
                if p.doc_id in scores:
                    scores[p.doc_id] += scorer(term, p, self.index)

        # топ-k без повного сортування: O(n log k).
        # при однаковому балі вище менший doc_id (щоб результат був стабільний)
        top = heapq.nlargest(k, scores.items(), key=lambda x: (x[1], -x[0]))
        return [SearchResult(score, d, self.index.doc_meta[d].title) for d, score in top]
