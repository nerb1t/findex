from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property

# Класи лежать в окремому модулі, а не в index.py.
# Причина: якщо запускати `python -m findex.index`, то модуль index стає `__main__`,
# і pickle запише клас як `__main__.Posting`. Тоді search.py не зможе його завантажити.
# А `findex.models.Posting` знайдеться звідки завгодно.


@dataclass(frozen=True, slots=True)
class Posting:
    doc_id: int
    tf: int  # скільки разів терм зустрівся в документі
    positions: tuple[int, ...] = ()  # номери токенів (тільки з --positions)


@dataclass(frozen=True)
class PlainPosting:
    # те саме, але БЕЗ slots — у кожного об'єкта є свій __dict__.
    # Потрібен тільки для порівняння пам'яті (Lab 2, M4)
    doc_id: int
    tf: int
    positions: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DocMeta:
    path: str
    title: str
    # де документ лежить у файлі книги (індекси символів) - для сніпетів
    start: int = 0
    end: int = 0


class Index(Mapping):
    """Інвертований індекс: term -> list[Posting].

    Наслідуємо collections.abc.Mapping: реалізуємо тільки __getitem__, __len__,
    __iter__, а keys(), items(), values(), get(), __contains__ дістаємо безкоштовно.

    kind = "plain" | "slots" -> postings: dict[str, list[Posting]]
    kind = "array"           -> postings: dict[str, (array doc_ids, array tfs)]
    """

    def __init__(self, postings, doc_lengths, doc_meta, kind="slots", positions=False):
        self.postings = postings
        self.doc_lengths = doc_lengths
        self.doc_meta = doc_meta
        self.kind = kind
        self.positions = positions  # чи є в postings позиції (для фраз)
        self.closed = False

    # ---------- протокол Mapping ----------

    def __getitem__(self, term):
        p = self.postings[term]  # якщо терма немає - KeyError, як у dict
        if self.kind == "array":
            ids, tfs = p
            return [Posting(d, tf) for d, tf in zip(ids, tfs, strict=True)]
        return p

    def __len__(self):
        return len(self.postings)  # кількість термів

    def __iter__(self):
        return iter(self.postings)

    def __contains__(self, term):
        # Mapping вміє це сам (через __getitem__ + try/except), але так швидше
        return term in self.postings

    def __repr__(self):
        return f"Index(terms={len(self):_}, docs={self.num_docs:_}, kind={self.kind!r})"

    def __eq__(self, other):
        if not isinstance(other, Index):
            return NotImplemented  # не False: хай Python спробує other.__eq__
        return (
            self.kind == other.kind
            and self.positions == other.positions
            and self.postings == other.postings
            and self.doc_lengths == other.doc_lengths
            and self.doc_meta == other.doc_meta
        )

    __hash__ = None  # індекс змінюваний (close), тому не хешується

    # ---------- властивості ----------

    @property
    def num_docs(self):
        return len(self.doc_lengths)

    @cached_property
    def avg_doc_length(self):
        # рахується один раз, потім лежить в self.__dict__["avg_doc_length"].
        # BM25 питає це для кожного документа, тому кеш дуже важливий
        if not self.doc_lengths:
            return 0.0
        return sum(self.doc_lengths.values()) / self.num_docs

    def doc_length(self, doc_id):
        return self.doc_lengths[doc_id]

    def df(self, term):
        """У скількох документах є терм."""
        p = self.postings.get(term)
        if p is None:
            return 0
        if self.kind == "array":
            return len(p[0])
        return len(p)

    def doc_ids(self, term):
        """Відсортований список doc_id для терма (або порожній)."""
        p = self.postings.get(term)
        if p is None:
            return []
        if self.kind == "array":
            return p[0]  # array('I') теж можна індексувати як список
        return [x.doc_id for x in p]

    def all_doc_ids(self):
        # doc_id видаються по порядку 0, 1, 2..., а dict зберігає порядок вставки
        return list(self.doc_meta)

    def num_postings(self):
        if self.kind == "array":
            return sum(len(ids) for ids, _ in self.postings.values())
        return sum(len(p) for p in self.postings.values())

    def close(self):
        """Звільняє пам'ять. Після close індекс порожній."""
        self.postings = {}
        self.doc_lengths = {}
        self.doc_meta = {}
        self.__dict__.pop("avg_doc_length", None)  # скинути cached_property
        self.closed = True
