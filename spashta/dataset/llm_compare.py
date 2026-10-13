"""
Measuring the optional LLM claim review on real articles.

For each provider (any provider Anchor supports, keys from the
environment as usual) and about 50 labelled articles, run the same audit
the report runs (claims checked against independent coverage retrieved
from the snapshot index) and record:

  agreement   headline_fair against the gold headline label (consistent =
              fair), as accuracy and Cohen's kappa
  claims      claims extracted per article and the verdict mix
  cost        input/output tokens, and USD per article when a price is given
  latency     seconds per article, median and 95th percentile
  errors      by kind (rate_limit, auth, parse, ...)

and between providers: kappa on headline_fair and on whether any claim
was contradicted. Nothing is cached, so latency and cost are real.
"""

from __future__ import annotations

import os
import random
import time
from collections import Counter
from statistics import median
from typing import Optional

from .agreement import cohen_kappa
from .snapshot import to_article


def connection(spec: str, price: Optional[tuple[float, float]] = None):
    """'provider:model' (model optional) -> anchor Connection with the
    provider's key from the environment."""
    from anchor import llm
    provider, _, model = spec.partition(":")
    p = llm.PROVIDERS[provider]
    key = next((os.environ[k] for k in p.env_keys if os.environ.get(k)), None)
    if key is None and provider != "custom":
        raise ValueError(f"no key for {provider}: set {' or '.join(p.env_keys)}")
    return llm.Connection(provider, model or p.models[0], llm.Secret(key) if key else None,
                          os.environ.get("ANCHOR_LLM_BASE_URL") if provider == "custom" else None, price_per_mtok=price)


def parse_prices(specs: list[str]) -> dict[str, tuple[float, float]]:
    """['gemini:gemini-2.5-flash=0.30/2.50'] -> {spec: (input, output) USD per million tokens}."""
    out = {}
    for s in specs or []:
        name, _, price = s.rpartition("=")
        i, _, o = price.partition("/")
        out[name] = (float(i), float(o))
    return out


def choose(gold: list[dict], records: dict[str, dict], n: int, seed: int = 3) -> list[dict]:
    """n labelled articles, balanced over the gold headline label where possible."""
    rng = random.Random(seed)  # noqa: S311
    by: dict[str, list[dict]] = {}
    for g in gold:
        if g.get("task", "article") == "article" and g["article_id"] in records and g.get("headline"):
            by.setdefault(g["headline"], []).append(g)
    for v in by.values():
        rng.shuffle(v)
    out: list[dict] = []
    while len(out) < n and any(by.values()):
        for v in by.values():
            if v and len(out) < n:
                out.append(v.pop())
    return out


def _p95(xs: list[float]) -> Optional[float]:
    return round(sorted(xs)[max(0, int(0.95 * len(xs)) - 1)], 2) if xs else None


def compare(index, records: dict[str, dict], gold: list[dict], connections: dict, n: int = 50, pause_s: float = 4.0,
            review_fn=None, sleep=time.sleep) -> dict:
    from ..analysis.corroboration import corroborate
    from ..analysis.llm_review import review
    review_fn = review_fn or review
    items = choose(gold, records, n)
    per_article = []
    for g in items:
        article = to_article(records[g["article_id"]])
        corr = corroborate(article, index)
        passages = [h for r in corr.related if r.role == "independent" for h in r.passages][:12]
        row = {"article_id": g["article_id"], "gold_headline": g["headline"], "passages": len(passages), "providers": {}}
        for name, conn in connections.items():
            for attempt in range(2):
                t = time.perf_counter()
                rev = review_fn(article, passages, conn, cache=None)
                took = time.perf_counter() - t
                if rev.error != "rate_limit" or attempt:
                    break
                sleep(30)
            d = rev.signal.details if rev.error is None else {}
            row["providers"][name] = {
                "error": rev.error, "seconds": round(took, 2), "headline_fair": rev.headline_fair,
                "claims": len(rev.claims), "verdicts": dict(Counter(c.verdict for c in rev.claims)),
                "input_tokens": d.get("input_tokens", 0), "output_tokens": d.get("output_tokens", 0),
                "cost_usd": d.get("cost_usd", 0.0)}
            sleep(pause_s)
        per_article.append(row)
    summary = {}
    for name, conn in connections.items():
        rows = [(a["gold_headline"], a["providers"][name]) for a in per_article]
        ok = [(g, p) for g, p in rows if p["error"] is None]
        fair_gold = [g == "consistent" for g, _ in ok]
        fair_pred = [p["headline_fair"] for _, p in ok]
        secs = [p["seconds"] for _, p in ok]
        verdicts = Counter()
        for _, p in ok:
            verdicts.update(p["verdicts"])
        summary[name] = {
            "model": conn.name, "articles": len(rows), "ok": len(ok),
            "errors": dict(Counter(p["error"] for _, p in rows if p["error"])),
            "headline_accuracy": round(sum(a == b for a, b in zip(fair_gold, fair_pred)) / len(ok), 3) if ok else None,
            "headline_kappa": _r(cohen_kappa(fair_gold, fair_pred)),
            "claims_per_article": round(sum(p["claims"] for _, p in ok) / len(ok), 2) if ok else None,
            "verdicts": dict(verdicts),
            "seconds_median": round(median(secs), 2) if secs else None, "seconds_p95": _p95(secs),
            "tokens_per_article": [round(sum(p["input_tokens"] for _, p in ok) / len(ok)),
                                   round(sum(p["output_tokens"] for _, p in ok) / len(ok))] if ok else None,
            "usd_per_article": round(sum(p["cost_usd"] for _, p in ok) / len(ok), 6) if ok and conn.price_per_mtok else None,
        }
    names = list(connections)
    between = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = [(r["providers"][a], r["providers"][b]) for r in per_article
                    if r["providers"][a]["error"] is None and r["providers"][b]["error"] is None]
            between[f"{a} vs {b}"] = {
                "n": len(both),
                "headline_fair_kappa": _r(cohen_kappa([x["headline_fair"] for x, _ in both], [y["headline_fair"] for _, y in both])),
                "any_contradicted_kappa": _r(cohen_kappa([bool(x["verdicts"].get("contradicted")) for x, _ in both],
                                                         [bool(y["verdicts"].get("contradicted")) for _, y in both]))}
    return {"articles": len(items), "summary": summary, "between_providers": between, "per_article": per_article}


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 3)
