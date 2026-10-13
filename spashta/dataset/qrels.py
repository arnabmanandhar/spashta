"""
Pooled relevance judgments for retrieval queries.

Judging only what one method retrieves would favour that method, and
judging the whole corpus per query is impossible. The standard fix
(TREC pooling) is to judge the union of every compared method's top
results: here the top `depth` articles from BM25 (lexical), dense,
cross-language and hybrid search. Annotators see the pooled articles in
random order without knowing which method found them. Articles nobody
retrieved count as not relevant, the usual assumption, and pool_stats()
reports how many relevant articles only one method found, which shows how
much each method relied on the pool.

Queries: 150-200 in Nepali (Devanagari), English and romanised Nepali,
each tagged "lang": "ne" | "en" | "rom". Write them before looking at
search results, from events in the snapshot's window.
"""

from __future__ import annotations

import random
from collections import Counter, defaultdict

from ..evaluate import load_questions, ranked_articles
from ..retrieve import Index

POOL_MODES = ("lexical", "dense", "xling", "hybrid")


def pool(index: Index, queries_path: str, depth: int = 10, modes: tuple[str, ...] = POOL_MODES, seed: int = 13) -> list[dict]:
    rng = random.Random(seed)  # noqa: S311 - reproducible order
    rows = []
    for q in load_questions(queries_path):
        found: dict[str, list[str]] = defaultdict(list)
        for mode in modes:
            for aid in ranked_articles(index, q["query"], depth, mode):
                found[aid].append(mode)
        items = list(found.items())
        rng.shuffle(items)
        for aid, by in items:
            a = index.store.article(aid)
            rows.append({"qid": q["qid"], "query": q["query"], "lang": q.get("lang"), "article_id": aid,
                         "url": a.url if a else "", "found_by": by})
    return rows


def build_questions(queries_path: str, judgments: list[dict], pool_rows: list[dict] | None = None,
                    min_grade: int = 1) -> dict:
    """Questions file for `spashta eval`: each query with the articles
    judged at least min_grade relevant, and every judgment kept."""
    by_q: dict[str, dict[str, int]] = defaultdict(dict)
    for j in judgments:
        if j.get("task", "qrel") == "qrel" and j.get("grade") is not None:
            by_q[j["qid"]][j["article_id"]] = j["grade"]
    urls = {r["article_id"]: r["url"] for r in pool_rows or []}
    questions = []
    for q in load_questions(queries_path):
        grades = by_q.get(q["qid"], {})
        rel = sorted(a for a, g in grades.items() if g >= min_grade)
        questions.append({"qid": q["qid"], "query": q["query"], "lang": q.get("lang"), "tags": q.get("tags", []),
                          "relevant_ids": rel, "relevant_urls": [urls[a] for a in rel if a in urls],
                          "judgments": [{"article_id": a, "grade": g} for a, g in sorted(grades.items())]})
    return {"questions": questions, "min_grade": min_grade}


def pool_stats(pool_rows: list[dict], judgments: list[dict], min_grade: int = 1) -> dict:
    grade = {(j["qid"], j["article_id"]): j["grade"] for j in judgments if j.get("grade") is not None}
    pooled = len(pool_rows)
    judged = sum((r["qid"], r["article_id"]) in grade for r in pool_rows)
    relevant = [r for r in pool_rows if grade.get((r["qid"], r["article_id"]), 0) >= min_grade]
    only = Counter(r["found_by"][0] for r in relevant if len(r["found_by"]) == 1)
    found = Counter(m for r in relevant for m in r["found_by"])
    per_query = Counter(r["qid"] for r in relevant)
    queries = {r["qid"] for r in pool_rows}
    return {"pooled": pooled, "judged": judged, "unjudged": pooled - judged, "relevant": len(relevant),
            "queries": len(queries), "queries_without_relevant": len(queries - set(per_query)),
            "relevant_found_by": dict(found), "relevant_found_only_by": dict(only)}
