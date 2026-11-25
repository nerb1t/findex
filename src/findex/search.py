"""Булевий пошук.

uv run python -m findex.search index.bin "whale ship"
uv run python -m findex.search index.bin "whale OR ship NOT sea" --engine set

Запит рахується зліва направо, без пріоритетів:
  "a b"        -> a AND b
  "a OR b c"   -> (a OR b) AND c
  "a NOT b"    -> a AND NOT b
  "NOT a"      -> всі документи без a
"""

import argparse
import time
import tracemalloc
from pathlib import Path

from findex.tokenize import tokenize

# ---------- merge (два вказівники) ----------
# Обидва списки відсортовані за doc_id. Йдемо по них одночасно,
# завжди рухаємо той вказівник, де число менше. O(len(a) + len(b)).


def merge_and(a, b):
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            i += 1
        else:
            j += 1
    return out


def merge_or(a, b):
    i = j = 0
    out = []
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            out.append(a[i])
            i += 1
            j += 1
        elif a[i] < b[j]:
            out.append(a[i])
            i += 1
        else:
            out.append(b[j])
            j += 1
    # хвіст, який залишився (тільки один з них не порожній)
    out.extend(a[i:])
    out.extend(b[j:])
    return out


def merge_not(a, b):
    """a AND NOT b: все з a, чого немає в b."""
    i = j = 0
    out = []
    while i < len(a):
        if j >= len(b) or a[i] < b[j]:
            out.append(a[i])
            i += 1
        elif a[i] == b[j]:
            i += 1
            j += 1
        else:
            j += 1
    return out


# ---------- через set ----------
# простіше, але треба побудувати хеш-таблицю і потім ще відсортувати результат


def set_and(a, b):
    return sorted(set(a) & set(b))


def set_or(a, b):
    return sorted(set(a) | set(b))


def set_not(a, b):
    return sorted(set(a) - set(b))


ENGINES = {
    "merge": (merge_and, merge_or, merge_not),
    "set": (set_and, set_or, set_not),
}


def parse_query(query):
    """Рядок -> список (оператор, терм). Терми проходять через той самий tokenize."""
    result = []
    op = "AND"
    for word in query.split():
        if word in ("AND", "OR", "NOT"):  # оператори тільки великими літерами
            op = word
            continue
        for term in tokenize(word):
            result.append((op, term))
            op = "AND"
    return result


def search(index, query, engine="merge"):
    and_, or_, not_ = ENGINES[engine]
    result = None
    for op, term in parse_query(query):
        ids = index.doc_ids(term)
        if result is None:
            if op == "NOT":
                result = not_(index.all_doc_ids(), ids)
            else:
                result = list(ids)
        elif op == "AND":
            result = and_(result, ids)
        elif op == "OR":
            result = or_(result, ids)
        else:
            result = not_(result, ids)
    return result if result is not None else []


def main():
    from findex.store import load

    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("query")
    parser.add_argument("--engine", choices=["merge", "set"], default="merge")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()

    tracemalloc.start()
    start = time.perf_counter()
    index = load(args.index)
    load_time = time.perf_counter() - start

    start = time.perf_counter()
    doc_ids = search(index, args.query, args.engine)
    search_time = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print(f"found: {len(doc_ids)} documents")
    for doc_id in doc_ids[: args.limit]:
        print(f"  [{doc_id}] {index.doc_meta[doc_id].title}")
    if len(doc_ids) > args.limit:
        print(f"  ... and {len(doc_ids) - args.limit} more")
    print(f"load: {load_time:.2f} s, search ({args.engine}): {search_time * 1000:.2f} ms")
    print(f"peak memory: {peak / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
