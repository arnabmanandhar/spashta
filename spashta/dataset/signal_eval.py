"""
Measuring the signals against gold labels.

For each signal and each binary target derived from the labels:

  auc         threshold-free separation of positives from negatives, with a
              95% bootstrap interval
  shipped     precision / recall / F1 at the thresholds in
              data/thresholds.json ("some" = where a report starts warning)
  calibrated  the F1-best threshold, and an honest estimate of its
              precision / recall / F1 from 5-fold cross-validation (the
              threshold is chosen on four folds and scored on the fifth)
  spearman    rank correlation with the ordinal label, where there is one

Corroboration is measured on the labelled pairs: the same-story matcher's
precision and recall on same_event, and whether the fact comparison
finds the conflicting figures annotators marked.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Optional

from ..analysis import signals as S
from ..evaluate import auc
from . import labels as LB
from .snapshot import to_article

TARGETS: dict[str, list[tuple[str, Callable[[dict], Optional[bool]]]]] = {
    "headline": [("partly or inconsistent", LB.headline_bad),
                 ("inconsistent", lambda r: LB.headline_bad(r, strict=True))],
    "sensationalism": [("level 1 or 2", LB.sensational), ("level 2", lambda r: LB.sensational(r, 2))],
    "sourcing": [("poorly sourced", LB.poorly_sourced)],
    "framing": [("any loaded language", LB.loaded)],
}
ORDINAL: dict[str, Callable[[dict], Optional[float]]] = {
    "headline": lambda r: LB.HEADLINE.index(r["headline"]) if r.get("headline") in LB.HEADLINE else None,
    "sensationalism": lambda r: r.get("sensationalism"),
    "sourcing": LB.unattributed_share,
}
SIGNAL_FN = {"headline": S.headline_consistency, "sensationalism": S.sensationalism, "sourcing": S.sourcing,
             "framing": S.framing}


def prf(scores: list[float], gold: list[bool], threshold: float) -> dict:
    tp = sum(s >= threshold and g for s, g in zip(scores, gold))
    fp = sum(s >= threshold and not g for s, g in zip(scores, gold))
    fn = sum(s < threshold and g for s, g in zip(scores, gold))
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else 0.0
    return {"threshold": round(threshold, 3), "precision": _r(p), "recall": _r(r), "f1": _r(f), "tp": tp, "fp": fp, "fn": fn}


def best_threshold(scores: list[float], gold: list[bool]) -> float:
    candidates = sorted(set(scores)) or [0.5]
    return max(candidates, key=lambda t: (prf(scores, gold, t)["f1"] or 0.0, -t))


def cross_validated(scores: list[float], gold: list[bool], folds: int = 5, seed: int = 11) -> dict:
    """Pick the threshold on k-1 folds, apply it to the held-out fold; pool
    the held-out predictions."""
    idx = list(range(len(scores)))
    random.Random(seed).shuffle(idx)  # noqa: S311 - reproducible folds
    predicted = [False] * len(scores)
    chosen = []
    for f in range(folds):
        test = set(idx[f::folds])
        train = [i for i in idx if i not in test]
        t = best_threshold([scores[i] for i in train], [gold[i] for i in train])
        chosen.append(t)
        for i in test:
            predicted[i] = scores[i] >= t
    as_scores = [1.0 if p else 0.0 for p in predicted]
    out = prf(as_scores, gold, 0.5)
    out["threshold"] = round(sorted(chosen)[len(chosen) // 2], 3)  # median of the fold thresholds
    return out


def _bootstrap_auc(pos: list[float], neg: list[float], n: int = 1000, seed: int = 5) -> Optional[list[float]]:
    if len(pos) < 2 or len(neg) < 2:
        return None
    rng = random.Random(seed)  # noqa: S311
    vals = sorted(auc(rng.choices(pos, k=len(pos)), rng.choices(neg, k=len(neg))) for _ in range(n))
    return [round(vals[int(0.025 * n)], 3), round(vals[int(0.975 * n) - 1], 3)]


def spearman(x: list[float], y: list[float]) -> Optional[float]:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2
            i = j + 1
        return r
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx, vy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    return round(cov / (vx * vy) ** 0.5, 3) if vx and vy else None


def score_articles(records: dict[str, dict], gold: list[dict]) -> list[tuple[dict, dict]]:
    """(gold label, {signal: Signal}) for every labelled article in the snapshot."""
    out = []
    for g in gold:
        if g.get("task", "article") != "article" or g["article_id"] not in records:
            continue
        a = to_article(records[g["article_id"]])
        out.append((g, {name: fn(a) for name, fn in SIGNAL_FN.items()}))
    return out


def evaluate(records: dict[str, dict], gold: list[dict]) -> dict:
    scored = score_articles(records, gold)
    report: dict = {"articles": len(scored), "signals": {}}
    for name, targets in TARGETS.items():
        entry: dict = {"targets": {}}
        shipped = S.thresholds()[name]
        for label, fn in targets:
            rows = [(sig[name].concern, fn(g)) for g, sig in scored if fn(g) is not None]
            scores, ys = [s for s, _ in rows], [bool(y) for _, y in rows]
            pos, neg = [s for s, y in rows if y], [s for s, y in rows if not y]
            entry["targets"][label] = {
                "n": len(rows), "positives": len(pos), "auc": _r(auc(pos, neg)) if pos and neg else None,
                "auc_ci95": _bootstrap_auc(pos, neg),
                "shipped_some": prf(scores, ys, shipped["some"]), "shipped_strong": prf(scores, ys, shipped["strong"]),
                "calibrated": cross_validated(scores, ys) if pos and neg and len(rows) >= 10 else None,
            }
        if name in ORDINAL:
            pairs = [(sig[name].concern, ORDINAL[name](g)) for g, sig in scored if ORDINAL[name](g) is not None]
            entry["spearman"] = spearman([a for a, _ in pairs], [b for _, b in pairs])
        report["signals"][name] = entry
    return report


def suggested_thresholds(report: dict) -> dict:
    """'some' thresholds from the calibrated first target of each signal;
    'strong' stays at least as high."""
    out = {}
    for name, entry in report["signals"].items():
        first = next(iter(entry["targets"].values()))
        cal = first.get("calibrated")
        if cal:
            current = S.thresholds()[name]
            out[name] = {"some": cal["threshold"], "strong": max(cal["threshold"], current["strong"])}
    return out


def evaluate_pairs(records: dict[str, dict], pairs: list[dict]) -> dict:
    """The same-story matcher and the figure comparison against labelled pairs."""
    from .. import story, xling
    from ..facts import compare_facts, extract_facts
    rows = [p for p in pairs if p.get("task") == "pair" and p.get("same_event") in ("yes", "no")
            and p["a"] in records and p["b"] in records]
    names: set[str] = set()
    arts = {}
    for p in rows:
        for k in (p["a"], p["b"]):
            if k not in arts:
                arts[k] = to_article(records[k])
                if arts[k].lang == "en":
                    names |= xling.name_keys(arts[k].text)
    predicted = [story.same_story(arts[p["a"]], arts[p["b"]], names)[0] for p in rows]
    gold = [p["same_event"] == "yes" for p in rows]
    out = {"pairs": len(rows), "same_event": sum(gold),
           "matcher": prf([1.0 if x else 0.0 for x in predicted], gold, 0.5)}
    conflicts = []
    for p, g in zip(rows, gold):
        if not g:
            continue
        checks = compare_facts(extract_facts(arts[p["a"]].text), {"b": extract_facts(arts[p["b"]].text)})
        conflicts.append((any(c.status in ("conflict", "disputed") for c in checks), bool(p.get("conflicting_figures"))))
    if conflicts:
        out["conflicting_figures"] = {"pairs": len(conflicts), "with_conflicts": sum(g for _, g in conflicts),
                                      **prf([1.0 if x else 0.0 for x, _ in conflicts], [g for _, g in conflicts], 0.5)}
    return out


def error_cases(records: dict[str, dict], gold: list[dict], per_signal: int = 5) -> list[dict]:
    """The most confident mistakes per signal: highest-concern negatives
    (false alarms) and lowest-concern positives (misses), with the
    evidence the signal showed, as starting points for ERROR_ANALYSIS.md."""
    out = []
    for g, sig in score_articles(records, gold):
        for name, targets in TARGETS.items():
            label, fn = targets[0]
            y = fn(g)
            if y is None:
                continue
            s = sig[name]
            some = S.thresholds()[name]["some"]
            if (s.concern >= some) != y:
                out.append({"signal": name, "kind": "false alarm" if not y else "miss", "target": label,
                            "concern": s.concern, "article_id": g["article_id"], "url": records[g["article_id"]]["url"],
                            "title": records[g["article_id"]]["title"], "evidence": s.evidence[:6],
                            "gold": {k: g.get(k) for k in ("headline", "sensationalism") if k in g}
                            | {"claims": len(g.get("claims", [])), "loaded_spans": len(g.get("loaded", []))},
                            "notes": g.get("notes", "")})
    out.sort(key=lambda e: (e["signal"], e["kind"], -abs(e["concern"] - S.thresholds()[e["signal"]]["some"])))
    picked, count = [], {}
    for e in out:
        key = (e["signal"], e["kind"])
        if count.get(key, 0) < per_signal:
            picked.append(e)
            count[key] = count.get(key, 0) + 1
    return picked


def _r(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 3)
