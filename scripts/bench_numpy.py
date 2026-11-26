"""Lab 8, M2: скорер Lab 3 (цикл по Posting) проти NumPy на повному корпусі.

uv run python scripts/bench_numpy.py index_pos.bin

Міряємо Searcher.search цілком; булевий фільтр (match) закешований в обох,
тож різниця - це саме підрахунок балів і топ-k.
"""

import logging
import statistics
import sys
import time
from pathlib import Path

from findex.query import parse
from findex.scoring import BM25
from findex.search import Searcher
from findex.store import load

QUERIES = [
    ("common term", "the"),
    ("rare term", "leviathan"),
    ("very rare term", "1816"),
    ("3-term query", "the white whale"),
    ("3-term OR", "sea OR ship OR whale"),
]


def median_ms(fn: object, repeat: int) -> float:
    times = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()  # type: ignore[operator]
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def main() -> None:
    logging.disable(logging.CRITICAL)
    index = load(Path(sys.argv[1]))
    fast = Searcher(index)
    slow = Searcher(index, vectorized=False)
    t = time.perf_counter()
    _ = fast.vectors
    print(f"VectorPostings.from_index: {(time.perf_counter() - t) * 1000:.0f} ms (один раз)")
    print()
    print(
        "| Query | | Docs matched | Postings scanned | Lab 3 scorer, ms | NumPy scorer, ms | Speedup |"
    )
    print("|---|---|---:|---:|---:|---:|---:|")
    for name, q in QUERIES:
        tree = parse(q)
        matched = len(fast.match(tree))
        scanned = sum(index.df(t) for t in set(tree.terms()))
        a = fast.search(q, BM25(), 10)
        b = slow.search(q, BM25(), 10)
        assert a == b, q
        repeat = 20 if scanned > 10_000 else 200
        ms_slow = median_ms(lambda q=q: slow.search(q, BM25(), 10), repeat)
        ms_fast = median_ms(lambda q=q: fast.search(q, BM25(), 10), repeat)
        print(
            f"| {name} | `{q}` | {matched:,} | {scanned:,} | {ms_slow:.3f} | {ms_fast:.3f} | {ms_slow / ms_fast:.1f}× |"
        )


if __name__ == "__main__":
    main()
