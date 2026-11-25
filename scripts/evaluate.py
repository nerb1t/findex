"""precision@5 для TF-IDF і BM25 на 10 запитах з ручною розміткою.

uv run python scripts/evaluate.py index_pos.bin          таблиця P@5
uv run python scripts/evaluate.py index_pos.bin --pool   топ-10 обох для розмітки
"""

import argparse
import json
import logging
from pathlib import Path

from findex.query import parse
from findex.scoring import BM25, TfIdf
from findex.search import Searcher
from findex.snippet import doc_text, make_snippet
from findex.store import open_index

QRELS = Path(__file__).with_name("qrels.json")


def doc_key(title):
    # "melville-moby_dick.txt #38: ..." -> "melville-moby_dick.txt #38"
    return title.split(":")[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("--pool", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)

    qrels = json.loads(QRELS.read_text(encoding="utf-8"))
    scorers = {"TF-IDF": TfIdf(), "BM25": BM25()}

    with open_index(args.index) as index:
        searcher = Searcher(index)

        if args.pool:
            # об'єднання топ-10 обох скорерів - це те, що треба розмітити руками
            for query in qrels:
                print(f"=== {query}")
                seen = {}
                for name, scorer in scorers.items():
                    for r in searcher.search(query, scorer, k=10):
                        seen.setdefault(r.doc_id, []).append(name)
                words = set(parse(query).terms())
                for d, names in seen.items():
                    meta = index.doc_meta[d]
                    print(f"- {doc_key(meta.title)} ({index.doc_length(d)} tok) {names}")
                    print("    " + make_snippet(doc_text(meta), words, width=150))
            return

        print("| # | Запит | Релевантних | P@5 TF-IDF | P@5 BM25 |")
        print("|---:|---|---:|---:|---:|")
        totals = dict.fromkeys(scorers, 0.0)
        for i, (query, relevant) in enumerate(qrels.items(), 1):
            relevant = set(relevant)
            row = []
            for name, scorer in scorers.items():
                top5 = [doc_key(r.title) for r in searcher.search(query, scorer, k=5)]
                p5 = sum(1 for key in top5 if key in relevant) / 5
                totals[name] += p5
                row.append(f"{p5:.1f}")
            print(f"| {i} | `{query}` | {len(relevant)} | {row[0]} | {row[1]} |")
        n = len(qrels)
        print(
            f"| | **середнє** | | **{totals['TF-IDF'] / n:.2f}** | **{totals['BM25'] / n:.2f}** |"
        )


if __name__ == "__main__":
    main()
