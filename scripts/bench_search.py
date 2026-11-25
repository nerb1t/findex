"""M2: merge vs set на найчастіших і найрідших термах.

uv run python scripts/bench_search.py index.bin
"""

import argparse
import timeit
from pathlib import Path

from findex.search import merge_and, merge_or, set_and, set_or
from findex.store import load


def bench(fn, a, b):
    # беремо найкращий з 5 замірів, кожен - кілька запусків
    timer = timeit.Timer(lambda: fn(a, b))
    number, _ = timer.autorange()
    return min(timer.repeat(5, number)) / number


def fmt(sec):
    if sec < 1e-3:
        return f"{sec * 1e6:.1f} µs"
    return f"{sec * 1e3:.2f} ms"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    args = parser.parse_args()

    index = load(args.index)
    # терми за кількістю документів (df)
    by_df = sorted(index.postings, key=lambda t: len(index.postings[t]))
    common = by_df[-2:]
    rare = by_df[:2]
    # рідкий терм з найбільшим doc_id - найгірший випадок для merge,
    # бо щоб до нього дійти, треба пройти майже весь довгий список
    once = [t for t in by_df if len(index.postings[t]) == 1]
    rare_last = max(once, key=lambda t: index.doc_ids(t)[0])
    cases = [
        ("2 найчастіші", common[1], common[0]),
        ("2 найрідші", rare[0], rare[1]),
        ("частий + рідкий (на початку)", common[1], rare[0]),
        ("частий + рідкий (в кінці)", common[1], rare_last),
    ]

    print("| Випадок | Терми (df) | AND merge | AND set | OR merge | OR set |")
    print("|---|---|---:|---:|---:|---:|")
    for name, t1, t2 in cases:
        a = index.doc_ids(t1)
        b = index.doc_ids(t2)
        terms = f"`{t1}` ({len(a)}), `{t2}` ({len(b)})"
        print(
            f"| {name} | {terms} | {fmt(bench(merge_and, a, b))} | {fmt(bench(set_and, a, b))}"
            f" | {fmt(bench(merge_or, a, b))} | {fmt(bench(set_or, a, b))} |"
        )


if __name__ == "__main__":
    main()
