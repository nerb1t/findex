"""Пошук для веба: одна функція і для JSON API, і для HTML-сторінки."""

import math
import re
import time
from dataclasses import dataclass

from findex.query import parse
from findex.scoring import ScorerName, make_scorer
from findex.search import SearchResult
from findex.semantic import SearchMode, search_any, semantic_text
from findex.snippet import Parts, doc_text, snippet_parts
from findex.tokenize import tokenize
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
    mode: SearchMode
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


SEMANTIC_DEPTH = 100  # semantic/hybrid: показуємо не більше 100 найближчих


def run_search(
    engine: Engine,
    q: str,
    k: int,
    scorer: ScorerName,
    page: int,
    mode: SearchMode = "keyword",
) -> SearchPage:
    """Робота CPU (парсер, BM25, сніпети). Кидає QueryError на поганий запит.

    Сторінка page = результати [(page-1)*k, page*k) з топу k*page.
    """
    start = time.perf_counter()
    if mode == "keyword":
        tree = parse(q)
        total = len(engine.searcher.match(tree))  # закешовано в lru_cache, майже безкоштовно
        top = engine.searcher.search(tree, make_scorer(scorer), k * page)
        terms, shown = set(tree.terms()), str(tree)
    else:
        found = search_any(
            engine.searcher, engine.semantic, q, mode, make_scorer(scorer), SEMANTIC_DEPTH
        )
        total, top = len(found), found[: k * page]
        shown = semantic_text(q)
        terms = set(tokenize(shown))
    hits: list[Hit] = []
    for r in top[(page - 1) * k :]:
        meta = engine.index.doc_meta[r.doc_id]
        hits.append(Hit(r, snippet_parts(doc_text(meta), terms), source_name(meta.path)))
    took = (time.perf_counter() - start) * 1000
    return SearchPage(shown, mode, scorer, total, page, k, took, hits)
