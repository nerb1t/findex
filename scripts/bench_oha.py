"""M4: навантаження через oha. Для кожного рядка піднімає свій сервер.

uv run python scripts/bench_oha.py --oha path/to/oha.exe
uv run python scripts/bench_oha.py --oha oha --url https://findex.onrender.com -c 20   (розгорнутий)
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

QUERY = "/search?q=the%20sea"

LOCAL = [
    # (назва, SEARCH_MODE, workers)
    ("local, 1 worker, async def", "async", 1),
    ("local, 1 worker, def", "def", 1),
    ("local, 4 workers, def", "def", 4),
]


def oha(exe: str, url: str, conc: int, duration: str) -> dict[str, float]:
    cmd = [exe, "-z", duration, "-c", str(conc), "--no-tui", "--output-format", "json", url]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    data = json.loads(out)
    pct = data["latencyPercentiles"]
    codes = data.get("statusCodeDistribution", {})
    return {
        "rps": data["summary"]["requestsPerSec"],
        "p50": pct["p50"] * 1000,
        "p95": pct["p95"] * 1000,
        "p99": pct["p99"] * 1000,
        "ok": data["summary"]["successRate"],
        "codes": codes,
    }


def wait_ready(base: str) -> None:
    for _ in range(300):
        try:
            with urllib.request.urlopen(base + "/health", timeout=1) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.2)
    raise RuntimeError("server did not start")


def row(name: str, conc: int, r: dict[str, float]) -> str:
    return (
        f"| {name} | `{QUERY}` | {conc} | {r['rps']:.0f} | {r['p50']:.0f} | {r['p95']:.0f} "
        f"| {r['p99']:.0f} | {r['codes']} |"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--oha", default="oha")
    parser.add_argument("--index", default="index_pos.bin")
    parser.add_argument("--url", help="розгорнутий сервіс замість локальних")
    parser.add_argument("-c", type=int, default=50)
    parser.add_argument("-z", default="30s")
    args = parser.parse_args()

    print("| Target | Route | Concurrency | RPS | p50, ms | p95 | p99 | статуси |")
    print("|---|---|---:|---:|---:|---:|---:|---|")
    if args.url:
        base = args.url.rstrip("/")
        oha(args.oha, base + QUERY, 2, "5s")  # розбудити (free-план засинає)
        print(row("deployed", args.c, oha(args.oha, base + QUERY, args.c, args.z)), flush=True)
        return

    for i, (name, mode, workers) in enumerate(LOCAL):
        port = 8200 + i
        env = {**os.environ, "SEARCH_MODE": mode, "LOG_LEVEL": "WARNING"}
        cmd = [
            sys.executable,
            "-m",
            "findex",
            "serve",
            "--index",
            str(Path(args.index)),
            "--port",
            str(port),
            "--workers",
            str(workers),
            "--no-access-log",
        ]
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            wait_ready(base)
            time.sleep(8 if workers > 1 else 1)  # кожен воркер вантажить свій індекс
            oha(args.oha, base + QUERY, args.c, "5s")  # розігрів
            print(row(name, args.c, oha(args.oha, base + QUERY, args.c, args.z)), flush=True)
        finally:
            proc.terminate()
            proc.wait()


if __name__ == "__main__":
    main()
