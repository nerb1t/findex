"""M2: /search як def чи як async def? 20 одночасних запитів + /health під час них.

uv run python scripts/bench_web.py --index index_pos.bin --query the

Для кожного режиму піднімає окремий сервер (SEARCH_MODE=def/async, 1 воркер).
"""

import argparse
import asyncio
import os
import statistics
import subprocess
import sys
import time

import httpx


async def wait_ready(base: str) -> None:
    async with httpx.AsyncClient() as c:
        for _ in range(300):
            try:
                if (await c.get(base + "/health")).status_code == 200:
                    return
            except httpx.TransportError:
                pass
            await asyncio.sleep(0.1)
    raise RuntimeError("server did not start")


async def burst(base: str, query: str, n: int) -> tuple[float, list[float], list[float]]:
    """n одночасних /search; паралельно кожні 10 мс питаємо /health."""
    lat: list[float] = []
    health: list[float] = []
    done = asyncio.Event()
    limits = httpx.Limits(max_connections=n + 5)
    async with httpx.AsyncClient(limits=limits, timeout=60) as c:

        async def one() -> None:
            t = time.perf_counter()
            r = await c.get(base + "/search", params={"q": query})
            r.raise_for_status()
            lat.append((time.perf_counter() - t) * 1000)

        async def probe() -> None:
            while not done.is_set():
                t = time.perf_counter()
                await c.get(base + "/health")
                health.append((time.perf_counter() - t) * 1000)
                await asyncio.sleep(0.01)

        prober = asyncio.create_task(probe())
        start = time.perf_counter()
        async with asyncio.TaskGroup() as tg:
            for _ in range(n):
                tg.create_task(one())
        wall = (time.perf_counter() - start) * 1000
        done.set()
        await prober
    return wall, lat, health


def run_mode(mode: str, index: str, query: str, n: int, repeat: int, port: int) -> dict[str, float]:
    env = {**os.environ, "SEARCH_MODE": mode, "LOG_LEVEL": "WARNING"}
    cmd = [
        sys.executable,
        "-m",
        "findex",
        "serve",
        "--index",
        index,
        "--port",
        str(port),
        "--no-access-log",
    ]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        asyncio.run(wait_ready(base))
        asyncio.run(burst(base, query, n))  # розігрів (кеш запиту, файли книг)
        walls, p50s, maxs, h_max, h_p50 = [], [], [], [], []
        for _ in range(repeat):
            wall, lat, health = asyncio.run(burst(base, query, n))
            walls.append(wall)
            p50s.append(statistics.median(lat))
            maxs.append(max(lat))
            h_max.append(max(health))
            h_p50.append(statistics.median(health))
        return {
            "wall": statistics.median(walls),
            "p50": statistics.median(p50s),
            "max": statistics.median(maxs),
            "health_p50": statistics.median(h_p50),
            "health_max": statistics.median(h_max),
        }
    finally:
        proc.terminate()
        proc.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", default="index_pos.bin")
    parser.add_argument("--query", default="the")
    parser.add_argument("-n", type=int, default=20)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()

    print(
        f"{args.n} одночасних GET /search?q={args.query}, медіана з {args.repeat} серій, 1 воркер"
    )
    print()
    print(
        "| /search | 20 запитів разом, мс | p50 запиту, мс | найдовший, мс | /health p50, мс | /health max, мс |"
    )
    print("|---|---:|---:|---:|---:|---:|")
    for i, mode in enumerate(("def", "async")):
        r = run_mode(mode, args.index, args.query, args.n, args.repeat, 8100 + i)
        name = "def" if mode == "def" else "async def"
        print(
            f"| `{name}` | {r['wall']:.0f} | {r['p50']:.0f} | {r['max']:.0f} | {r['health_p50']:.1f} | {r['health_max']:.0f} |",
            flush=True,
        )


if __name__ == "__main__":
    main()
