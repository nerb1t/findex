"""Витягуємо з HTML заголовок, текст і посилання (стандартний html.parser)."""

import re
from html.parser import HTMLParser

SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
# після цих тегів - новий рядок (а порожній рядок між абзацами - як у книгах корпусу)
BLOCK_TAGS = frozenset({"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "tr", "br"})
SPACES = re.compile(r"[ \t\r\f\v]+")


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.links: list[str] = []
        self.chunks: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in BLOCK_TAGS:
            self.chunks.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self.chunks.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.chunks.append(data)


def extract(html: str) -> tuple[str, str, list[str]]:
    """HTML -> (title, text, сирі href). Текст: абзаци розділені порожнім рядком."""
    p = _Extractor()
    p.feed(html)
    p.close()
    lines = [SPACES.sub(" ", line).strip() for line in "".join(p.chunks).split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return " ".join(p.title.split()), text, p.links
