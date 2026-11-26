"""Lab 8, M3: precision@5 для keyword / semantic / hybrid.

10 запитів з Lab 3 (scripts/qrels.json) + 5 перефразованих без слів з документів
(scripts/qrels_paraphrase.json).

uv run python scripts/evaluate_modes.py index_arr.bin            таблиця
uv run python scripts/evaluate_modes.py index_arr.bin --pool     нерозмічені з топ-5 (для розмітки)
"""

import argparse
import json
import logging
from pathlib import Path

from findex.scoring import BM25
from findex.search import Searcher
from findex.semantic import (
    MODES,
    FastEmbedder,
    SemanticIndex,
    default_embeddings_path,
    search_any,
    semantic_text,
)
from findex.snippet import doc_text, make_snippet
from findex.store import load
from findex.tokenize import tokenize

HERE = Path(__file__).parent


def doc_key(title: str) -> str:
    return title.split(":")[0]  # "melville-moby_dick.txt #38: ..." -> "...txt #38"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("--pool", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)

    original: dict[str, list[str]] = json.loads((HERE / "qrels.json").read_text(encoding="utf-8"))
    para: dict[str, list[str]] = json.loads(
        (HERE / "qrels_paraphrase.json").read_text(encoding="utf-8")
    )
    index = load(args.index)
    searcher = Searcher(index)
    sem = SemanticIndex.load(default_embeddings_path(args.index))
    sem.embedder = FastEmbedder(sem.model)

    def top5(query: str, mode: str) -> list[str]:
        try:
            res = search_any(searcher, sem, query, mode, BM25(), 5)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001  # напр. порожній булевий запит
            return []
        return [doc_key(r.title) for r in res]

    if args.pool:
        for query, relevant in {**original, **para}.items():
            print(f"=== {query}")
            seen: set[str] = set()
            for mode in MODES:
                try:
                    res = search_any(searcher, sem, query, mode, BM25(), 5)  # type: ignore[arg-type]
                except Exception:  # noqa: BLE001
                    res = []
                for r in res:
                    key = doc_key(r.title)
                    if key in relevant or key in seen:
                        continue
                    seen.add(key)
                    words = set(tokenize(semantic_text(query)))
                    print(f"- {key} [{mode}]")
                    print("    " + make_snippet(doc_text(index.doc_meta[r.doc_id]), words, 150))
        return

    print("| # | Запит | Тип | P@5 keyword | P@5 semantic | P@5 hybrid |")
    print("|---:|---|---|---:|---:|---:|")
    sums: dict[str, dict[str, float]] = {
        "orig": dict.fromkeys(MODES, 0.0),
        "para": dict.fromkeys(MODES, 0.0),
    }
    rows = [(q, rel, "orig") for q, rel in original.items()] + [
        (q, rel, "para") for q, rel in para.items()
    ]
    for i, (query, relevant, kind) in enumerate(rows, 1):
        rel = set(relevant)
        cells = []
        for mode in MODES:
            p5 = sum(1 for key in top5(query, mode) if key in rel) / 5
            sums[kind][mode] += p5
            cells.append(f"{p5:.1f}")
        label = "Lab 3" if kind == "orig" else "перефраз"
        print(f"| {i} | `{query}` | {label} | " + " | ".join(cells) + " |")
    for kind, n, label in (
        ("orig", len(original), "середнє, 10 запитів Lab 3"),
        ("para", len(para), "середнє, 5 перефразів"),
    ):
        print(
            f"| | **{label}** | | "
            + " | ".join(f"**{sums[kind][m] / n:.2f}**" for m in MODES)
            + " |"
        )
    allp = {m: (sums["orig"][m] + sums["para"][m]) / (len(original) + len(para)) for m in MODES}
    print("| | **усі 15** | | " + " | ".join(f"**{allp[m]:.2f}**" for m in MODES) + " |")


if __name__ == "__main__":
    main()
