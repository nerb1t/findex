import argparse
import logging
import time
import tracemalloc
from collections import Counter
from itertools import islice
from pathlib import Path

from findex.corpus import iter_documents
from findex.tokenize import tokenize


def lazy_stats(root, limit=None):
    docs = islice(iter_documents(root), limit)
    n_docs = 0
    counts = Counter()
    for doc in docs:
        n_docs += 1
        counts.update(tokenize(doc.text))
    return n_docs, counts


def eager_stats(root, limit=None):
    # спеціально погана версія: все в списки
    docs = list(islice(iter_documents(root), limit))
    all_tokens = [list(tokenize(doc.text)) for doc in docs]
    counts = Counter()
    for tokens in all_tokens:
        counts.update(tokens)
    return len(docs), counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--eager", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    tracemalloc.start()
    start = time.perf_counter()
    if args.eager:
        n_docs, counts = eager_stats(args.root, args.limit)
    else:
        n_docs, counts = lazy_stats(args.root, args.limit)
    elapsed = time.perf_counter() - start
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("version:", "eager" if args.eager else "lazy")
    print("documents:", n_docs)
    print("tokens:", counts.total())
    print("vocabulary:", len(counts))
    print(f"elapsed: {elapsed:.2f} s")
    print(f"peak memory: {peak / 1024 / 1024:.1f} MB")
    print()
    print("top 50:")
    for word, count in counts.most_common(50):
        print(f"  {word:<15} {count}")


if __name__ == "__main__":
    main()
