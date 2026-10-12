"""
Retrieval evaluation with Anchor's metrics.

A question set is {"questions": [{"query", "relevant_urls"}]}. Each query
is searched, the ranked chunks are collapsed to ranked articles, and
Anchor computes Recall@k, MRR, nDCG@k and MAP against the relevant
articles. Pure math, no model call, so a tokenizer or embedder change can
be compared with numbers. Results can also be written as Anchor records
(`anchor score` replays them into its traces and dashboard).
"""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from .models import article_id
from .retrieve import Index


def ranked_articles(index: Index, query: str, k: int) -> list[str]:
    seen: dict[str, None] = {}
    for h in index.search(query, k=k * 5):
        seen.setdefault(h.chunk.article_id, None)
    return list(seen)[:k]


def evaluate_retrieval(index: Index, questions_path: str | Path, k: int = 5, records_out: str | Path | None = None) -> dict:
    from anchor.retrieval_metrics import all_retrieval_metrics

    data = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    rows, records = [], []
    for q in data["questions"]:
        relevant = [article_id(u) for u in q["relevant_urls"]]
        ranked = ranked_articles(index, q["query"], k)
        m = all_retrieval_metrics(ranked, relevant, k)
        rows.append({"query": q["query"], **m.model_dump()})
        if records_out:
            records.append({"query": q["query"], "answer": "",
                            "retrieved_chunks": [{"id": a, "text": getattr(index.store.article(a), "title", "")} for a in ranked],
                            "reference_chunk_ids": relevant})
    if records_out:
        Path(records_out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    keys = ("recall_at_k", "mrr", "ndcg_at_k", "map_score", "hit_at_k")
    return {"embedder": index.embedder.name, "k": k, "n": len(rows),
            "mean": {key: round(mean(r[key] for r in rows), 4) for key in keys} if rows else {},
            "per_question": rows}
