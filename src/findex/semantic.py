"""Lab 8, M3: семантичний пошук (ембединги) і гібридний (BM25 + семантика через RRF).

Ембединг - вектор з 384 чисел, який модель all-MiniLM-L6-v2 видає для тексту так,
що схожі за змістом тексти лежать поруч, навіть якщо в них немає спільних слів.
Усі вектори нормовані (довжина 1), тому косинусна близькість = скалярний добуток,
а пошук по всіх фрагментах - одне множення матриці на вектор: E @ q.
"""

import logging
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from findex.models import DocId, Index
from findex.query import QueryError
from findex.scoring import Scorer
from findex.search import Searcher, SearchResult
from findex.snippet import doc_text

log = logging.getLogger(__name__)

type FloatMatrix = NDArray[np.float32]
type SearchMode = Literal["keyword", "semantic", "hybrid"]
MODES: Final[tuple[SearchMode, ...]] = ("keyword", "semantic", "hybrid")

DEFAULT_MODEL: Final = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_WORDS: Final = 200  # модель однаково бачить лише перші ~256 токенів
RRF_K: Final = 60
FUSE_DEPTH: Final = 100  # скільки результатів кожного рейтингу йде в злиття


class Embedder(Protocol):
    """Будь-що, що перетворює список текстів на матрицю float32[n, d]."""

    def __call__(self, texts: Sequence[str]) -> FloatMatrix: ...


class FastEmbedder:
    """all-MiniLM-L6-v2 через fastembed (ONNX Runtime, без PyTorch: ~190 MB RAM, а не ~1 GB)."""

    def __init__(self, model: str = DEFAULT_MODEL, cache_dir: str | None = None) -> None:
        from fastembed import TextEmbedding  # тут, щоб без моделі findex імпортувався швидко

        self.name = model
        self._model = TextEmbedding(model, cache_dir=cache_dir)

    def __call__(self, texts: Sequence[str]) -> FloatMatrix:
        vectors = list(self._model.embed(list(texts), batch_size=64))
        return np.asarray(vectors, dtype=np.float32).reshape(len(texts), -1)


def normalize(m: FloatMatrix) -> FloatMatrix:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    return (m / np.maximum(norms, 1e-12)).astype(np.float32)


def chunk_text(text: str, words: int = CHUNK_WORDS) -> list[str]:
    """Документ -> фрагменти до `words` слів. Абзац корпусу зазвичай один фрагмент."""
    tokens = text.split()
    if not tokens:
        return []
    return [" ".join(tokens[i : i + words]) for i in range(0, len(tokens), words)]


def build_chunks(index: Index) -> tuple[list[str], NDArray[np.int32]]:
    texts: list[str] = []
    owner: list[int] = []
    for d, meta in index.doc_meta.items():
        for chunk in chunk_text(doc_text(meta)):
            texts.append(chunk)
            owner.append(d)
    return texts, np.asarray(owner, dtype=np.int32)


def embed_index(
    index: Index,
    embedder: Embedder,
    batch: int = 512,
    on_progress: Callable[[int], None] | None = None,
) -> tuple[FloatMatrix, NDArray[np.int32]]:
    """Ембединги всіх фрагментів. Сортуємо за довжиною: у пакеті всі тексти
    доповнюються до найдовшого, тож схожі довжини разом - у 3 рази швидше."""
    texts, owner = build_chunks(index)
    order = sorted(range(len(texts)), key=lambda i: len(texts[i]))
    out: FloatMatrix | None = None
    for start in range(0, len(order), batch):
        ids = order[start : start + batch]
        vecs = normalize(embedder([texts[i] for i in ids]))
        if out is None:
            out = np.zeros((len(texts), vecs.shape[1]), np.float32)
        out[ids] = vecs
        if on_progress is not None:
            on_progress(len(ids))
    if out is None:
        out = np.zeros((0, 384), np.float32)
    return out, owner


# ---------- файл з ембедингами ----------
# int8 замість float32: у 4 рази менше (18 MB замість 73 MB на корпусі), а для
# косинуса різниця в 4-му знаку. Кожен рядок: q = round(v / s * 127), s = max|v|.


def default_embeddings_path(index_path: Path) -> Path:
    return index_path.with_suffix(".emb.npz")


def save_embeddings(path: Path, e: FloatMatrix, owner: NDArray[np.int32], model: str) -> None:
    scale = np.maximum(np.abs(e).max(axis=1), 1e-12).astype(np.float32)
    q = np.round(e / scale[:, None] * 127).astype(np.int8)
    np.savez(path, q=q, scale=scale, owner=owner, model=np.array(model))


@dataclass
class SemanticIndex:
    vectors: FloatMatrix  # float32[chunks, d], нормовані
    owner: NDArray[np.int32]  # chunk -> doc_id (зростає)
    model: str
    embedder: Embedder | None = None

    @classmethod
    def load(cls, path: Path, embedder: Embedder | None = None) -> "SemanticIndex":
        with np.load(path) as data:
            q = data["q"].astype(np.float32)
            scale = data["scale"].astype(np.float32)
            owner = data["owner"].astype(np.int32)
            model = str(data["model"])
        return cls(normalize(q * scale[:, None] / 127), owner, model, embedder)

    def __post_init__(self) -> None:
        # фрагменти одного документа йдуть підряд: межі груп для reduceat
        if len(self.owner):
            change = np.flatnonzero(self.owner[1:] != self.owner[:-1]) + 1
            self._starts = np.concatenate(([0], change)).astype(np.intp)
        else:
            self._starts = np.zeros(0, np.intp)
        self._docs = self.owner[self._starts]

    def search(self, text: str, k: int) -> list[tuple[DocId, float]]:
        """Топ-k документів за найкращим фрагментом."""
        if self.embedder is None:
            raise RuntimeError("no embedding model loaded")
        if len(self.owner) == 0 or k <= 0:
            return []
        q = normalize(self.embedder([text]))[0]
        sims = self.vectors @ q  # усі косинуси одним множенням матриці на вектор
        best = np.maximum.reduceat(sims, self._starts)  # найкращий фрагмент кожного документа
        k = min(k, len(best))
        top = np.argpartition(best, -k)[-k:]
        top = top[np.lexsort((self._docs[top], -best[top]))]
        return [(int(self._docs[i]), float(best[i])) for i in top]


def semantic_text(query: str) -> str:
    """Для моделі прибираємо синтаксис запиту: оператори, лапки, дужки."""
    words = re.findall(r"[^\s()\"]+", query)
    return " ".join(w for w in words if w not in ("AND", "OR", "NOT"))


def rrf(rankings: Iterable[Sequence[DocId]], k: int = RRF_K) -> list[tuple[DocId, float]]:
    """Reciprocal Rank Fusion: score(d) = sum 1 / (k + rank_i(d)), rank з 1.

    Береться лише місце в кожному рейтингу, не бал: BM25 (0..30) і косинус (0..1)
    у різних шкалах, а місця можна порівнювати без нормалізації.
    """
    fused: dict[DocId, float] = {}
    for ranking in rankings:
        for rank, d in enumerate(ranking, start=1):
            fused[d] = fused.get(d, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: (-x[1], x[0]))


def search_any(
    searcher: Searcher,
    semantic: SemanticIndex | None,
    query: str,
    mode: SearchMode = "keyword",
    scorer: Scorer | None = None,
    k: int = 10,
) -> list[SearchResult]:
    """Один вхід для всіх режимів. keyword - BM25/TF-IDF з булевим фільтром (Lab 3, 8),
    semantic - близькість ембедингів, hybrid - обидва рейтинги, злиті через RRF."""
    if mode == "keyword":
        return searcher.search(query, scorer, k)
    if semantic is None:
        raise QueryError("semantic search is not available (no embeddings loaded)")
    titles = searcher.index.doc_meta
    if mode == "semantic":
        pairs = semantic.search(semantic_text(query), k)
        return [SearchResult(score, d, titles[d].title) for d, score in pairs]
    depth = max(k, FUSE_DEPTH)
    try:
        keyword = [r.doc_id for r in searcher.search(query, scorer, depth)]
    except QueryError:
        keyword = []  # запит не розібрався як булевий - лишається тільки семантика
    sem = [d for d, _ in semantic.search(semantic_text(query), depth)]
    return [SearchResult(score, d, titles[d].title) for d, score in rrf([keyword, sem])[:k]]
