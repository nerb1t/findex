import gc
from array import array
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Literal, cast

# Класи лежать в окремому модулі, а не в index.py.
# Причина: якщо запускати модуль як `python -m ...`, він стає `__main__`,
# і pickle запише клас як `__main__.Posting`. Тоді інший модуль не зможе його завантажити.

type DocId = int
type Kind = Literal["plain", "slots", "array"]


@dataclass(frozen=True, slots=True)
class Posting:
    doc_id: DocId
    tf: int  # скільки разів терм зустрівся в документі
    positions: tuple[int, ...] = ()  # номери токенів (тільки з --positions)


@dataclass(frozen=True)
class PlainPosting:
    # те саме, але БЕЗ slots — у кожного об'єкта є свій __dict__.
    # Потрібен тільки для порівняння пам'яті (Lab 2, M4)
    doc_id: DocId
    tf: int
    positions: tuple[int, ...] = ()


# у обох класів однакові поля, тому з об'єднанням можна працювати однаково
type AnyPosting = Posting | PlainPosting
type ArrayPostings = tuple[array[int], array[int]]  # (doc_ids, tfs)


@dataclass(frozen=True, slots=True)
class DocMeta:
    path: str
    title: str
    # де документ лежить у файлі книги (індекси символів) - для сніпетів
    start: int = 0
    end: int = 0


class Index(Mapping[str, list[AnyPosting]]):
    """Інвертований індекс: term -> list[Posting].

    Наслідуємо collections.abc.Mapping: реалізуємо тільки __getitem__, __len__,
    __iter__, а keys(), items(), values(), get(), __contains__ дістаємо безкоштовно.

    kind = "plain" | "slots" -> дані в self.postings (списки Posting)
    kind = "array"           -> дані в self.arrays (два array('I') на терм) і, якщо
                                є позиції, в self.array_positions (усі позиції терма підряд)
    """

    def __init__(
        self,
        postings: dict[str, list[AnyPosting]],
        doc_lengths: dict[DocId, int],
        doc_meta: dict[DocId, DocMeta],
        kind: Kind = "slots",
        positions: bool = False,
        arrays: dict[str, ArrayPostings] | None = None,
        array_positions: dict[str, array[int]] | None = None,
    ) -> None:
        self.postings = postings
        self.arrays: dict[str, ArrayPostings] = arrays if arrays is not None else {}
        # Lab 8: компактний індекс з позиціями (для фраз) - ~10x менше пам'яті, ніж Posting
        self.array_positions: dict[str, array[int]] = array_positions or {}
        self.doc_lengths = doc_lengths
        self.doc_meta = doc_meta
        self.kind: Kind = kind
        self.positions = positions  # чи є в postings позиції (для фраз)
        self.closed = False

    # ---------- протокол Mapping ----------

    def __getitem__(self, term: str) -> list[AnyPosting]:
        if self.kind == "array":
            ids, tfs = self.arrays[term]  # якщо терма немає - KeyError, як у dict
            pos = self.array_positions.get(term)
            if pos is None:
                return [Posting(d, tf) for d, tf in zip(ids, tfs, strict=True)]
            # об'єкти Posting створюються тільки на час запиту (для фраз)
            out: list[AnyPosting] = []
            o = 0
            for d, tf in zip(ids, tfs, strict=True):
                out.append(Posting(d, tf, tuple(pos[o : o + tf])))
                o += tf
            return out
        return self.postings[term]

    def __len__(self) -> int:
        return len(self._terms())  # кількість термів

    def __iter__(self) -> Iterator[str]:
        return iter(self._terms())

    def __contains__(self, term: object) -> bool:
        # Mapping вміє це сам (через __getitem__ + try/except), але так швидше
        return term in self._terms()

    def __repr__(self) -> str:
        return f"Index(terms={len(self):_}, docs={self.num_docs:_}, kind={self.kind!r})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Index):
            return NotImplemented  # не False: хай Python спробує other.__eq__
        return (
            self.kind == other.kind
            and self.positions == other.positions
            and self.postings == other.postings
            and self.arrays == other.arrays
            and self.array_positions == other.array_positions
            and self.doc_lengths == other.doc_lengths
            and self.doc_meta == other.doc_meta
        )

    __hash__ = None  # Mapping теж так робить; індекс змінюваний, тому не хешується

    # ---------- pickle (Lab 7) ----------
    # Якщо pickle пише 1.5 млн об'єктів Posting поштучно, то при завантаженні
    # unpickler тримає їх усі ще й у своїй мемо-таблиці: пік пам'яті ~2x від
    # індексу, і ця пам'ять потім не повертається ОС (у Docker RSS був 540 MB,
    # а безкоштовний Render дає 512 MB). Тому в файл пишемо компактно - по
    # три array('I') на терм, а Posting збираємо назад уже при завантаженні.

    def __getstate__(self) -> dict[str, object]:
        state = self.__dict__.copy()
        state.pop("avg_doc_length", None)  # cached_property порахується заново
        packed: dict[str, tuple[array[int], array[int], array[int]]] = {}
        for term, plist in self.postings.items():
            ids = array("I", [p.doc_id for p in plist])
            tfs = array("I", [p.tf for p in plist])
            pos = array("I", [i for p in plist for i in p.positions])  # усі позиції підряд
            packed[term] = (ids, tfs, pos)
        state["postings"] = packed
        return state

    def __setstate__(self, state: dict[str, object]) -> None:
        packed = cast(dict[str, tuple[array[int], array[int], array[int]]], state["postings"])
        self.__dict__.update(state)
        self.__dict__.setdefault("array_positions", {})  # старі файли (до Lab 8)
        cls = PlainPosting if state["kind"] == "plain" else Posting
        doc_lengths = cast(dict[DocId, int], state["doc_lengths"])
        # один об'єкт int на документ (як при побудові), а не новий на кожен posting
        doc_ints = list(range(max(doc_lengths, default=-1) + 1))
        postings: dict[str, list[AnyPosting]] = {}
        was_enabled = gc.isenabled()
        gc.disable()  # мільйон нових об'єктів без циклів - gc тут лише заважає (Lab 5)
        try:
            for term, (ids, tfs, pos) in packed.items():
                if pos:
                    plist: list[AnyPosting] = []
                    o = 0
                    for d, tf in zip(ids, tfs, strict=True):
                        plist.append(cls(doc_ints[d], tf, tuple(pos[o : o + tf])))
                        o += tf
                else:
                    plist = [cls(doc_ints[d], tf) for d, tf in zip(ids, tfs, strict=True)]
                postings[term] = plist
        finally:
            if was_enabled:
                gc.enable()
        self.postings = postings

    def _terms(self) -> dict[str, list[AnyPosting]] | dict[str, ArrayPostings]:
        return self.arrays if self.kind == "array" else self.postings

    # ---------- властивості ----------

    @property
    def num_docs(self) -> int:
        return len(self.doc_lengths)

    @cached_property
    def avg_doc_length(self) -> float:
        # рахується один раз, потім лежить в self.__dict__["avg_doc_length"].
        # BM25 питає це для кожного документа, тому кеш дуже важливий
        if not self.doc_lengths:
            return 0.0
        return sum(self.doc_lengths.values()) / self.num_docs

    def doc_length(self, doc_id: DocId) -> int:
        return self.doc_lengths[doc_id]

    def df(self, term: str) -> int:
        """У скількох документах є терм."""
        if self.kind == "array":
            pair = self.arrays.get(term)
            return 0 if pair is None else len(pair[0])
        plist = self.postings.get(term)
        return 0 if plist is None else len(plist)

    def doc_ids(self, term: str) -> list[DocId] | array[int]:
        """Відсортований список doc_id для терма (або порожній)."""
        if self.kind == "array":
            pair = self.arrays.get(term)
            return [] if pair is None else pair[0]  # array('I') теж можна індексувати
        plist = self.postings.get(term)
        return [] if plist is None else [p.doc_id for p in plist]

    def all_doc_ids(self) -> list[DocId]:
        # doc_id видаються по порядку 0, 1, 2..., а dict зберігає порядок вставки
        return list(self.doc_meta)

    def term_frequency(self, term: str) -> int:
        """Скільки разів терм зустрічається в усьому корпусі (сума tf)."""
        if self.kind == "array":
            pair = self.arrays.get(term)
            return 0 if pair is None else sum(pair[1])
        return sum(p.tf for p in self.postings.get(term, ()))

    def num_postings(self) -> int:
        if self.kind == "array":
            return sum(len(ids) for ids, _ in self.arrays.values())
        return sum(len(p) for p in self.postings.values())

    def close(self) -> None:
        """Звільняє пам'ять. Після close індекс порожній."""
        self.postings = {}
        self.arrays = {}
        self.array_positions = {}
        self.doc_lengths = {}
        self.doc_meta = {}
        self.__dict__.pop("avg_doc_length", None)  # скинути cached_property
        self.closed = True
