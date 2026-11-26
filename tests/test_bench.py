"""Lab 8: бенчмарки (pytest-benchmark) для build_index і search.

Звичайний pytest їх запускає один раз, тільки як тести (див. --benchmark-disable
у pyproject.toml). Справжній замір: CI-крок або локально
    uv run pytest tests/test_bench.py --benchmark-enable --benchmark-only

Стелі часу дуже щедрі (~10x від виміряного на i7-11700K): мета - впіймати
грубу регресію (наприклад, випадковий O(n^2)), а не шум між машинами.
"""

import random
from pathlib import Path
from typing import cast

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from findex.index import build_index
from findex.models import Index
from findex.scoring import BM25
from findex.search import Searcher, SearchResult

# стелі медіани, секунди
MAX_BUILD_S = 1.5
MAX_SEARCH_S = 0.02

# pytest-benchmark не має анотацій типів: benchmark(...) повертає Unknown,
# тому cast - обіцянка pyright, що повертається те саме, що повертає функція


@pytest.fixture(scope="module")
def bench_corpus(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Синтетичний корпус: 2 "книги" по 1500 абзаців, слова з ймовірністю за Ципфом
    (кілька дуже частих слів і довгий хвіст рідких - як у справжньому тексті)."""
    rnd = random.Random(42)
    vocab = ["the", "and", "whale", "white", "ship", "sea"] + [f"w{i}" for i in range(3000)]
    weights = [1 / (rank + 1) for rank in range(len(vocab))]
    root = tmp_path_factory.mktemp("bench")
    for book in ("a", "b"):
        paras = [" ".join(rnd.choices(vocab, weights, k=rnd.randint(10, 80))) for _ in range(1500)]
        (root / f"{book}.txt").write_text("\n\n".join(paras), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def bench_index(bench_corpus: Path) -> Index:
    return build_index(bench_corpus, positions=True)


def median_s(benchmark: BenchmarkFixture) -> float | None:
    if benchmark.disabled or benchmark.stats is None:
        return None
    # getattr: тип атрибута в pytest-benchmark невідомий (Unknown), а getattr дає Any
    return float(getattr(benchmark.stats.stats, "median"))  # noqa: B009


def test_bench_build_index(benchmark: BenchmarkFixture, bench_corpus: Path) -> None:
    index = cast(Index, benchmark(build_index, bench_corpus, positions=True))
    assert index.num_docs == 3000
    m = median_s(benchmark)
    assert m is None or m < MAX_BUILD_S, f"build_index regressed: {m:.3f} s"


def test_bench_search_3_terms(benchmark: BenchmarkFixture, bench_index: Index) -> None:
    searcher = Searcher(bench_index)
    results = cast(list[SearchResult], benchmark(searcher.search, "the white whale", BM25(), 10))
    assert len(results) == 10
    m = median_s(benchmark)
    assert m is None or m < MAX_SEARCH_S, f"search regressed: {m * 1000:.1f} ms"


def test_bench_search_common_term(benchmark: BenchmarkFixture, bench_index: Index) -> None:
    searcher = Searcher(bench_index)
    results = cast(list[SearchResult], benchmark(searcher.search, "the", BM25(), 10))
    assert len(results) == 10
    m = median_s(benchmark)
    assert m is None or m < MAX_SEARCH_S, f"search regressed: {m * 1000:.1f} ms"
