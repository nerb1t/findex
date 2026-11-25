"""Протилежний дослід: I/O-bound робота, де потоки масштабуються навіть з GIL.

Піднімаємо локальний HTTP-сервер, який на кожен запит "думає" 100 мс
(як повільний сайт), і качаємо 48 сторінок пулом потоків з різною кількістю воркерів.
Поки потік чекає відповідь у socket.recv(), він ВІДПУСКАЄ GIL.

uv run python scripts/bench_io.py
"""

import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DELAY = 0.1
N_URLS = 48


class SlowHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        time.sleep(DELAY)  # імітація мережі / повільного сервера
        body = b"x" * 10_000
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass  # без логів на кожен запит


def fetch(url: str) -> int:
    with urllib.request.urlopen(url) as r:
        return len(r.read())


def main() -> None:
    # request_queue_size за замовчуванням 5: при 16+ одночасних з'єднаннях Windows
    # відкидає зайві, і клієнт перепідключається через ~0.5 s. Тому ставимо 128
    ThreadingHTTPServer.request_queue_size = 128
    server = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    urls = [f"http://127.0.0.1:{server.server_port}/page/{i}" for i in range(N_URLS)]
    fetch(urls[0])  # розігрів

    print(f"{N_URLS} запитів по {DELAY * 1000:.0f} мс, потоки (cpu_count = {os.cpu_count()})")
    print("| Workers | Wall, s | Speedup | Ideal |")
    print("|---:|---:|---:|---:|")
    base = 0.0
    for workers in (1, 2, 4, 8, 16, 48):
        times = []
        for _ in range(3):
            start = time.perf_counter()
            with ThreadPoolExecutor(workers) as pool:
                total = sum(pool.map(fetch, urls))
            times.append(time.perf_counter() - start)
            assert total == N_URLS * 10_000
        t = sorted(times)[1]  # медіана з 3
        base = base or t
        print(f"| {workers} | {t:.2f} | {base / t:.1f}× | {min(workers, N_URLS)}× |")
    server.shutdown()


if __name__ == "__main__":
    main()
