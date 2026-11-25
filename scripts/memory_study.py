"""M4: три варіанти зберігання postings на одному корпусі.

uv run python scripts/memory_study.py data/
"""

import argparse
import gc
import logging
import os
import tempfile
import time
import tracemalloc
from pathlib import Path

from findex.index import build_index
from findex.store import load, save

MB = 1024 * 1024


def measure(root, kind, positions=False):
    gc.collect()
    tracemalloc.start()
    start = time.perf_counter()
    index = build_index(root, kind=kind, positions=positions)
    build_time = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()  # current = скільки займає сам індекс
    tracemalloc.stop()

    row = {
        "kind": kind + (" + positions" if positions else ""),
        "peak": peak / MB,
        "in_ram": current / MB,
        "build": build_time,
        "postings": index.num_postings(),
    }

    tmp = Path(tempfile.mkdtemp())
    for ext in (".bin", ".json"):
        path = tmp / ("index" + ext)
        start = time.perf_counter()
        save(index, path)
        row["save" + ext] = time.perf_counter() - start
        row["size" + ext] = path.stat().st_size / MB

    # Спочатку видаляємо індекс з пам'яті. Якщо він живий, то під час load
    # збирач сміття (gc) щоразу обходить і старі мільйони об'єктів - load виходить у 2 рази довшим.
    del index
    gc.collect()

    for ext in (".bin", ".json"):
        path = tmp / ("index" + ext)
        # load міряємо БЕЗ tracemalloc, бо він сповільнює в ~3 рази
        gc.collect()
        start = time.perf_counter()
        loaded = load(path)
        row["load" + ext] = time.perf_counter() - start
        del loaded
        os.remove(path)
    os.rmdir(tmp)
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)

    rows = [
        measure(args.root, "plain"),
        measure(args.root, "slots"),
        measure(args.root, "array"),
        measure(args.root, "slots", positions=True),
    ]

    print(f"postings: {rows[0]['postings']:,}")
    print()
    print(
        "| Postings | Peak (build) | Index in RAM | Build | pickle size | pickle save |"
        " pickle load | json size | json save | json load |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(
            f"| {r['kind']} | {r['peak']:.1f} MB | {r['in_ram']:.1f} MB | {r['build']:.2f} s"
            f" | {r['size.bin']:.1f} MB | {r['save.bin']:.2f} s | {r['load.bin']:.2f} s"
            f" | {r['size.json']:.1f} MB | {r['save.json']:.2f} s | {r['load.json']:.2f} s |"
        )


if __name__ == "__main__":
    main()
