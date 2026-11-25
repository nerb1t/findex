"""Одне ввічливе завантаження з таймаутом і повторами."""

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime

import httpx

log = logging.getLogger(__name__)

type Sleep = Callable[[float], Awaitable[None]]

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class FetchResult:
    url: str  # що просили
    final_url: str  # куди привели редіректи
    status: int  # HTTP-статус, 0 якщо відповіді не було (таймаут, помилка з'єднання)
    text: str
    content_type: str
    nbytes: int
    elapsed: float  # секунди, всі спроби разом
    attempts: int
    error: str | None = None  # "timeout", "redirect loop", "ConnectError" ...

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


def backoff(attempt: int, base: float = 0.5, cap: float = 10.0) -> float:
    """Експоненційна затримка з jitter: 0.5, 1, 2, 4... + випадкове 0..1 s.

    jitter - щоб 20 воркерів, які впали одночасно, не прийшли знову одночасно.
    """
    return min(cap, base * 2**attempt) + random.uniform(0, 1)


def retry_after(resp: httpx.Response) -> float | None:
    """Заголовок Retry-After: або число секунд, або HTTP-дата."""
    value = resp.headers.get("retry-after")
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float = 10.0,
    max_attempts: int = 3,
    max_retry_after: float = 60.0,
    sleep: Sleep = asyncio.sleep,
) -> FetchResult:
    """GET з повторами. Ніколи не кидає винятків (крім CancelledError) - помилка в result.error.

    Повторюємо: 429, 5xx, таймаут, обрив з'єднання. НЕ повторюємо: інші 4xx (404 не
    зміниться), зациклені редіректи. На 429 чекаємо стільки, скільки просить Retry-After.
    """
    start = time.perf_counter()
    status = 0
    error: str | None = None
    attempt = 0
    for attempt in range(1, max_attempts + 1):
        delay: float | None = None
        try:
            # asyncio.timeout скасовує все всередині, якщо не встигли - на весь запит
            # разом з тілом відповіді (а не тільки на з'єднання, як у httpx)
            async with asyncio.timeout(timeout):
                resp = await client.get(url)
        except TimeoutError:
            status, error = 0, "timeout"
        except httpx.TooManyRedirects:
            status, error = 0, "redirect loop"
            break  # повтор не допоможе
        except httpx.TransportError as e:  # ConnectError, ReadError, ...
            status, error = 0, type(e).__name__
        else:
            status = resp.status_code
            if status not in RETRY_STATUSES:
                text = resp.text
                return FetchResult(
                    url,
                    str(resp.url),
                    status,
                    text,
                    resp.headers.get("content-type", ""),
                    len(resp.content),
                    time.perf_counter() - start,
                    attempt,
                    None,
                )
            error = f"HTTP {status}"
            if status == 429:
                delay = retry_after(resp)
                if delay is not None and delay > max_retry_after:
                    break  # просять чекати занадто довго - здаємось
        if attempt == max_attempts:
            break
        if delay is None:
            delay = backoff(attempt - 1)
        log.info("retry %s in %.1f s (%s, attempt %d)", url, delay, error, attempt)
        await sleep(delay)  # asyncio.sleep, НЕ time.sleep: інші корутини працюють далі

    return FetchResult(url, url, status, "", "", 0, time.perf_counter() - start, attempt, error)
