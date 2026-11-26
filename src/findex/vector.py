"""Lab 8, M2: постинги в масивах NumPy і BM25 / TF-IDF виразами над масивами.

Профіль (docs/profiling.md) показав, що пошук - це Python-цикл по об'єктах Posting:
для "the" 35 572 ітерації, на кожну - перевірка `in scores` і виклик скорера.
Тут ті самі постинги лежать як три суцільні масиви на весь індекс (як CSR):

    ids  int32[P]   - doc_id усіх postings підряд (терм за термом)
    tfs  int32[P]   - tf
    span term -> (start, end) - де в ids/tfs лежать postings цього терма

і бал для ВСІХ документів терма рахується одним виразом (цикл у C, без
об'єктів Python на кожен posting).
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from findex.models import ArrayPostings, DocId, Index
from findex.scoring import BM25, TfIdf

type IntArray = NDArray[np.int32]
type FloatArray = NDArray[np.float64]


@dataclass
class VectorPostings:
    ids: IntArray
    tfs: IntArray
    span: dict[str, tuple[int, int]]
    doc_len: IntArray  # int32[N]: довжина кожного документа
    num_docs: int
    avg_doc_length: float

    # Lab 8, M3: для компактного індексу (kind="array") масиви вже є в пам'яті -
    # беремо np.frombuffer прямо з них при кожному запиті, без жодної копії (~1 мкс).
    # Без цього копія всіх постингів займала +54 MB, а на Render ліміт 512 MB.
    arrays: dict[str, ArrayPostings] | None = None

    @classmethod
    def from_index(cls, index: Index) -> "VectorPostings":
        """Один раз перекладаємо постинги в масиви (~0.3 s для slots, ~0 для array)."""
        n = index.num_docs
        doc_len = np.zeros(n, np.int32)
        for d, length in index.doc_lengths.items():
            doc_len[d] = length
        empty = np.zeros(0, np.int32)
        if index.kind == "array":
            return cls(empty, empty, {}, doc_len, n, index.avg_doc_length, index.arrays)
        span: dict[str, tuple[int, int]] = {}
        id_parts: list[IntArray] = []
        tf_parts: list[IntArray] = []
        start = 0
        for term in index:
            plist = index[term]
            id_parts.append(np.fromiter((p.doc_id for p in plist), np.int32, len(plist)))
            tf_parts.append(np.fromiter((p.tf for p in plist), np.int32, len(plist)))
            span[term] = (start, start + len(plist))
            start += len(plist)
        return cls(
            ids=np.concatenate(id_parts) if id_parts else empty,
            tfs=np.concatenate(tf_parts) if tf_parts else empty,
            span=span,
            doc_len=doc_len,
            num_docs=n,
            avg_doc_length=index.avg_doc_length,
        )

    def postings(self, term: str) -> tuple[IntArray, IntArray]:
        if self.arrays is not None:
            pair = self.arrays.get(term)
            if pair is None or len(pair[0]) == 0:
                return self.ids, self.tfs  # порожні
            # array('I') - uint32; doc_id < 2^31, тож ті самі байти як int32 - те саме число
            return np.frombuffer(pair[0], np.int32), np.frombuffer(pair[1], np.int32)
        start, end = self.span.get(term, (0, 0))
        return self.ids[start:end], self.tfs[start:end]  # зрізи - це view, без копії


def score_terms(
    vp: VectorPostings, terms: list[str], scorer: BM25 | TfIdf, cands: IntArray
) -> FloatArray:
    """Бали лише для документів cands (відсортовані doc_id з булевого фільтра).

    Масив балів - довжини len(cands), а не на весь корпус: для "the white whale"
    це 108 чисел, а не 47 887. Postings терма, яких немає в cands, відкидаємо
    через np.searchsorted (двійковий пошук у C для всього масиву одразу).
    Порядок дій той самий, що в scoring.py, тому бали збігаються з Lab 3
    до останнього біта (це перевіряє тест).
    """
    # Якщо кандидатів багато (> 1/8 корпусу, напр. "the"), дешевше порахувати
    # щільний масив на весь корпус і в кінці взяти scores[cands] - без пошуку.
    dense = len(cands) * 8 > vp.num_docs
    scores = np.zeros(vp.num_docs if dense else len(cands), np.float64)
    if len(cands) == 0:
        return scores
    for term in terms:
        ids, tfs = vp.postings(term)
        if len(ids) == 0:
            continue
        df = len(ids)  # df рахуємо по ВСІХ postings терма, до фільтра
        if dense:
            pos = ids
        else:
            pos = np.searchsorted(cands, ids)  # де стояв би кожен doc_id серед cands
            hit = pos < len(cands)
            hit[hit] = cands[pos[hit]] == ids[hit]  # чи справді там цей doc_id
            if not hit.all():
                ids, tfs, pos = ids[hit], tfs[hit], pos[hit]
        tf = tfs.astype(np.float64)
        if isinstance(scorer, BM25):
            idf = math.log(1 + (vp.num_docs - df + 0.5) / (df + 0.5))
            # fancy indexing: довжини саме тих документів, де є терм
            norm = 1 - scorer.b + scorer.b * vp.doc_len[ids] / vp.avg_doc_length
            part = idf * tf * (scorer.k1 + 1) / (tf + scorer.k1 * norm)
        else:
            part = tf * math.log(vp.num_docs / df)
        # у межах одного терма doc_id не повторюються, тож += по індексах коректне
        scores[pos] += part
    return scores[cands] if dense else scores


def top_k(candidates: IntArray, cand_scores: FloatArray, k: int) -> list[tuple[DocId, float]]:
    """Топ-k: cand_scores[i] - бал документа candidates[i]. Бал за спаданням,
    при рівних - менший doc_id.

    np.argpartition знаходить k найбільших за O(n) (без повного сортування),
    а сортуємо вже лише ці k.
    """
    if len(candidates) == 0 or k <= 0:
        return []
    if len(candidates) > k:
        # межа k-го місця; беремо все, що >= неї, щоб не загубити рівні бали на межі
        part = np.argpartition(cand_scores, -k)[-k:]
        threshold = cand_scores[part].min()
        keep = np.flatnonzero(cand_scores >= threshold)
        candidates, cand_scores = candidates[keep], cand_scores[keep]
    # lexsort: останній ключ - головний. Спершу -бал, потім doc_id
    order = np.lexsort((candidates, -cand_scores))[:k]
    return [(int(candidates[i]), float(cand_scores[i])) for i in order]
