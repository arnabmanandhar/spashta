"""
Indexing and query speed, and memory, on a synthetic or a real corpus.

Synthetic (default) recombines sentences from the bundled samples, so the
vocabulary is realistic Nepali and English:

    python benchmarks/bench_retrieval.py --articles 2000 --batch 50

Real: index a frozen snapshot from scratch, as polling would (batches),
then time the questions you evaluate with:

    python benchmarks/bench_retrieval.py --snapshot snapshots/2026-10 --queries questions.json --embedder e5-small --json

Reports articles, chunks, indexing time (total and per 1,000 articles),
query latency per mode (median, p95, p99), the database size on disk,
peak Python memory (tracemalloc, with --trace-memory), the size of the vector matrix, and
peak process memory where the platform reports it (resource on
Linux/macOS, psutil if installed on Windows).
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time
import tracemalloc

from spashta import lang as L
from spashta.embed import HashingEmbedder, get_embedder
from spashta.models import Article
from spashta.retrieve import MODES, Index
from spashta.samples import sample_articles
from spashta.store import Store


def synthetic(n: int, seed: int = 7) -> list[Article]:
    rng = random.Random(seed)
    pool = [s for a in sample_articles() for s in L.sentences(a.body)]
    titles = [a.title for a in sample_articles()]
    out = []
    for i in range(n):
        body = "\n\n".join(rng.sample(pool, 6))
        out.append(Article(url=f"https://bench{i % 40}.example/news/{i}", title=f"{rng.choice(titles)} {i}", body=body,
                           source=f"bench{i % 40}.example", lang=L.detect_language(body)))
    return out


def peak_rss_mb() -> float | None:
    try:
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    except ImportError:
        try:
            import psutil
            info = psutil.Process().memory_info()
            return round(getattr(info, "peak_wset", info.rss) / 2**20, 1)
        except ImportError:
            return None


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))] * 1000, 1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--articles", type=int, default=2000, help="synthetic corpus size")
    p.add_argument("--snapshot", help="index this snapshot instead of a synthetic corpus")
    p.add_argument("--queries", help="questions file to time (default: the sample headlines)")
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--embedder", default="hashing")
    p.add_argument("--modes", nargs="+", default=["hybrid"], choices=MODES)
    p.add_argument("--repeat", type=int, default=3, help="passes over the queries")
    p.add_argument("--json", action="store_true")
    p.add_argument("--trace-memory", action="store_true", help="also measure peak Python memory (slows indexing 2-3x)")
    args = p.parse_args()

    if args.snapshot:
        from spashta.dataset import snapshot
        arts = snapshot.load(args.snapshot)
    else:
        arts = synthetic(args.articles)
    if args.queries:
        from spashta.evaluate import load_questions
        queries = [q["query"] for q in load_questions(args.queries)]
    else:
        queries = [a.title for a in sample_articles()]

    embedder = HashingEmbedder() if args.embedder == "hashing" else get_embedder(args.embedder)
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, "bench.db")
    if args.trace_memory:
        tracemalloc.start()
    index = Index(Store(db), embedder)
    t = time.perf_counter()
    for i in range(0, len(arts), args.batch):
        index.add_many(arts[i:i + args.batch])
    build = time.perf_counter() - t
    t = time.perf_counter()
    reopened = Index(Store(db), embedder)  # cold start: rebuild the in-memory index from the store
    reload = time.perf_counter() - t
    latencies: dict[str, list[float]] = {m: [] for m in args.modes}
    for _ in range(args.repeat):
        for q in queries:
            for m in args.modes:
                t = time.perf_counter()
                reopened.search(q, k=10, mode=m)
                latencies[m].append(time.perf_counter() - t)
    peak = tracemalloc.get_traced_memory()[1] if args.trace_memory else None
    tracemalloc.stop()
    result = {
        "corpus": args.snapshot or f"synthetic:{args.articles}", "embedder": embedder.name, "articles": len(arts),
        "chunks": len(reopened.chunks), "index_seconds": round(build, 2),
        "index_seconds_per_1k_articles": round(build * 1000 / max(1, len(arts)), 2),
        "reload_seconds": round(reload, 2), "queries": len(queries) * args.repeat,
        "query_ms": {m: {"median": pct(v, 0.5), "p95": pct(v, 0.95), "p99": pct(v, 0.99)} for m, v in latencies.items()},
        "db_mb": round(os.path.getsize(db) / 2**20, 1), "vectors_mb": round(reopened.vectors.nbytes / 2**20, 1),
        "python_peak_mb": round(peak / 2**20, 1) if peak else None, "process_peak_mb": peak_rss_mb(),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        q = result["query_ms"][args.modes[0]]
        print(f"articles={result['articles']} chunks={result['chunks']} build={result['index_seconds']}s "
              f"reload={result['reload_seconds']}s query({args.modes[0]}) median={q['median']}ms p95={q['p95']}ms "
              f"db={result['db_mb']}MB peak={result['process_peak_mb'] or result['python_peak_mb']}MB")


if __name__ == "__main__":
    main()
