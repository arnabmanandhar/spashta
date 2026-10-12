"""
Indexing and query speed on a synthetic corpus.

Builds N articles by recombining sentences from the bundled samples (so
the vocabulary is realistic Nepali and English), then times: adding them
in batches (as polling does), and running queries.

    python benchmarks/bench_retrieval.py --articles 2000 --batch 50
"""

from __future__ import annotations

import argparse
import random
import time

from spashta import lang as L
from spashta.embed import HashingEmbedder
from spashta.models import Article
from spashta.retrieve import Index
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


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--articles", type=int, default=2000)
    p.add_argument("--batch", type=int, default=50)
    p.add_argument("--queries", type=int, default=50)
    args = p.parse_args()
    arts = synthetic(args.articles)
    index = Index(Store(":memory:"), HashingEmbedder())
    t = time.perf_counter()
    for i in range(0, len(arts), args.batch):
        index.add_many(arts[i:i + args.batch])
    build = time.perf_counter() - t
    queries = [a.title for a in sample_articles()] * (args.queries // 17 + 1)
    t = time.perf_counter()
    for q in queries[:args.queries]:
        index.search(q, k=10)
    query = (time.perf_counter() - t) / args.queries
    print(f"articles={len(arts)} chunks={len(index.chunks)} build={build:.2f}s query={query * 1000:.1f}ms")


if __name__ == "__main__":
    main()
