"""M4: бенчмарк краулера на локальному дзеркалі (scripts/local_site.py має вже працювати).

uv run python scripts/local_site.py --chaos          (в іншому терміналі)
uv run python scripts/bench_crawl.py --max-pages 300

Кожен запуск - окремий процес (свій пік RSS).
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

SEED = "http://127.0.0.1:8001/"

# (concurrency, per_host, delay)
CONFIGS = [
    (1, 1, 0.0),
    (5, 5, 0.0),
    (20, 20, 0.0),
    # ціна ввічливості: та сама concurrency 20, але з лімітами на хост
    (20, 5, 0.02),
    (20, 2, 0.25),
]


def one(max_pages: int, concurrency: int, per_host: int, delay: float) -> dict[str, float]:
    import psutil

    from findex.crawler.crawl import CrawlStats, crawl

    async def main() -> CrawlStats:
        stats = CrawlStats()
        with tempfile.TemporaryFile("w", encoding="utf-8") as f:
            pages = crawl(
                [SEED],
                max_pages=max_pages,
                concurrency=concurrency,
                per_host=per_host,
                delay=delay,
                timeout=2.0,
                stats=stats,
            )
            async for page in pages:
                f.write(json.dumps(page.__dict__) + "\n")
        return stats

    st = asyncio.run(main())
    return {
        "pages": st.pages,
        "wall": st.elapsed,
        "errors": st.errors,
        "requests": st.requests,
        "peak_rss_mb": psutil.Process().memory_info().peak_wset / 2**20,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--one", nargs=3, metavar=("C", "PER_HOST", "DELAY"))
    args = parser.parse_args()

    if args.one:
        c, h, d = args.one
        print(json.dumps(one(args.max_pages, int(c), int(h), float(d))))
        return

    rows = []
    for c, h, d in CONFIGS:
        runs = []
        for _ in range(args.repeat):
            cmd = [
                sys.executable,
                __file__,
                "--max-pages",
                str(args.max_pages),
                "--one",
                str(c),
                str(h),
                str(d),
            ]
            out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
            runs.append(json.loads(out.strip().splitlines()[-1]))
        walls = [r["wall"] for r in runs]
        wall = statistics.median(walls)
        row = {
            "concurrency": c,
            "per_host": h,
            "delay": d,
            "pages": runs[0]["pages"],
            "wall": wall,
            "wall_min": min(walls),
            "wall_max": max(walls),
            "pps": runs[0]["pages"] / wall,
            "errors": statistics.median(r["errors"] for r in runs),
            "requests": statistics.median(r["requests"] for r in runs),
            "rss": statistics.median(r["peak_rss_mb"] for r in runs),
        }
        rows.append(row)
        print(row, flush=True)

    Path("results").mkdir(exist_ok=True)
    Path("results/crawl.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print()
    print(
        "| Concurrency | per-host | delay, s | Pages | Wall, s (median) | min–max | Pages/s | Errors | Requests | Peak RSS |"
    )
    print("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(
            f"| {r['concurrency']} | {r['per_host']} | {r['delay']} | {r['pages']} | {r['wall']:.1f} "
            f"| {r['wall_min']:.1f}–{r['wall_max']:.1f} | {r['pps']:.1f} | {r['errors']:.0f} "
            f"| {r['requests']:.0f} | {r['rss']:.0f} MB |"
        )


if __name__ == "__main__":
    main()
