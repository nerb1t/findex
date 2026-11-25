"""Канонізація URL: різні записи однієї адреси -> один рядок, щоб не качати двічі."""

from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize(url: str, base: str | None = None) -> str | None:
    """Повертає канонічний URL або None, якщо це не http(s).

    - відносне посилання -> абсолютне (через base)
    - схема і хост у нижньому регістрі, порт за замовчуванням прибираємо
    - фрагмент (#...) відкидаємо - це та сама сторінка
    - порожній шлях -> "/", а "/a/" і "/a" вважаємо однією сторінкою (без кінцевого /)
    - параметри query сортуємо: ?b=2&a=1 == ?a=1&b=2
    """
    if base is not None:
        url = urljoin(base, url)
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname:
        return None  # mailto:, javascript:, ftp: ...
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"  # IPv6
    port = parts.port
    netloc = host if port is None or port == DEFAULT_PORTS[scheme] else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def host_of(url: str) -> str:
    """Хост для лімітів ввічливості: host:port (різні порти - різні сервери)."""
    return urlsplit(url).netloc


def domain_allowed(url: str, allowed: frozenset[str]) -> bool:
    """Чи хост (без порту) у списку дозволених або його піддомен."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowed)
