import re
import unicodedata
from collections.abc import Iterator

# слово = літери, всередині можуть бути ' або - (don't, п'ять, well-known)
# або окремо число з цифр
# [^\W\d_] - це будь-яка літера (\w без цифр і без _), працює і для кирилиці
TOKEN_RE = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*|\d+")


def normalize(text: str) -> str:
    """NFC + однаковий апостроф, але БЕЗ casefold (потрібно для сніпетів)."""
    text = unicodedata.normalize("NFC", text)
    return text.replace("’", "'").replace("ʼ", "'")  # ’ і ʼ -> '


def tokenize(text: str) -> Iterator[str]:
    """Генератор токенів: NFC -> однаковий апостроф -> casefold -> finditer."""
    text = normalize(text).casefold()
    for m in TOKEN_RE.finditer(text):
        yield m.group()
