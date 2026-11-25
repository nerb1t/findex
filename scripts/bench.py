"""M4: eager vs. lazy on the same corpus — prints a Markdown table for the README.

uv run python scripts/bench.py data/
uv run python scripts/bench.py data/ --limit 10
"""

import argparse
import logging
from pathlib import Path

from findex.stats import corpus_stats_eager, fmt_bytes, measure, run_lazy


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    rows = []
    for name, fn in (("eager (lists)", corpus_stats_eager), ("lazy (generators)", run_lazy)):
        stats, peak, elapsed = measure(fn, args.root, args.limit)
        rows.append((name, stats.documents, stats.tokens, fmt_bytes(peak), f"{elapsed:.2f} s"))

    print("| Version | Documents | Tokens | Peak memory | Elapsed |")
    print("|---|---:|---:|---:|---:|")
    for name, docs, toks, peak, elapsed in rows:
        print(f"| {name} | {docs:,} | {toks:,} | {peak} | {elapsed} |")


if __name__ == "__main__":
    main()
