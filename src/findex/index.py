"""Інвертований індекс.

uv run python -m findex.index data/ --out index.bin
uv run python -m findex.index data/ --out index_pos.bin --positions   (для фраз)
uv run python -m findex.index data/ --out index.json
"""

import argparse
import logging
import re
import time
import tracemalloc
from array import array
from collections import Counter, defaultdict
from pathlib import Path

from findex.corpus import iter_documents
from findex.decorators import timed
from findex.models import DocMeta, Index, PlainPosting, Posting
from findex.tokenize import tokenize

# абзаци розділені порожнім рядком (може бути з пробілами)
PARA_RE = re.compile(r"\n\s*\n")


def split_paragraphs(text):
    """Генератор (k, start, end) - номер абзацу і його межі в тексті.

    Те саме, що PARA_RE.split(text), але ще знаємо де абзац лежить (для сніпетів).
    """
    start = 0
    k = 0
    for m in PARA_RE.finditer(text):
        yield k, start, m.start()
        start = m.end()
        k += 1
    yield k, start, len(text)


def iter_units(root, unit="para"):
    """Генератор (path, title, text, start, end).

    unit="book" - одна книга = один документ (як в Lab 1, але тоді документів лише 18).
    unit="para" - один абзац = один документ (так індекс схожий на справжній).
    """
    for doc in iter_documents(root):
        path = str(doc.path)  # один рядок на книгу, а не новий на кожен абзац
        if unit == "book":
            yield path, doc.path.stem, doc.text, 0, len(doc.text)
            continue
        for k, start, end in split_paragraphs(doc.text):
            para = doc.text[start:end]
            stripped = para.strip()
            if not stripped:
                continue
            # зсуваємо межі, щоб не було пробілів на краях
            start = start + (len(para) - len(para.lstrip()))
            end = start + len(stripped)
            first_line = stripped.splitlines()[0][:60]
            yield path, f"{doc.path.name} #{k}: {first_line}", stripped, start, end


@timed
def build_index(root, unit="para", kind="slots", positions=False):
    """Будує індекс за один прохід по корпусу.

    Для кожного документа рахуємо Counter термів, а коли документ закінчився -
    скидаємо його в postings. doc_id ростуть, тому кожен список postings
    одразу відсортований, sort не потрібен.
    """
    if kind not in ("plain", "slots", "array"):
        raise ValueError(f"unknown kind: {kind}")
    if kind == "array" and positions:
        raise ValueError("positions are not supported for kind='array'")

    if kind == "array":
        # два паралельні масиви: doc_ids і tfs, по 4 байти на число
        postings = defaultdict(lambda: (array("I"), array("I")))
    else:
        postings = defaultdict(list)
    posting_cls = PlainPosting if kind == "plain" else Posting

    doc_lengths = {}
    doc_meta = {}

    for doc_id, (path, title, text, start, end) in enumerate(iter_units(root, unit)):
        if positions:
            # терм -> список номерів токенів
            pos = defaultdict(list)
            n_tokens = 0
            for i, term in enumerate(tokenize(text)):
                pos[term].append(i)
                n_tokens = i + 1
            for term, lst in pos.items():
                postings[term].append(posting_cls(doc_id, len(lst), tuple(lst)))
        else:
            counts = Counter(tokenize(text))
            n_tokens = counts.total()
            for term, tf in counts.items():
                if kind == "array":
                    ids, tfs = postings[term]
                    ids.append(doc_id)
                    tfs.append(tf)
                else:
                    postings[term].append(posting_cls(doc_id, tf))

        doc_lengths[doc_id] = n_tokens
        doc_meta[doc_id] = DocMeta(path, title, start, end)

    # звичайний dict: defaultdict з lambda не можна запікліти,
    # і щоб пошук неіснуючого терма не додавав порожні списки
    return Index(dict(postings), doc_lengths, doc_meta, kind, positions)


def main():
    from findex.store import save  # тут, щоб не було циклічного імпорту

    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--out", type=Path, default=Path("index.bin"))
    parser.add_argument("--unit", choices=["para", "book"], default="para")
    parser.add_argument("--kind", choices=["plain", "slots", "array"], default="slots")
    parser.add_argument("--positions", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(message)s")
    logging.getLogger("findex.corpus").setLevel(logging.ERROR)

    tracemalloc.start()
    start = time.perf_counter()
    index = build_index(args.root, args.unit, args.kind, args.positions)
    build_time = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    start = time.perf_counter()
    save(index, args.out)
    save_time = time.perf_counter() - start

    print(index)
    print("postings:", index.num_postings())
    print(f"build: {build_time:.2f} s (з tracemalloc)")
    print(f"peak memory (build): {peak / 1024 / 1024:.1f} MB")
    print(f"save: {save_time:.2f} s -> {args.out} ({args.out.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
