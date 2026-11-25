"""Пошук для веба: одна функція і для JSON API, і для HTML-сторінки."""

import math
import re
import time
from dataclasses import dataclass

from findex.query import parse
from findex.scoring import ScorerName, make_scorer
from findex.search import SearchResult
from findex.snippet import Parts, doc_text, snippet_parts
from findex.web.state import Engine


@dataclass(frozen=True)
class Hit:
    result: SearchResult
    parts: Parts
    source: str  # назва файлу книги (без шляху на сервері)

    @property
    def snippet(self) -> str:
        return "".join(s for s, _ in self.parts)


@dataclass(frozen=True)
class SearchPage:
    query: str
    scorer: ScorerName
    total: int
    page: int
    k: int
    took_ms: float
    hits: list[Hit]

    @property
    def pages(self) -> int:
        return max(1, math.ceil(self.total / self.k))


def source_name(path: str) -> str:
    # "C:\...\moby_dick.txt" -> "moby_dick.txt"; "...\crawl.jsonl#5" -> "crawl.jsonl"
    # вручну, бо індекс міг бути зібраний на іншій ОС (Windows-шлях на Linux)
    return re.split(r"[\\/]", path.split("#")[0])[-1]


def run_search(engine: Engine, q: str, k: int, scorer: ScorerName, page: int) -> SearchPage:
    """Робота CPU (парсер, BM25, сніпети). Кидає QueryError на поганий запит.

    Сторінка page = результати [(page-1)*k, page*k) з топу k*page.
    """
    start = time.perf_counter()
    tree = parse(q)
    total = len(engine.searcher.match(tree))  # закешовано в lru_cache, тож майже безкоштовно
    top = engine.searcher.search(tree, make_scorer(scorer), k * page)
    terms = set(tree.terms())
    hits: list[Hit] = []
    for r in top[(page - 1) * k :]:
        meta = engine.index.doc_meta[r.doc_id]
        hits.append(Hit(r, snippet_parts(doc_text(meta), terms), source_name(meta.path)))
    took = (time.perf_counter() - start) * 1000
    return SearchPage(str(tree), scorer, total, page, k, took, hits)
