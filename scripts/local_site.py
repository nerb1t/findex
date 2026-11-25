"""Локальне дзеркало для краулера: книги корпусу як HTML-сайт на кількох портах.

Свій сайт - тому дозвіл на обхід є, і ніхто сторонній не отримує навантаження.
Кожен порт - окремий "хост" (окремий сервер для лімітів ввічливості).

uv run python scripts/local_site.py --ports 8001 8002 8003 8004 --chaos

- затримка 50-150 мс на кожну відповідь (як справжня мережа)
- /robots.txt забороняє /private/
- --chaos: 3% запитів дають 503, 2% - 429 (Retry-After: 1),
  /slow.html відповідає 30 s (таймаут), /loop/a <-> /loop/b - зациклений редірект,
  /missing.html - 404
"""

import argparse
import html
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PARAS_PER_PAGE = 30


def load_books(ports: list[int]) -> dict[int, dict[str, list[list[str]]]]:
    """port -> {book_stem: [сторінка = список абзаців]}"""
    site: dict[int, dict[str, list[list[str]]]] = {p: {} for p in ports}
    books = sorted((ROOT / "data" / "gutenberg").glob("*.txt"))
    for i, path in enumerate(books):
        text = path.read_text(encoding="utf-8", errors="replace")
        paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        pages = [paras[j : j + PARAS_PER_PAGE] for j in range(0, len(paras), PARAS_PER_PAGE)]
        site[ports[i % len(ports)]][path.stem] = pages
    return site


def make_handler(
    port: int,
    ports: list[int],
    site: dict[int, dict[str, list[list[str]]]],
    chaos: bool,
    latency: tuple[float, float],
) -> type[BaseHTTPRequestHandler]:
    books = site[port]

    def page(title: str, body: str) -> str:
        return f"<!doctype html><html><head><title>{html.escape(title)}</title></head><body>{body}</body></html>"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive: клієнт може перевикористати з'єднання
        # заголовки і тіло йдуть двома send(); з алгоритмом Нейгла + delayed ACK на Windows
        # кожна відповідь чекала б ~200 мс зайвих. TCP_NODELAY це вимикає
        disable_nagle_algorithm = True

        def send(
            self,
            status: int,
            body: str = "",
            ctype: str = "text/html; charset=utf-8",
            headers: dict[str, str] | None = None,
        ) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:
            path = self.path.split("?")[0].split("#")[0]
            time.sleep(random.uniform(*latency))  # "мережа"

            if path == "/robots.txt":
                return self.send(200, "User-agent: *\nDisallow: /private/\n", "text/plain")
            if chaos:
                if path == "/slow.html":
                    time.sleep(30)
                    return self.send(200, page("slow", "<p>finally</p>"))
                if path == "/loop/a":
                    return self.send(302, headers={"Location": "/loop/b"})
                if path == "/loop/b":
                    return self.send(302, headers={"Location": "/loop/a"})
                # кожен запит (крім robots.txt): 3% - 503, 2% - 429 з Retry-After: 1
                h = random.randrange(100)
                if h < 3:
                    return self.send(503, page("busy", "<p>try later</p>"))
                if h < 5:
                    return self.send(
                        429, page("slow down", "<p>too many</p>"), headers={"Retry-After": "1"}
                    )

            others = "".join(
                f'<li><a href="http://127.0.0.1:{p}/">site {p}</a></li>' for p in ports if p != port
            )
            if path in ("/", "/index.html"):
                items = "".join(f'<li><a href="/books/{b}/">{b}</a></li>' for b in books)
                extra = (
                    '<li><a href="/private/notes.html">private</a></li>'
                    '<li><a href="/missing.html">missing</a></li>'
                )
                if chaos:
                    extra += (
                        '<li><a href="/slow.html">slow</a></li><li><a href="/loop/a">loop</a></li>'
                    )
                return self.send(
                    200,
                    page(
                        f"site {port}",
                        f"<h1>Books on {port}</h1><ul>{items}{extra}</ul><ul>{others}</ul>",
                    ),
                )
            m = re.fullmatch(r"/books/([\w-]+)/?", path)
            if m and m.group(1) in books:
                b = m.group(1)
                items = "".join(
                    f'<li><a href="/books/{b}/{i}.html">part {i}</a></li>'
                    for i in range(len(books[b]))
                )
                return self.send(200, page(b, f"<h1>{b}</h1><ul>{items}</ul>"))
            m = re.fullmatch(r"/books/([\w-]+)/(\d+)\.html", path)
            if m and m.group(1) in books and int(m.group(2)) < len(books[m.group(1)]):
                b, i = m.group(1), int(m.group(2))
                paras = "".join(f"<p>{html.escape(p)}</p>" for p in books[b][i])
                nav = f'<a href="/books/{b}/">index</a> <a href="/books/{b}/{i}.html#top">top</a>'
                if i + 1 < len(books[b]):
                    nav += f' <a href="/books/{b}/{i + 1}.html">next</a>'
                if i > 0:
                    nav += f' <a href="/books/{b}/{i - 1}.html?b=2&amp;a=1">prev</a>'
                return self.send(
                    200,
                    page(f"{b} part {i}", f"<nav>{nav}</nav><script>var x = 1;</script>{paras}"),
                )
            if path.startswith("/private/"):
                return self.send(200, page("private", "<p>you should not see this</p>"))
            return self.send(404, page("not found", "<p>404</p>"))

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ports", type=int, nargs="+", default=[8001, 8002, 8003, 8004])
    parser.add_argument("--chaos", action="store_true")
    parser.add_argument("--latency", type=float, nargs=2, default=[0.05, 0.15])
    args = parser.parse_args()

    site = load_books(args.ports)
    ThreadingHTTPServer.request_queue_size = 256
    ThreadingHTTPServer.daemon_threads = True
    servers = []
    for port in args.ports:
        handler = make_handler(port, args.ports, site, args.chaos, tuple(args.latency))
        srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
    pages = sum(len(v) for books in site.values() for v in books.values())
    print(f"ready: {len(args.ports)} hosts, {pages} book pages, chaos={args.chaos}", flush=True)
    try:
        while True:  # працюємо, поки процес не зупинять (Ctrl-C або terminate)
            time.sleep(3600)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
