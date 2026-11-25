"""Збереження індексу на диск.

Формат вибирається за розширенням файлу:
  .json -> json
  все інше (.bin, .pkl) -> pickle
"""

import json
import pickle
from array import array
from contextlib import contextmanager
from pathlib import Path

from findex.decorators import timed
from findex.models import DocMeta, Index, PlainPosting, Posting

# УВАГА: pickle.load виконує код!
# Pickle-файл - це не дані, а програма для маленької віртуальної машини, яка
# відтворює об'єкти. Через __reduce__ можна записати в файл "виклич os.system('...')",
# і pickle.load це виконає. Тому pickle можна вантажити ТІЛЬКИ зі своїх файлів
# (наш індекс ми самі створили). Файл зі скачаного архіву / з мережі - ніколи.


def save(index, path):
    path = Path(path)
    if path.suffix == ".json":
        save_json(index, path)
    else:
        save_pickle(index, path)


@timed
def load(path):
    path = Path(path)
    if path.suffix == ".json":
        return load_json(path)
    return load_pickle(path)


@contextmanager
def open_index(path):
    """with open_index("index.bin") as ix: ...

    Все до yield - це __enter__, все після - __exit__.
    finally спрацює і коли блок with закінчився нормально, і коли був return,
    і коли був виняток. Виняток ми не ковтаємо, він піде далі.
    """
    index = load(path)
    try:
        yield index
    finally:
        index.close()


# ---------- pickle ----------


def save_pickle(index, path):
    with open(path, "wb") as f:
        pickle.dump(index, f, protocol=pickle.HIGHEST_PROTOCOL)


def load_pickle(path):
    # тільки для своїх файлів, див. коментар зверху
    with open(path, "rb") as f:
        return pickle.load(f)


# ---------- json ----------
# json знає тільки dict/list/str/числа, тому все перетворюємо руками.
# Ключі в json завжди рядки, тому doc_id при завантаженні знову робимо int.


def save_json(index, path):
    if index.kind == "array":
        postings = {t: [list(ids), list(tfs)] for t, (ids, tfs) in index.postings.items()}
    else:
        postings = {}
        for term, plist in index.postings.items():
            rows = []
            for p in plist:
                if p.positions:
                    rows.append([p.doc_id, p.tf, list(p.positions)])
                else:
                    rows.append([p.doc_id, p.tf])
            postings[term] = rows

    data = {
        "kind": index.kind,
        "positions": index.positions,
        "postings": postings,
        "doc_lengths": index.doc_lengths,
        "doc_meta": {d: [m.path, m.title, m.start, m.end] for d, m in index.doc_meta.items()},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    kind = data["kind"]
    if kind == "array":
        postings = {
            t: (array("I", ids), array("I", tfs)) for t, (ids, tfs) in data["postings"].items()
        }
    else:
        cls = PlainPosting if kind == "plain" else Posting
        postings = {}
        for term, rows in data["postings"].items():
            plist = []
            for row in rows:
                if len(row) == 3:
                    plist.append(cls(row[0], row[1], tuple(row[2])))
                else:
                    plist.append(cls(row[0], row[1]))
            postings[term] = plist

    doc_lengths = {int(d): n for d, n in data["doc_lengths"].items()}
    doc_meta = {int(d): DocMeta(*m) for d, m in data["doc_meta"].items()}
    return Index(postings, doc_lengths, doc_meta, kind, data["positions"])
