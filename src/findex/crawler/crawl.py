"""Краулер: черга адрес (frontier) + воркери в TaskGroup + ліміти ввічливості.

    seeds -> frontier (asyncio.Queue) -> N воркерів -> out (asyncio.Queue) -> async for page

Усе в одному потоці. Воркер віддає керування на кожному await (мережа, sleep,
семафор), і цикл подій запускає іншого. Між двома await код виконується
без перерви, тому seen/лічильники можна міняти без Lock.
"""

import asyncio
import importlib
import logging
import time
from collections.abc import AsyncGenerator, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from findex.crawler import USER_AGENT
from findex.crawler.fetch import FetchResult, Sleep, fetch
from findex.crawler.html import extract
from findex.crawler.robots import RobotsCache
from findex.crawler.urls import canonicalize, domain_allowed, host_of

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    fetched_at: str  # ISO 8601, UTC


@dataclass
class CrawlStats:
    """Живі лічильники для rich-екрана. Міняються тільки в циклі подій."""

    started: float = field(default_factory=time.perf_counter)
    pages: int = 0  # успішно отримані HTML-сторінки
    requests: int = 0  # усі завантаження (крім robots.txt), з повторами - одне
    errors: int = 0  # status 0 / 4xx / 5xx після всіх спроб
    skipped: int = 0  # robots.txt, не HTML
    in_flight: int = 0
    queued: int = 0

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    @property
    def pages_per_s(self) -> float:
        return self.pages / self.elapsed if self.elapsed > 0 else 0.0


type OnResult = Callable[[FetchResult, str], None]


def make_client(concurrency: int = 10, timeout: float = 10.0) -> httpx.AsyncClient:
    # httpx/httpcore на ПЕРШОМУ запиті ліниво імпортують anyio (~100 мс, debug=True
    # це показав як "worker took 0.105 seconds"). Імпортуємо заздалегідь, тут,
    # бо ця функція і так виконується в окремому потоці
    for name in ("anyio.abc", "anyio.lowlevel", "anyio.streams.tls", "anyio.to_thread"):
        importlib.import_module(name)

    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        max_redirects=5,  # більше - вважаємо зацикленим редіректом
        timeout=timeout,
        limits=httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency),
    )


class _HostLimits:
    """Ввічливість до одного сервера: не більше per_host запитів одночасно
    і не частіше, ніж раз на delay секунд."""

    def __init__(self, per_host: int, delay: float) -> None:
        self.sem = asyncio.Semaphore(per_host)
        self.delay = delay
        self.next_slot = 0.0

    async def wait_turn(self) -> None:
        # бронюємо час наступного запиту ДО await - між читанням і записом
        # next_slot немає перемикання, тому два воркери не візьмуть один слот
        now = time.monotonic()
        slot = max(now, self.next_slot)
        self.next_slot = slot + self.delay
        if slot > now:
            await asyncio.sleep(slot - now)


async def crawl(
    seeds: Iterable[str],
    *,
    max_pages: int = 100,
    concurrency: int = 10,
    per_host: int = 2,
    delay: float = 0.5,
    allowed_domains: Iterable[str] | None = None,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
    stats: CrawlStats | None = None,
    on_result: OnResult | None = None,
    sleep: Sleep = asyncio.sleep,
) -> AsyncGenerator[Page]:
    """Асинхронний генератор сторінок: async for page in crawl([...]): ...

    allowed_domains за замовчуванням - хости з seeds. Сторінки віддаються по мірі
    надходження, в пам'яті тільки черги. Після max_pages воркери скасовуються.
    """
    start_urls = [u for u in (canonicalize(s) for s in seeds) if u is not None]
    if not start_urls:
        raise ValueError("no valid http(s) seed URLs")
    if allowed_domains is None:
        allowed = frozenset((httpx.URL(u).host or "").lower() for u in start_urls)
    else:
        allowed = frozenset(d.lower() for d in allowed_domains)
    st = stats if stats is not None else CrawlStats()

    own_client = client is None
    if client is None:
        # один клієнт на весь обхід: пул з'єднань, keep-alive - головне джерело швидкості.
        # Створення клієнта вантажить SSL-сертифікати з диска (~175 мс блокуючої роботи) -
        # asyncio debug=True це впіймав. Тому створюємо його в окремому потоці
        client = await asyncio.to_thread(make_client, concurrency, timeout)
    robots = RobotsCache(client, timeout=timeout, sleep=sleep)
    frontier: asyncio.Queue[str] = asyncio.Queue()
    out: asyncio.Queue[Page | None] = asyncio.Queue()  # None = "все, сторінок більше не буде"
    seen: set[str] = set()
    hosts: dict[str, _HostLimits] = {}
    global_sem = asyncio.Semaphore(concurrency)  # стеля на всі запити разом

    def enqueue(url: str) -> None:
        if url in seen or not domain_allowed(url, allowed):
            return
        seen.add(url)
        frontier.put_nowait(url)
        st.queued = frontier.qsize()

    def report(res: FetchResult, note: str) -> None:
        if on_result is not None:
            on_result(res, note)

    async def process(url: str) -> None:
        if st.pages >= max_pages:
            return  # бюджет вичерпано - решту черги тільки "відпрацьовуємо"
        if not await robots.allowed(url):
            st.skipped += 1
            report(FetchResult(url, url, 0, "", "", 0, 0.0, 0), "robots.txt disallow")
            return
        host = host_of(url)
        if host not in hosts:
            crawl_delay = await robots.crawl_delay(url)
            hosts[host] = _HostLimits(per_host, max(delay, crawl_delay or 0.0))
        limits = hosts[host]
        async with limits.sem, global_sem:  # спочатку хост, потім глобальний слот
            await limits.wait_turn()
            st.in_flight += 1
            try:
                res = await fetch(client, url, timeout=timeout, sleep=sleep)
            finally:
                st.in_flight -= 1
        st.requests += 1

        final = canonicalize(res.final_url) or url
        if final != url:
            seen.add(final)  # редірект: цільову адресу теж вважаємо відвіданою
        if not res.ok:
            st.errors += 1
            report(res, res.error or "")
            return
        if "html" not in res.content_type:
            st.skipped += 1
            report(res, "not html")
            return
        if st.pages >= max_pages:
            report(res, "over max_pages, dropped")
            return
        # розбір HTML - це робота CPU, але маленька (кілька мс на сторінку).
        # Токенізація й індексація - НЕ тут, а потім, окремою командою findex index
        title, text, links = extract(res.text)
        for href in links:
            link = canonicalize(href, base=res.final_url)
            if link is not None:
                enqueue(link)
        st.pages += 1
        report(res, "")
        await out.put(Page(final, title, text, datetime.now(UTC).isoformat(timespec="seconds")))

    async def worker() -> None:
        while True:
            url = await frontier.get()
            st.queued = frontier.qsize()
            try:
                await process(url)
            except Exception:  # noqa: BLE001
                # помилка в одній адресі не повинна валити весь обхід.
                # CancelledError - це BaseException, тому сюди НЕ потрапляє і скасування працює
                log.exception("worker failed on %s", url)
                st.errors += 1
            finally:
                frontier.task_done()

    async def finisher() -> None:
        await frontier.join()  # усі адреси оброблено (або воркери скасовані)
        await out.put(None)

    async def engine() -> None:
        # TaskGroup = структурна конкурентність: після виходу з блоку жодна задача
        # не живе; якщо одна впала - решту скасовано, виняток іде далі.
        async with asyncio.TaskGroup() as tg:
            for _ in range(concurrency):
                tg.create_task(worker())
            tg.create_task(finisher())

    for u in start_urls:
        enqueue(u)
    # yield НЕ можна робити всередині TaskGroup (PEP 789): тоді скасування групи
    # прилітає в код того, хто читає генератор, а break/aclose з async for
    # перетворюється на BaseExceptionGroup(GeneratorExit). Тому група живе в окремій
    # задачі, а генератор тільки читає чергу out. Час життя задачі все одно обмежений:
    # finally нижче скасовує її і чекає, поки всі воркери зупиняться.
    task = asyncio.create_task(engine())
    task.add_done_callback(lambda _: out.put_nowait(None))  # впала - розбудити читача
    try:
        while (page := await out.get()) is not None:
            yield page
            if st.pages >= max_pages and out.empty():
                break
        if task.done() and not task.cancelled() and task.exception() is not None:
            raise task.exception()  # type: ignore[misc]  # помилка в самому краулері (баг)
    finally:
        task.cancel()  # чиста зупинка: CancelledError у кожному await воркера
        # asyncio.wait не кидає виняток задачі і не ковтає скасування НАШОЇ задачі
        await asyncio.wait([task])
        if own_client:
            await client.aclose()
