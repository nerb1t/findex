"""Три перевірки ранжування на справжньому корпусі.

uv run python scripts/sanity.py index_pos.bin
"""

import argparse
import logging
from pathlib import Path

from findex.models import Posting
from findex.scoring import BM25, TfIdf
from findex.search import Searcher
from findex.store import open_index


def check_rare_vs_common(index, searcher):
    print("## 1. Рідкий терм vs частий: запит `the OR leviathan`")
    print()
    for name in ("the", "leviathan"):
        print(f"- df(`{name}`) = {index.df(name)}, idf BM25 = {BM25().idf(name, index):.2f}")
    print()
    results = searcher.search("the OR leviathan", BM25(), k=100_000)
    only_the = [r for r in results if r.doc_id not in set(index.doc_ids("leviathan"))]
    only_lev = [r for r in results if r.doc_id not in set(index.doc_ids("the"))]
    print("| | Найкращий документ | BM25 | Місце |")
    print("|---|---|---:|---:|")
    for label, group in (("тільки `leviathan`", only_lev), ("тільки `the`", only_the)):
        best = group[0]
        rank = results.index(best) + 1
        print(f"| {label} | {best.title} | {best.score:.2f} | {rank} |")
    worst_lev = only_lev[-1]
    print(
        f"\nНайгірший документ з тільки `leviathan`: {worst_lev.score:.2f} — "
        f"все одно вище за найкращий з тільки `the` ({only_the[0].score:.2f})."
    )
    print()


def check_saturation(index):
    print("## 2. Насичення tf: терм `whale`, документ середньої довжини")
    print()
    avg = index.avg_doc_length
    # шукаємо документ з довжиною, найближчою до середньої
    doc = min(index.doc_lengths, key=lambda d: abs(index.doc_lengths[d] - avg))
    print(f"avg_doc_length = {avg:.1f}, документ {doc} має довжину {index.doc_length(doc)}")
    print()
    bm, ti = BM25(), TfIdf()
    print("| tf | BM25 | +за цей повтор | TF-IDF | +за цей повтор |")
    print("|---:|---:|---:|---:|---:|")
    prev_bm = prev_ti = 0.0
    for tf in range(1, 21):
        b = bm("whale", Posting(doc, tf), index)
        t = ti("whale", Posting(doc, tf), index)
        if tf in (1, 2, 3, 5, 10, 19, 20):
            print(f"| {tf} | {b:.3f} | {b - prev_bm:+.3f} | {t:.2f} | {t - prev_ti:+.2f} |")
        prev_bm, prev_ti = b, t

    # і реальний документ, де whale зустрічається найбільше разів
    p = max(index["whale"], key=lambda p: p.tf)
    meta = index.doc_meta[p.doc_id]
    print(f"\nНа корпусі: найбільше `whale` у `{meta.title}` — tf = {p.tf}.")
    print()


def check_length(index):
    print("## 3. Короткий документ vs довгий, обидва з одним `whale`")
    print()
    ones = [p for p in index["whale"] if p.tf == 1]
    short = min(ones, key=lambda p: index.doc_length(p.doc_id))
    long = max(ones, key=lambda p: index.doc_length(p.doc_id))
    print("| Документ | Довжина | tf | BM25 | TF-IDF |")
    print("|---|---:|---:|---:|---:|")
    for p in (short, long):
        print(
            f"| {index.doc_meta[p.doc_id].title} | {index.doc_length(p.doc_id)} | {p.tf}"
            f" | {BM25()('whale', p, index):.2f} | {TfIdf()('whale', p, index):.2f} |"
        )
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    with open_index(args.index) as index:
        searcher = Searcher(index)
        check_rare_vs_common(index, searcher)
        check_saturation(index)
        check_length(index)


if __name__ == "__main__":
    main()
