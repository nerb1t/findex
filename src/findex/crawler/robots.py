"""robots.txt: качаємо один раз на хост, кешуємо, відповідаємо allowed(url)."""

import asyncio
import logging
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from findex.crawler import USER_AGENT
from findex.crawler.fetch import Sleep, fetch

log = logging.getLogger(__name__)


class RobotsCache:
    def __init__(
        self,
        client: httpx.AsyncClient,
        user_agent: str = USER_AGENT,
        *,
        timeout: float = 10.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.client = client
        self.user_agent = user_agent
        self.timeout = timeout
        self.sleep = sleep
        self._parsers: dict[str, RobotFileParser] = {}
        # Lock на хост: 20 воркерів одночасно прийдуть на новий хост, а robots.txt
        # треба скачати ОДИН раз. Тут між перевіркою "є в кеші?" і записом в кеш
        # є await - саме в такому місці в asyncio знову потрібен Lock
        self._locks: dict[str, asyncio.Lock] = {}
        self.fetches = 0  # скільки разів реально качали robots.txt (для тестів)

    async def parser_for(self, url: str) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._parsers:
                self._parsers[origin] = await self._load(origin)
        return self._parsers[origin]

    async def _load(self, origin: str) -> RobotFileParser:
        self.fetches += 1
        rp = RobotFileParser()
        res = await fetch(
            self.client, origin + "/robots.txt", timeout=self.timeout, sleep=self.sleep
        )
        if res.ok:
            rp.parse(res.text.splitlines())
        elif 400 <= res.status < 500:
            rp.parse([])  # robots.txt немає - можна все (так роблять Google і Bing)
        else:
            # 5xx / мережа: не знаємо правил - краще нічого не чіпати
            rp.parse(["User-agent: *", "Disallow: /"])
            log.warning("robots.txt for %s unavailable (%s), not crawling it", origin, res.error)
        return rp

    async def allowed(self, url: str) -> bool:
        return (await self.parser_for(url)).can_fetch(self.user_agent, url)

    async def crawl_delay(self, url: str) -> float | None:
        delay = (await self.parser_for(url)).crawl_delay(self.user_agent)
        return float(delay) if delay is not None else None
