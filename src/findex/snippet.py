"""Сніпети: шматок тексту ±80 символів навколо найкращого входження."""

import logging
from collections.abc import Iterable
from functools import lru_cache

from findex.corpus import read_source
from findex.models import DocMeta
from findex.tokenize import TOKEN_RE, normalize

log = logging.getLogger(__name__)

# сніпет = список шматків (текст, чи підсвічувати). Так CLI може сам вирішити,
# як підсвітити (rich-стиль, ** для markdown, нічого для json)
type Parts = list[tuple[str, bool]]


@lru_cache(maxsize=32)
def book_text(path: str) -> str:
    # книги всього 18, тому кешуємо, щоб не читати файл на кожен результат
    return read_source(path)


def doc_text(meta: DocMeta) -> str:
    """Текст документа (абзацу) за DocMeta. Тут єдине місце з читанням файлу.

    Якщо книгу перемістили / видалили після індексації - не падаємо,
    а показуємо порожній сніпет і пишемо warning.
    """
    try:
        return book_text(meta.path)[meta.start : meta.end]
    except OSError as e:
        log.warning("cannot read %s for snippet: %s", meta.path, e.strerror)
        return ""


def snippet_parts(text: str, terms: Iterable[str], width: int = 80) -> Parts:
    """Вікно ±width символів навколо найкращого входження, розбите на шматки.

    Шукаємо токени тією ж регуляркою, що й tokenize, але в тексті без casefold,
    щоб позиції збігались з оригіналом. Порівнюємо вже m.group().casefold().
    """
    text = normalize(text)
    wanted = set(terms)
    matches = [m for m in TOKEN_RE.finditer(text) if m.group().casefold() in wanted]
    if not matches:
        end = min(len(text), 2 * width)
        tail = "..." if end < len(text) else ""
        return [(" ".join(text[:end].split()) + tail, False)]

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

    parts: Parts = []
    if start > 0:
        parts.append(("...", False))
    pos = start
    for m in matches:
        if m.start() >= start and m.end() <= end:
            parts.append((text[pos : m.start()], False))
            parts.append((m.group(), True))
            pos = m.end()
    parts.append((text[pos:end], False))
    if end < len(text):
        parts.append(("...", False))

    # переноси рядків і подвійні пробіли -> один пробіл, але пробіли на межах залишаємо
    clean: Parts = []
    for s, hl in parts:
        squeezed = " ".join(s.split())
        if s[:1].isspace() and squeezed:
            squeezed = " " + squeezed
        if s[-1:].isspace():
            squeezed = squeezed + " "
        if squeezed:
            clean.append((squeezed, hl))
    return clean


def make_snippet(
    text: str, terms: Iterable[str], width: int = 80, before: str = "**", after: str = "**"
) -> str:
    """Сніпет одним рядком, терми обгорнуті в before/after."""
    return "".join(before + s + after if hl else s for s, hl in snippet_parts(text, terms, width))
