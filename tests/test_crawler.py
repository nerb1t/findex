"""Lab 6: краулер. Жодної живої мережі - усе через httpx.MockTransport."""

import asyncio
import json
import time
from collections import Counter
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

import findex.crawler.crawl as crawl_mod
from findex.cli import app
from findex.crawler.crawl import CrawlStats, Page, crawl
from findex.crawler.fetch import FetchResult, backoff, fetch, retry_after
from findex.crawler.html import extract
from findex.crawler.robots import RobotsCache
from findex.crawler.urls import canonicalize, domain_allowed, host_of
from findex.index import build_index
from findex.parallel import build_index_parallel
from findex.search import Searcher
from findex.snippet import doc_text

type Handler = Callable[[httpx.Request], Coroutine[Any, Any, httpx.Response]]


def client_for(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True, max_redirects=5
    )


class FakeSleep:
    """Замість asyncio.sleep: запам'ятовує затримки і не чекає (тести миттєві)."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ---------- URL ----------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTP://Example.COM", "http://example.com/"),
        ("http://example.com:80/a/", "http://example.com/a"),
        ("https://example.com:443/a#frag", "https://example.com/a"),
        ("http://example.com:8001/x?b=2&a=1", "http://example.com:8001/x?a=1&b=2"),
        ("http://example.com/A/B/", "http://example.com/A/B"),  # шлях - з урахуванням регістру
        ("mailto:me@example.com", None),
        ("javascript:void(0)", None),
        ("ftp://example.com/f", None),
    ],
)
def test_canonicalize(url: str, expected: str | None) -> None:
    assert canonicalize(url) == expected


def test_canonicalize_relative() -> None:
    assert canonicalize("../b.html#x", base="http://h.com/a/c/d.html") == "http://h.com/a/b.html"
    assert canonicalize("/", base="http://h.com/a") == "http://h.com/"


def test_domains_and_hosts() -> None:
    allowed = frozenset({"example.com"})
    assert domain_allowed("http://example.com/a", allowed)
    assert domain_allowed("http://docs.example.com:8080/", allowed)
    assert not domain_allowed("http://badexample.com/", allowed)
    assert host_of("http://127.0.0.1:8001/x") == "127.0.0.1:8001"


def test_extract() -> None:
    html = (
        "<html><head><title> My  Page </title><style>p{}</style></head><body>"
        "<h1>Hi</h1><p>One <b>two</b></p><script>var x='<p>no</p>';</script>"
        '<p>Three &amp; four</p><a href="/a">A</a><a>no href</a>'
        '<a href="b.html">B</a></body></html>'
    )
    title, text, links = extract(html)
    assert title == "My Page"
    assert text == "Hi\n\nOne two\n\nThree & four\n\nAno hrefB"  # текст посилань - теж текст
    assert links == ["/a", "b.html"]


# ---------- fetch ----------


def test_backoff_grows_and_is_capped() -> None:
    for attempt in range(6):
        d = backoff(attempt, base=0.5, cap=4)
        assert min(4, 0.5 * 2**attempt) <= d <= min(4, 0.5 * 2**attempt) + 1


def test_retry_after_parsing() -> None:
    assert retry_after(httpx.Response(429, headers={"Retry-After": "7"})) == 7.0
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    parsed = retry_after(httpx.Response(429, headers={"Retry-After": when}))
    assert parsed is not None and 25 < parsed <= 30
    assert retry_after(httpx.Response(429)) is None
    assert retry_after(httpx.Response(429, headers={"Retry-After": "soon"})) is None


def run_fetch(handler: Handler, **kw: float) -> tuple[FetchResult, FakeSleep]:
    sleep = FakeSleep()

    async def go() -> FetchResult:
        async with client_for(handler) as client:
            return await fetch(client, "http://h.com/p", sleep=sleep, **kw)  # type: ignore[arg-type]

    return asyncio.run(go()), sleep


def test_fetch_ok() -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html="<p>hi</p>")

    res, sleep = run_fetch(h)
    assert res.ok and res.status == 200 and res.attempts == 1 and res.nbytes == 9
    assert sleep.calls == []


def test_fetch_retries_503_with_backoff() -> None:
    n = Counter[str]()

    async def h(req: httpx.Request) -> httpx.Response:
        n["x"] += 1
        return httpx.Response(503 if n["x"] < 3 else 200, text="ok")

    res, sleep = run_fetch(h)
    assert res.ok and res.attempts == 3
    assert len(sleep.calls) == 2
    assert 0.5 <= sleep.calls[0] <= 1.5 and 1.0 <= sleep.calls[1] <= 2.0  # 0.5*2^k + jitter


def test_fetch_honors_retry_after() -> None:
    n = Counter[str]()

    async def h(req: httpx.Request) -> httpx.Response:
        n["x"] += 1
        if n["x"] == 1:
            return httpx.Response(429, headers={"Retry-After": "2"})
        return httpx.Response(200, text="ok")

    res, sleep = run_fetch(h)
    assert res.ok and res.attempts == 2
    assert sleep.calls == [2.0]  # рівно стільки, скільки просив сервер


def test_fetch_gives_up_on_huge_retry_after() -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "3600"})

    res, sleep = run_fetch(h)
    assert not res.ok and res.error == "HTTP 429" and res.attempts == 1 and sleep.calls == []


@pytest.mark.parametrize("status", [400, 403, 404, 410])
def test_fetch_no_retry_on_4xx(status: int) -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    res, sleep = run_fetch(h)
    assert res.status == status and res.attempts == 1 and not res.ok and sleep.calls == []


def test_fetch_max_3_attempts() -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    res, sleep = run_fetch(h)
    assert res.attempts == 3 and res.error == "HTTP 500" and len(sleep.calls) == 2


def test_fetch_timeout_is_retried() -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)  # довше за таймаут
        return httpx.Response(200)

    start = time.perf_counter()
    res, _ = run_fetch(h, timeout=0.05)
    assert res.error == "timeout" and res.status == 0 and res.attempts == 3
    assert time.perf_counter() - start < 0.9  # asyncio.timeout справді обірвав запит


def test_fetch_connect_error_is_retried() -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=req)

    res, sleep = run_fetch(h)
    assert res.error == "ConnectError" and res.attempts == 3 and len(sleep.calls) == 2


def test_fetch_redirect_loop_not_retried() -> None:
    async def h(req: httpx.Request) -> httpx.Response:
        nxt = "/b" if req.url.path == "/a" else "/a"
        return httpx.Response(302, headers={"Location": nxt})

    async def go() -> FetchResult:
        async with client_for(h) as client:
            return await fetch(client, "http://h.com/a", sleep=FakeSleep())

    res = asyncio.run(go())
    assert res.error == "redirect loop" and res.attempts == 1


# ---------- robots ----------


def test_robots() -> None:
    seen = Counter[str]()

    async def h(req: httpx.Request) -> httpx.Response:
        seen[req.url.path] += 1
        if req.url.host == "a.com":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/\nCrawl-delay: 3\n")
        if req.url.host == "b.com":
            return httpx.Response(404)
        return httpx.Response(503)

    async def go() -> tuple[list[bool], list[bool], float | None]:
        async with client_for(h) as client:
            rc = RobotsCache(client, sleep=FakeSleep())
            # 10 одночасних питань про новий хост -> robots.txt качається ОДИН раз
            many = await asyncio.gather(*(rc.allowed("http://a.com/x") for _ in range(10)))
            other = [
                await rc.allowed("http://a.com/private/secret"),
                await rc.allowed("http://b.com/anything"),
                await rc.allowed("http://c.com/anything"),
            ]
            assert rc.fetches == 3
            return many, other, await rc.crawl_delay("http://a.com/")

    many, other, delay = asyncio.run(go())
    assert all(many)
    assert other == [False, True, False]  # disallow / 404 = можна все / 503 = нічого
    assert seen["/robots.txt"] == 1 + 1 + 3  # c.com: 503 тричі (повтори)
    assert delay == 3.0


# ---------- crawl ----------

SITE = {
    "a.com": {
        "/robots.txt": "User-agent: *\nDisallow: /private/\n",
        "/": '<title>Home</title><a href="/p1">1</a><a href="/p2#top">2</a><a href="/p2/">2</a>'
        '<a href="/private/x">secret</a><a href="http://evil.org/">ext</a>'
        '<a href="http://b.a.com/">sub</a><a href="/old">old</a><a href="/missing">m</a>',
        "/p1": '<title>P1</title><p>whale ship</p><a href="/">home</a><a href="/p2?b=1&a=2">q</a>',
        "/p2": '<title>P2</title><p>white whale</p><a href="/p1">1</a><a href="/p3">3</a>',
        "/p2?a=2&b=1": "<title>P2q</title><p>query page</p>",
        "/p3": "<title>P3</title><p>sea</p>",
        "/new": "<title>New</title><p>moved here</p>",
    },
    "b.a.com": {"/robots.txt": "", "/": "<title>B</title><p>sub site</p>"},
}


def site_handler(log: list[str]) -> Handler:
    async def h(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        path = req.url.path + (f"?{req.url.query.decode()}" if req.url.query else "")
        log.append(f"{host}{path}")
        if path == "/old":
            return httpx.Response(301, headers={"Location": "/new"})
        pages = SITE.get(host, {})
        if path not in pages:
            return httpx.Response(404, text="nope")
        ctype = "text/plain" if path == "/robots.txt" else "text/html"
        return httpx.Response(200, text=pages[path], headers={"Content-Type": ctype})

    return h


def run_crawl(
    max_pages: int = 100,
    stop_after: int | None = None,
    concurrency: int = 10,
    per_host: int = 2,
    allowed_domains: list[str] | None = None,
) -> tuple[list[Page], list[str], CrawlStats]:
    log: list[str] = []
    stats = CrawlStats()

    async def go() -> list[Page]:
        out: list[Page] = []
        async with client_for(site_handler(log)) as client:
            gen = crawl(
                ["http://a.com/"],
                max_pages=max_pages,
                concurrency=concurrency,
                per_host=per_host,
                allowed_domains=allowed_domains,
                delay=0,
                client=client,
                stats=stats,
            )
            async for page in gen:
                out.append(page)
                if stop_after is not None and len(out) >= stop_after:
                    break
            await gen.aclose()
        return out

    return asyncio.run(go()), log, stats


def test_crawl_all_pages_no_duplicates() -> None:
    pages, log, stats = run_crawl(concurrency=4, per_host=2, allowed_domains=["a.com"])
    urls = {p.url for p in pages}
    assert urls == {
        "http://a.com/",
        "http://a.com/p1",
        "http://a.com/p2",
        "http://a.com/p3",
        "http://a.com/p2?a=2&b=1",
        "http://a.com/new",
        "http://b.a.com/",
    }
    fetched = [x for x in log if not x.endswith("/robots.txt")]
    assert len(fetched) == len(set(fetched))  # жодну адресу не качали двічі
    assert not any("private" in x for x in log)  # robots.txt
    assert not any("evil.org" in x for x in log)  # чужий домен
    assert stats.pages == 7 and stats.errors == 1 and stats.skipped == 1  # 404 і robots
    p2 = next(p for p in pages if p.url == "http://a.com/p2")
    assert p2.title == "P2" and p2.text == "white whale\n\n13"  # "13" - тексти посилань


def test_crawl_default_domain_is_seed_host() -> None:
    pages, _, _ = run_crawl()
    assert "http://b.a.com/" in {p.url for p in pages}  # піддомен a.com дозволений


def test_crawl_max_pages() -> None:
    pages, _, stats = run_crawl(max_pages=3, concurrency=4, per_host=4)
    assert len(pages) == 3 and stats.pages == 3


def test_crawl_consumer_can_stop_early() -> None:
    pages, _, _ = run_crawl(stop_after=2)
    assert len(pages) == 2  # break з async for -> генератор закрився, воркери скасовані


def test_crawl_bad_seed() -> None:
    async def go() -> None:
        async for _ in crawl(["mailto:x@y.z"]):
            pass

    with pytest.raises(ValueError):
        asyncio.run(go())


def test_crawl_per_host_delay() -> None:
    times: list[float] = []

    async def h(req: httpx.Request) -> httpx.Response:
        times.append(time.monotonic())
        if req.url.path == "/robots.txt":
            return httpx.Response(404)
        links = "".join(f'<a href="/{i}">x</a>' for i in range(5))
        return httpx.Response(200, html=f"<title>t</title>{links}")

    async def go() -> None:
        async with client_for(h) as client:
            async for _ in crawl(
                ["http://d.com/"], max_pages=4, concurrency=4, per_host=4, delay=0.05, client=client
            ):
                pass

    asyncio.run(go())
    page_times = sorted(times[1:])  # без robots.txt
    gaps = [b - a for a, b in zip(page_times, page_times[1:], strict=False)]
    assert all(g >= 0.04 for g in gaps)  # не частіше, ніж раз на delay


def test_crawl_no_blocking_in_debug_mode(caplog: pytest.LogCaptureFixture) -> None:
    """asyncio debug=True пише warning, якщо крок корутини блокує цикл > slow_callback_duration."""

    async def go() -> None:
        asyncio.get_running_loop().slow_callback_duration = 0.05
        async with client_for(site_handler([])) as client:
            async for _ in crawl(["http://a.com/"], delay=0, client=client):
                pass

    with caplog.at_level("WARNING", logger="asyncio"):
        asyncio.run(go(), debug=True)
    assert "took" not in caplog.text


# ---------- CLI + індексація JSONL ----------


def test_cli_crawl_then_index_and_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_mod, "make_client", lambda c=10, t=10.0: client_for(site_handler([])))
    out = tmp_path / "crawl.jsonl"
    result = CliRunner().invoke(app, ["crawl", "http://a.com/", "--delay", "0", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "7 pages" in result.stdout
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 7 and {"url", "title", "text", "fetched_at"} <= set(lines[0])
    log = (tmp_path / "crawl.log").read_text(encoding="utf-8")
    assert "robots.txt disallow" in log and "\t404\t" in log and "-> http://a.com/new" in log

    ix_path = tmp_path / "ix.bin"
    result = CliRunner().invoke(
        app, ["index", str(out), "-o", str(ix_path), "--executor", "threads"]
    )
    assert result.exit_code == 0, result.output
    result = CliRunner().invoke(app, ["search", str(ix_path), "whale", "--json"])
    rows = [json.loads(x) for x in result.stdout.splitlines()]
    assert {r["title"].split(" #")[0] for r in rows} == {"P1", "P2"}
    assert any(r["snippet"] == "white whale" for r in rows)


def test_jsonl_index_parallel_equals_serial(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    pages = [
        {"url": f"u{i}", "title": f"T{i}", "text": f"page {i}\n\nwhale {i % 3}"} for i in range(40)
    ]
    path.write_text("".join(json.dumps(p) + "\n" for p in pages), encoding="utf-8")
    ref = build_index(path)
    assert build_index_parallel(path, "threads", 3, chunk_kb=1) == ref
    res = Searcher(ref).search("whale 2", k=1)[0]
    assert doc_text(ref.doc_meta[res.doc_id]) == "whale 2"
