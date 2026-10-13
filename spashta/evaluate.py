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


def load_questions(path: str | Path) -> list[dict]:
    """{"questions": [...]} JSON, a JSON list, or JSONL. Each question has
    "query" and "relevant_urls" or "relevant_ids", optionally "qid",
    "lang" (ne, en or rom for romanised Nepali) and "tags"."""
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith("{") and '"questions"' in text[:2000]:
        qs = json.loads(text)["questions"]
    elif text.lstrip().startswith("["):
        qs = json.loads(text)
    else:
        qs = [json.loads(line) for line in text.splitlines() if line.strip()]
    for i, q in enumerate(qs, 1):
        q.setdefault("qid", f"q{i:03d}")
    return qs


def relevant_ids(q: dict) -> list[str]:
    return list(q.get("relevant_ids") or [article_id(u) for u in q.get("relevant_urls", [])])


def question_tags(q: dict, index: Index) -> list[str]:
    """The question's own tags, plus lang:<query language> and whether the
    relevant articles are in another language or script than the query."""
    tags = list(q.get("tags", []))
    lang = q.get("lang")
    if lang:
        tags.append(f"lang:{lang}")
        langs = {getattr(index.store.article(a), "lang", None) for a in relevant_ids(q)} - {None}
        if langs:
            tags.append("cross-language" if lang == "rom" or langs - {lang} else "same-language")
    return list(dict.fromkeys(tags))


def _means(rows: list[dict]) -> dict:
    return {key: round(mean(r[key] for r in rows), 4) for key in METRICS} if rows else {}


def _cis(rows: list[dict]) -> dict:
    from anchor.stats import bootstrap_ci
    out = {}
    for key in METRICS:
        ci = bootstrap_ci([r[key] for r in rows]) if rows else None
        out[key] = [round(ci[0], 4), round(ci[1], 4)] if ci else None
    return out


def evaluate_retrieval(index: Index, questions_path: str | Path, k: int = 5, records_out: str | Path | None = None,
                       mode: str = "hybrid") -> dict:
    from anchor.retrieval_metrics import all_retrieval_metrics

    rows, records, skipped = [], [], 0
    for q in load_questions(questions_path):
        relevant = relevant_ids(q)
        if not relevant:
            skipped += 1  # nothing judged relevant: no metric is defined
            continue
        ranked = ranked_articles(index, q["query"], k, mode)
        m = all_retrieval_metrics(ranked, relevant, k)
        rows.append({"qid": q["qid"], "query": q["query"], "tags": question_tags(q, index), "ranked": ranked,
                     "relevant": relevant, **m.model_dump()})
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
    return {"embedder": index.embedder.name, "mode": mode, "k": k, "n": len(rows), "skipped_no_relevant": skipped,
            "mean": _means(rows), "ci95": _cis(rows),
            "by_tag": {t: {"n": len(rs), **_means(rs), "ci95": _cis(rs)} for t, rs in sorted(by_tag.items())},
            "per_question": rows}


def _significance(per_a: list[float], per_b: list[float]) -> dict:
    from anchor.stats import paired_test
    t = paired_test(per_a, per_b)
    return {key: (round(t[key], 4) if isinstance(t[key], float) else t[key])
            for key in ("n", "mean_delta", "ci_low", "ci_high", "p_value", "significant", "mde")}


def compare_modes(index: Index, questions_path: str | Path, k: int = 5, metric: str = "ndcg_at_k") -> dict:
    """Every retrieval mode on the same questions, with paired significance
    of hybrid against each, overall and per tag (query language)."""
    results = {m: evaluate_retrieval(index, questions_path, k, mode=m) for m in MODES}
    per = {m: [r[metric] for r in results[m]["per_question"]] for m in MODES}
    tags = sorted({t for r in results["hybrid"]["per_question"] for t in r["tags"]})
    by_tag_sig = {}
    for tag in tags:
        idx = [i for i, r in enumerate(results["hybrid"]["per_question"]) if tag in r["tags"]]
        by_tag_sig[tag] = {f"hybrid vs {m}": _significance([per[m][i] for i in idx], [per["hybrid"][i] for i in idx])
                           for m in MODES if m != "hybrid"}
    return {"metric": metric, "k": k, "embedder": index.embedder.name, "n": results["hybrid"]["n"],
            "mean": {m: results[m]["mean"] for m in MODES},
            "ci95": {m: results[m]["ci95"] for m in MODES},
            "by_tag": {m: results[m]["by_tag"] for m in MODES},
            "significance": {f"hybrid vs {m}": _significance(per[m], per["hybrid"]) for m in MODES if m != "hybrid"},
            "significance_by_tag": by_tag_sig}


def ablate_xling(store, embedder, questions_path: str | Path, k: int = 10, metric: str = "ndcg_at_k") -> dict:
    """Switch off each part of the cross-language index in turn
    (romanisation, phonetic keys, glossary, then all of it) and measure the
    cross-language ranking alone and hybrid search, against the full index.
    Stored vectors are reused, so only the BM25 halves are rebuilt."""
    from . import xling
    out: dict = {"metric": metric, "k": k, "embedder": embedder.name, "variants": {}}
    full: dict[str, list[float]] = {}
    for cfg in xling.ABLATIONS:
        index = Index(store, embedder, xling_config=cfg)
        entry = {}
        for mode in (("xling", "hybrid") if cfg.enabled else ("hybrid",)):
            res = evaluate_retrieval(index, questions_path, k, mode=mode)
            per = [r[metric] for r in res["per_question"]]
            entry[mode] = {"mean": res["mean"], "ci95": res["ci95"],
                           "by_tag": {t: {metric: v[metric], "n": v["n"]} for t, v in res["by_tag"].items()}}
            if cfg == xling.FULL:
                full[mode] = per
            else:
                entry[mode]["vs_full"] = _significance(full[mode], per)
        out["variants"][cfg.name] = entry
    return out


def auc(pos: list[float], neg: list[float]) -> float:
    """Probability a random positive scores above a random negative (ties
    count half): threshold-free separation of labelled examples."""
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def evaluate_signals(articles_path: str | Path, index: Index | None = None) -> dict:
    """AUC of each deterministic signal (and the overall score) against the
    labels in an articles file ({"articles": [{..., "labels": {...}}]}).
    The bundled labels are synthetic; the same command runs on any
    annotated set in that shape."""
    from .analysis.signals import framing, sensationalism, sourcing
    from .report import analyze

    data = json.loads(Path(articles_path).read_text(encoding="utf-8"))
    from .samples import article_from_dict

    rows = [(article_from_dict(a), a.get("labels", {})) for a in data["articles"]]
    out = {}
    for name, fn, label in (("sensationalism", sensationalism, "sensational"), ("framing", framing, "loaded"),
                            ("sourcing", sourcing, "poorly_sourced")):
        pos = [fn(a).concern for a, lab in rows if lab.get(label)]
        neg = [fn(a).concern for a, lab in rows if label in lab and not lab[label]]
        out[name] = {"label": label, "auc": round(auc(pos, neg), 3), "positives": len(pos), "negatives": len(neg)}
    bad = [100 - analyze(a, index).score for a, lab in rows if any(lab.values())]
    good = [100 - analyze(a, index).score for a, lab in rows if lab and not any(lab.values())]
    out["overall"] = {"label": "any", "auc": round(auc(bad, good), 3), "positives": len(bad), "negatives": len(good)}
    return out
