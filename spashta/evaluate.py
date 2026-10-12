"""
Retrieval evaluation with Anchor's metrics.

A question set is {"questions": [{"query", "relevant_urls", "tags"?}]}.
Each query is searched, the ranked chunks are collapsed to ranked
articles, and Anchor computes Recall@k, MRR, nDCG@k and MAP against the
relevant articles. Pure math, no model call, so a tokenizer or embedder
change can be compared with numbers.

compare_modes() runs the same questions through lexical-only, dense-only
and hybrid search and reports Anchor's paired significance test between
them, so "hybrid is better" is a measured claim. Results can also be
written as Anchor records (`anchor score` replays them into its dashboard).
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from .models import article_id
from .retrieve import MODES, Index

METRICS = ("recall_at_k", "mrr", "ndcg_at_k", "map_score", "hit_at_k")


def ranked_articles(index: Index, query: str, k: int, mode: str = "hybrid") -> list[str]:
    seen: dict[str, None] = {}
    for h in index.search(query, k=k * 5, mode=mode):
        seen.setdefault(h.chunk.article_id, None)
    return list(seen)[:k]


def _means(rows: list[dict]) -> dict:
    return {key: round(mean(r[key] for r in rows), 4) for key in METRICS} if rows else {}


def evaluate_retrieval(index: Index, questions_path: str | Path, k: int = 5, records_out: str | Path | None = None,
                       mode: str = "hybrid") -> dict:
    from anchor.retrieval_metrics import all_retrieval_metrics

    data = json.loads(Path(questions_path).read_text(encoding="utf-8"))
    rows, records = [], []
    for q in data["questions"]:
        relevant = [article_id(u) for u in q["relevant_urls"]]
        ranked = ranked_articles(index, q["query"], k, mode)
        m = all_retrieval_metrics(ranked, relevant, k)
        rows.append({"query": q["query"], "tags": q.get("tags", []), **m.model_dump()})
        if records_out:
            records.append({"query": q["query"], "answer": "",
                            "retrieved_chunks": [{"id": a, "text": getattr(index.store.article(a), "title", "")} for a in ranked],
                            "reference_chunk_ids": relevant})
    if records_out:
        Path(records_out).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    by_tag: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        for t in r["tags"]:
            by_tag[t].append(r)
    return {"embedder": index.embedder.name, "mode": mode, "k": k, "n": len(rows), "mean": _means(rows),
            "by_tag": {t: {"n": len(rs), **_means(rs)} for t, rs in sorted(by_tag.items())},
            "per_question": rows}


def compare_modes(index: Index, questions_path: str | Path, k: int = 5, metric: str = "ndcg_at_k") -> dict:
    from anchor.stats import paired_test

    results = {m: evaluate_retrieval(index, questions_path, k, mode=m) for m in MODES}
    per = {m: [r[metric] for r in results[m]["per_question"]] for m in MODES}
    tests = {f"hybrid vs {m}": paired_test(per[m], per["hybrid"]) for m in MODES if m != "hybrid"}
    return {"metric": metric, "k": k, "embedder": index.embedder.name,
            "mean": {m: results[m]["mean"] for m in MODES},
            "by_tag": {m: results[m]["by_tag"] for m in MODES},
            "significance": {name: {key: t[key] for key in ("mean_delta", "ci_low", "ci_high", "p_value", "significant")}
                             for name, t in tests.items()}}
