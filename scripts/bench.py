"""Lab 1, M4: eager vs. lazy на одному корпусі - таблиця Markdown для README.

uv run python scripts/bench.py data/
uv run python scripts/bench.py data/ --limit 5
"""

import argparse
import logging
import time
import tracemalloc
from pathlib import Path

from findex.stats import eager_stats, lazy_stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    print("| Version | Documents | Tokens | Peak memory | Elapsed |")
    print("|---|---:|---:|---:|---:|")
    for name, fn in (("eager (lists)", eager_stats), ("lazy (generators)", lazy_stats)):
        tracemalloc.start()
        start = time.perf_counter()
        n_docs, counts = fn(args.root, args.limit)
        elapsed = time.perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peak_mb = peak / 1024 / 1024
        print(f"| {name} | {n_docs} | {counts.total():,} | {peak_mb:.1f} MB | {elapsed:.2f} s |")


if __name__ == "__main__":
    main()
