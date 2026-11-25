"""Сніпети: шматок тексту ±80 символів навколо найкращого входження."""

from functools import lru_cache
from pathlib import Path

from findex.corpus import read_text
from findex.tokenize import TOKEN_RE, normalize


@lru_cache(maxsize=32)
def book_text(path):
    # книги всього 18, тому кешуємо, щоб не читати файл на кожен результат
    return read_text(Path(path))


def doc_text(meta):
    """Текст документа (абзацу) за DocMeta."""
    return book_text(meta.path)[meta.start : meta.end]


def make_snippet(text, terms, width=80, before="**", after="**"):
    """Вікно ±width символів, терми обгорнуті в before/after.

    Шукаємо токени тією ж регуляркою, що й tokenize, але в тексті без casefold,
    щоб позиції збігались з оригіналом. Порівнюємо вже m.group().casefold().
    """
    text = normalize(text)
    terms = set(terms)
    matches = [m for m in TOKEN_RE.finditer(text) if m.group().casefold() in terms]
    if not matches:
        return shorten(text, 0, min(len(text), 2 * width))

    # найкраще входження = навколо нього у вікні найбільше РІЗНИХ термів
    best = matches[0]
    best_count = 0
    for m in matches[:200]:  # обмеження, щоб на цілій книзі не було O(n^2)
        found = {x.group().casefold() for x in matches if abs(x.start() - m.start()) <= width}
        if len(found) > best_count:
            best = m
            best_count = len(found)

    start = max(0, best.start() - width)
    end = min(len(text), best.end() + width)

    parts = []
    pos = start
    for m in matches:
        if m.start() >= start and m.end() <= end:
            parts.append(text[pos : m.start()])
            parts.append(before + m.group() + after)
            pos = m.end()
    parts.append(text[pos:end])
    snippet = " ".join("".join(parts).split())  # переноси рядків -> пробіли

    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


def shorten(text, start, end):
    s = " ".join(text[start:end].split())
    return s + "..." if end < len(text) else s
