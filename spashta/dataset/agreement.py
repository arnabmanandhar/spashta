"""
Inter-annotator agreement, per signal.

  headline        Cohen's kappa over 3 classes, and quadratic-weighted kappa
                  (partly vs inconsistent is a smaller disagreement than
                  consistent vs inconsistent)
  sensationalism  quadratic-weighted kappa on the 0-2 scale, and plain kappa
  sourcing        token-level kappa: every word of the body gets the label of
                  the claim span covering it (named / anonymous /
                  unattributed / none), plus article-level kappa on
                  "poorly sourced"
  framing         token-level kappa on loaded vs not, article-level kappa on
                  "any loaded language", and span F1 (overlap match)
  corroboration   kappa on same_event over pairs ("unsure" kept as a class)

Each kappa comes with a 95% bootstrap interval from resampling articles,
and the Landis & Koch band. A low kappa is a finding: the signal is
subjective as defined, and should be reported as such.
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Optional

from .. import lang as L
from . import labels as LB


def cohen_kappa(a: list, b: list, weights: Optional[str] = None, categories: Optional[list] = None) -> Optional[float]:
    """Cohen's kappa; weights None, 'linear' or 'quadratic' (ordinal
    categories, in order). None when there is nothing to compare or both
    annotators used a single identical category throughout."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    if not pairs:
        return None
    cats = categories or sorted({x for p in pairs for x in p}, key=str)
    idx = {c: i for i, c in enumerate(cats)}
    k, n = len(cats), len(pairs)
    if k < 2:
        return None

    def w(i: int, j: int) -> float:
        if weights is None:
            return 0.0 if i == j else 1.0
        d = abs(i - j) / (k - 1)
        return d if weights == "linear" else d * d

    obs = Counter((idx[x], idx[y]) for x, y in pairs)
    pa = Counter(idx[x] for x, _ in pairs)
    pb = Counter(idx[y] for _, y in pairs)
    observed = sum(w(i, j) * c for (i, j), c in obs.items()) / n
    expected = sum(w(i, j) * pa[i] * pb[j] for i in range(k) for j in range(k)) / (n * n)
    if expected == 0:
        return 1.0 if observed == 0 else None
    return 1 - observed / expected


def band(kappa: Optional[float]) -> str:
    if kappa is None:
        return "n/a"
    for limit, name in ((0, "poor"), (0.2, "slight"), (0.4, "fair"), (0.6, "moderate"), (0.8, "substantial")):
        if kappa <= limit:
            return name
    return "almost perfect"


def token_labels(body: str, spans: list[dict], field: Optional[str] = None) -> list[str]:
    """One label per word of body: the label of the span covering most of
    it (field value, or 'yes' when field is None), else 'none'."""
    out = []
    for m in L.TOKEN_RE.finditer(body):
        best, cover = "none", 0
        for s in spans:
            overlap = min(m.end(), s["end"]) - max(m.start(), s["start"])
            if overlap * 2 >= m.end() - m.start() and overlap > cover:
                best, cover = (s[field] if field else "yes"), overlap
        out.append(best)
    return out


def span_f1(a: list[dict], b: list[dict]) -> Optional[float]:
    """F1 of b's spans against a's, a span matching when they overlap."""
    if not a and not b:
        return None
    hit_a = sum(any(min(x["end"], y["end"]) > max(x["start"], y["start"]) for y in b) for x in a)
    hit_b = sum(any(min(x["end"], y["end"]) > max(x["start"], y["start"]) for x in a) for y in b)
    p = hit_b / len(b) if b else 0.0
    r = hit_a / len(a) if a else 0.0
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def _bootstrap(items: list, stat, n: int = 1000, seed: int = 7) -> Optional[tuple[float, float]]:
    rng = random.Random(seed)  # noqa: S311 - reproducible resampling
    vals = []
    for _ in range(n):
        v = stat([rng.choice(items) for _ in items])
        if v is not None:
            vals.append(v)
    if len(vals) < n // 2:
        return None
    vals.sort()
    return round(vals[int(0.025 * len(vals))], 3), round(vals[int(0.975 * len(vals)) - 1], 3)


def _entry(items: list, stat, n_items: int) -> dict:
    k = stat(items)
    return {"n": n_items, "kappa": None if k is None else round(k, 3), "ci95": _bootstrap(items, stat) if items else None,
            "band": band(k)}


def article_agreement(a_rows: list[dict], b_rows: list[dict], bodies: dict[str, str]) -> dict:
    a, b = LB.by_key([r for r in a_rows if r.get("task", "article") == "article"]), \
        LB.by_key([r for r in b_rows if r.get("task", "article") == "article"])
    shared = [k for k in a if k in b and not a[k].get("skip") and not b[k].get("skip")]
    items = [(a[k], b[k], bodies.get(a[k]["article_id"], "")) for k in shared]
    out: dict = {"articles_compared": len(items),
                 "skipped_by_either": sum(1 for k in a if k in b and (a[k].get("skip") or b[k].get("skip")))}

    def field_kappa(field, weights=None, cats=None):
        return lambda its: cohen_kappa([x[field] for x, _, _ in its], [y[field] for _, y, _ in its], weights, cats)

    out["headline"] = _entry(items, field_kappa("headline", None, list(LB.HEADLINE)), len(items))
    out["headline"]["weighted_kappa"] = _round(field_kappa("headline", "quadratic", list(LB.HEADLINE))(items))
    out["headline"]["confusion"] = _confusion(items, "headline")
    out["sensationalism"] = _entry(items, field_kappa("sensationalism", "quadratic", list(LB.SENSATIONALISM)), len(items))
    out["sensationalism"]["unweighted_kappa"] = _round(field_kappa("sensationalism", None, list(LB.SENSATIONALISM))(items))
    out["sensationalism"]["confusion"] = _confusion(items, "sensationalism")

    def tokens(field, kind):
        def stat(its):
            xs, ys = [], []
            for x, y, body in its:
                xs += token_labels(body, x.get(kind, []), field)
                ys += token_labels(body, y.get(kind, []), field)
            return cohen_kappa(xs, ys)
        return stat

    def derived(fn):
        return lambda its: cohen_kappa([fn(x) for x, _, _ in its], [fn(y) for _, y, _ in its])

    out["sourcing"] = _entry(items, tokens("sourcing", "claims"), len(items))
    out["sourcing"]["unit"] = "word"
    out["sourcing"]["poorly_sourced_kappa"] = _round(derived(LB.poorly_sourced)(items))
    out["sourcing"]["claim_span_f1"] = _mean([span_f1(x.get("claims", []), y.get("claims", [])) for x, y, _ in items])
    out["framing"] = _entry(items, tokens(None, "loaded"), len(items))
    out["framing"]["unit"] = "word"
    out["framing"]["any_loaded_kappa"] = _round(derived(LB.loaded)(items))
    out["framing"]["span_f1"] = _mean([span_f1(x.get("loaded", []), y.get("loaded", [])) for x, y, _ in items])
    out["topic"] = _entry(items, lambda its: cohen_kappa([x.get("topic") for x, _, _ in its],
                                                         [y.get("topic") for _, y, _ in its]), len(items))
    return out


def pair_agreement(a_rows: list[dict], b_rows: list[dict]) -> dict:
    a = LB.by_key([r for r in a_rows if r.get("task") == "pair"])
    b = LB.by_key([r for r in b_rows if r.get("task") == "pair"])
    items = [(a[k], b[k]) for k in a if k in b]
    stat = lambda its: cohen_kappa([x["same_event"] for x, _ in its], [y["same_event"] for _, y in its],  # noqa: E731
                                   categories=list(LB.SAME_EVENT))
    out = _entry(items, stat, len(items))
    sure = [(x, y) for x, y in items if "unsure" not in (x["same_event"], y["same_event"])]
    out["kappa_excluding_unsure"] = _round(cohen_kappa([x["same_event"] for x, _ in sure], [y["same_event"] for _, y in sure]))
    return out


def qrel_agreement(a_rows: list[dict], b_rows: list[dict]) -> dict:
    a = LB.by_key([r for r in a_rows if r.get("task") == "qrel"])
    b = LB.by_key([r for r in b_rows if r.get("task") == "qrel"])
    items = [(a[k], b[k]) for k in a if k in b]
    stat = lambda its: cohen_kappa([x["grade"] for x, _ in its], [y["grade"] for _, y in its], "quadratic", [0, 1, 2])  # noqa: E731
    out = _entry(items, stat, len(items))
    out["binary_kappa"] = _round(cohen_kappa([x["grade"] >= 1 for x, _ in items], [y["grade"] >= 1 for _, y in items]))
    return out


def _confusion(items, field) -> dict:
    """Counts of (first annotator | second annotator) label pairs."""
    return dict(sorted(Counter(f"{x[field]}|{y[field]}" for x, y, _ in items).items()))


def _round(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 3)


def _mean(vals: list[Optional[float]]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def to_markdown(report: dict) -> str:
    rows = ["| Signal | Unit | n | Cohen's κ | 95% CI | Band | Also |", "|---|---|---|---|---|---|---|"]
    art = report.get("articles", {})
    extras = {"headline": lambda e: f"weighted κ {e.get('weighted_kappa')}",
              "sensationalism": lambda e: f"unweighted κ {e.get('unweighted_kappa')} (κ shown is quadratic-weighted)",
              "sourcing": lambda e: f"poorly-sourced κ {e.get('poorly_sourced_kappa')}, claim span F1 {e.get('claim_span_f1')}",
              "framing": lambda e: f"any-loaded κ {e.get('any_loaded_kappa')}, span F1 {e.get('span_f1')}",
              "topic": lambda e: ""}
    for name, extra in extras.items():
        e = art.get(name)
        if e:
            rows.append(f"| {name} | {e.get('unit', 'article')} | {e['n']} | {e['kappa']} | {e['ci95']} | {e['band']} | {extra(e)} |")
    for name, unit, extra in (("pairs", "pair", lambda e: f"κ without 'unsure' {e.get('kappa_excluding_unsure')}"),
                              ("qrels", "judgment", lambda e: f"binary κ {e.get('binary_kappa')} (κ shown is quadratic-weighted)")):
        e = report.get(name)
        if e and e["n"]:
            label = "corroboration (same event)" if name == "pairs" else "retrieval relevance"
            rows.append(f"| {label} | {unit} | {e['n']} | {e['kappa']} | {e['ci95']} | {e['band']} | {extra(e)} |")
    return "\n".join(rows) + "\n"
