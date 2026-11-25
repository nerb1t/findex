"""Ранжований пошук.

uv run python -m findex.search index_pos.bin 'whale AND (ship OR boat) NOT sea "white whale"'
uv run python -m findex.search index_pos.bin "white whale" "white whale" --scorer tfidf
uv run python -m findex.search index_pos.bin        (без запиту - інтерактивний режим)
"""

import argparse
import functools
import heapq
import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from findex.decorators import timed
from findex.query import Node, QueryError, parse
from findex.scoring import BM25, SCORERS
from findex.snippet import doc_text, make_snippet

log = logging.getLogger("findex.search")


@dataclass(frozen=True, order=True)
class SearchResult:
    # order=True порівнює поля по черзі, тому score перший:
    # sorted(results) - за балом, при однаковому балі - за doc_id
    score: float
    doc_id: int
    title: str


class Searcher:
    def __init__(self, index, engine="merge"):
        self.index = index
        self.engine = engine
        # lru_cache на дерево запиту -> doc_ids. Дерево - frozen dataclass,
        # тому хешується. Кеш свій для кожного Searcher (бо в кожного свій індекс).
        self.match = functools.lru_cache(maxsize=256)(self._match)

    def __repr__(self):
        return f"Searcher({self.index!r}, engine={self.engine!r})"

    @timed
    def _match(self, tree):
        # tuple, а не list: результат лежить у кеші, його не можна змінювати
        return tuple(tree.evaluate(self.index, self.engine))

    @timed
    def search(self, query, scorer=None, k=10):
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


def print_results(searcher, query, scorer, k, before, after):
    try:
        tree = parse(query)
        results = searcher.search(tree, scorer, k)
    except QueryError as e:
        print("query error:", e)
        return
    print(f"query: {tree}   scorer: {scorer!r}")
    if not results:
        print("  nothing found")
    terms = set(tree.terms())
    for rank, r in enumerate(results, 1):
        meta = searcher.index.doc_meta[r.doc_id]
        print(f"{rank:2}. [{r.score:6.2f}] {r.title}")
        print("    " + make_snippet(doc_text(meta), terms, before=before, after=after))
    print()


def main():
    from findex.store import open_index

    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("queries", nargs="*", help="без запитів - інтерактивний режим")
    parser.add_argument("--scorer", choices=list(SCORERS), default="bm25")
    parser.add_argument("--engine", choices=["merge", "set"], default="merge")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--color", action="store_true", help="підсвітка кольором (ANSI)")
    parser.add_argument("-q", "--quiet", action="store_true", help="без логів @timed")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO, format="  [%(name)s] %(message)s"
    )
    # щоб символи, яких немає в кодуванні консолі Windows, не валили програму
    sys.stdout.reconfigure(errors="replace")
    before, after = ("\033[1;33m", "\033[0m") if args.color else ("**", "**")
    scorer = SCORERS[args.scorer]()

    start = time.perf_counter()
    with open_index(args.index) as index:
        print(index, f"loaded in {time.perf_counter() - start:.2f} s")
        searcher = Searcher(index, args.engine)
        if args.queries:
            for q in args.queries:
                print_results(searcher, q, scorer, args.k, before, after)
        else:
            while True:
                try:
                    q = input("findex> ").strip()
                except EOFError:
                    break
                if not q:
                    break
                print_results(searcher, q, scorer, args.k, before, after)
        print(searcher.match.cache_info())


if __name__ == "__main__":
    main()
