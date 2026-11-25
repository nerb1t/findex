"""M3: бенчмарк паралельної індексації.

Кожен запуск - окремий процес (щоб peak RSS і кеші одного запуску не впливали на інший).

uv run python scripts/bench_parallel.py run data/ --out results/gil.json
.venv-313t/Scripts/python.exe scripts/bench_parallel.py run data/ --out results/ft313.json
uv run python scripts/bench_parallel.py report results/*.json      -> таблиця + PNG

Всередині (режим one) робимо те саме, що build_index_parallel, але руками,
щоб встигнути поміряти CPU воркерів до того, як пул їх закриє.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))  # щоб працювало і з python3.13t без встановлення

WORKERS = [1, 2, 4, 8, os.cpu_count() or 1]


def one(corpus: Path, executor: str, workers: int, kind: str) -> dict[str, object]:
    import psutil

    from findex.parallel import make_executor, make_tasks, merge, run_tasks

    me = psutil.Process()
    paths = sorted(corpus.rglob("*.txt"))
    start = time.perf_counter()
    tasks = make_tasks(paths)
    pool = make_executor(executor, workers)  # type: ignore[arg-type]
    partials = run_tasks(tasks, pool, kind)  # type: ignore[arg-type]
    map_time = time.perf_counter() - start
    children_cpu = 0.0
    children_rss = 0
    for child in me.children(recursive=True):  # живі воркери, поки пул не закритий
        t = child.cpu_times()
        children_cpu += t.user + t.system
        children_rss += child.memory_info().rss
    if pool is not None:
        pool.shutdown()
    t0 = time.perf_counter()
    index = merge(partials, kind)  # type: ignore[arg-type]
    merge_time = time.perf_counter() - t0
    wall = time.perf_counter() - start
    t = me.cpu_times()
    gil = sys._is_gil_enabled() if hasattr(sys, "_is_gil_enabled") else True
    return {
        "python": sys.version.split()[0] + ("" if gil else "t"),
        "gil": gil,
        "executor": executor,
        "workers": workers,
        "kind": kind,
        "wall": wall,
        "map": map_time,
        "merge": merge_time,
        "cpu": t.user + t.system + children_cpu,
        "peak_rss_mb": me.memory_info().peak_wset / 2**20,  # Windows: peak working set
        "workers_rss_mb": children_rss / 2**20,
        "docs": index.num_docs,
    }


def run(args: argparse.Namespace) -> None:
    configs: list[tuple[str, int, str]] = [("serial", 1, "slots")]
    configs += [("threads", w, "slots") for w in WORKERS]
    if not args.threads_only:
        configs += [("processes", w, "slots") for w in WORKERS]
        configs += [("processes", w, "array") for w in WORKERS]

    def call(executor: str, workers: int, kind: str) -> dict[str, object]:
        cmd = [sys.executable, __file__, "one", str(args.corpus), executor, str(workers), kind]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
        return json.loads(out.strip().splitlines()[-1])

    call("serial", 1, "slots")  # розігрів: файли в кеші ОС, .pyc скомпільовані
    rows: list[dict[str, object]] = []
    for executor, workers, kind in configs:
        runs = [call(executor, workers, kind) for _ in range(args.repeat)]
        walls = [float(r["wall"]) for r in runs]  # type: ignore[arg-type]
        row = dict(runs[0])
        for key in ("wall", "map", "merge", "cpu", "peak_rss_mb", "workers_rss_mb"):
            row[key] = statistics.median(float(r[key]) for r in runs)  # type: ignore[arg-type]
        row["wall_min"] = min(walls)
        row["wall_max"] = max(walls)
        rows.append(row)
        print(
            f"{row['python']:9} {executor:9} {kind:5} w={workers:2}  wall {row['wall']:.2f} s"
            f" ({min(walls):.2f}-{max(walls):.2f})  merge {row['merge']:.2f}  cpu {row['cpu']:.1f}",
            flush=True,
        )
    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")


def report(files: list[Path], png: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows: list[dict[str, object]] = []
    for f in files:
        rows += json.loads(f.read_text(encoding="utf-8"))
    base = {r["python"]: float(r["wall"]) for r in rows if r["executor"] == "serial"}  # type: ignore[arg-type]
    gil_base = next(float(r["wall"]) for r in rows if r["executor"] == "serial" and r["gil"])  # type: ignore[arg-type]

    print(
        "| Python | Executor | kind | Workers | Wall, s (median) | min-max | CPU, s | Peak RSS батька | RSS воркерів | Merge, s | Speedup |"
    )
    print("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        speed = gil_base / float(r["wall"])  # type: ignore[arg-type]
        print(
            f"| {r['python']} | {r['executor']} | {r['kind']} | {r['workers']} | {float(r['wall']):.2f} "  # type: ignore[arg-type]
            f"| {float(r['wall_min']):.2f}-{float(r['wall_max']):.2f} | {float(r['cpu']):.1f} "  # type: ignore[arg-type]
            f"| {float(r['peak_rss_mb']):.0f} MB | {float(r['workers_rss_mb']):.0f} MB "  # type: ignore[arg-type]
            f"| {float(r['merge']):.2f} | {speed:.2f}× |"
        )
    print("\nserial baseline per python:", {k: round(v, 2) for k, v in base.items()})

    fig, ax = plt.subplots(figsize=(7, 4.5))
    series: dict[str, list[tuple[int, float]]] = {}
    for r in rows:
        if r["executor"] == "serial":
            continue
        label = f"{r['executor']} ({r['kind']}), Python {r['python']}"
        series.setdefault(label, []).append((int(r["workers"]), gil_base / float(r["wall"])))  # type: ignore[arg-type]
    for label, pts in series.items():
        pts.sort()
        ax.plot([p[0] for p in pts], [p[1] for p in pts], marker="o", label=label)
    xs = sorted({w for pts in series.values() for w, _ in pts})
    ax.plot(xs, xs, "k--", linewidth=1, label="ideal (linear)")
    ax.set_xscale("log", base=2)
    ax.set_xticks(xs, [str(x) for x in xs])
    ax.set_ylim(0, max(4.0, max(s for pts in series.values() for _, s in pts) * 1.3))
    ax.set_xlabel("workers")
    ax.set_ylabel("speedup vs serial (Python 3.13, GIL)")
    ax.set_title("findex index: speedup vs workers (i7-11700K, 8C/16T)")
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(png, dpi=120)
    print(f"plot -> {png}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_one = sub.add_parser("one")
    p_one.add_argument("corpus", type=Path)
    p_one.add_argument("executor")
    p_one.add_argument("workers", type=int)
    p_one.add_argument("kind")
    p_run = sub.add_parser("run")
    p_run.add_argument("corpus", type=Path)
    p_run.add_argument("--out", type=Path, required=True)
    p_run.add_argument("--repeat", type=int, default=3)
    p_run.add_argument("--threads-only", action="store_true")
    p_rep = sub.add_parser("report")
    p_rep.add_argument("files", type=Path, nargs="+")
    p_rep.add_argument("--png", type=Path, default=ROOT / "docs" / "speedup.png")
    args = parser.parse_args()

    if args.cmd == "one":
        print(json.dumps(one(args.corpus, args.executor, args.workers, args.kind)))
    elif args.cmd == "run":
        run(args)
    else:
        args.png.parent.mkdir(exist_ok=True)
        report(args.files, args.png)


if __name__ == "__main__":  # обов'язково для ProcessPoolExecutor зі spawn
    main()
