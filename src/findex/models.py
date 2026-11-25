from dataclasses import dataclass

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
    # Потрібен тільки для порівняння пам'яті (M4)
    doc_id: int
    tf: int
    positions: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class DocMeta:
    path: str
    title: str


@dataclass
class Index:
    # kind = "plain" | "slots" -> postings: dict[str, list[Posting]]
    # kind = "array"           -> postings: dict[str, (array doc_ids, array tfs)]
    postings: dict
    doc_lengths: dict[int, int]
    doc_meta: dict[int, DocMeta]
    kind: str = "slots"

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
