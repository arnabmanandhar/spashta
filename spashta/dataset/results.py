"""
Results tables from evaluation outputs, ready for the README, the
dataset card, a blog post or a paper.

Every number comes from a JSON file written by an evaluation command, so
the tables can be regenerated whenever the data changes:

  --manifest    snapshot manifest.json            corpus table
  --compare     spashta eval Q --compare --out     retrieval by mode and by query language
  --ablation    spashta eval Q --ablate-xling      what each cross-language bridge adds
  --signals     spashta eval --gold ... --out      signal AUC / precision / recall
  --agreement   spashta dataset agreement --out    kappa per signal
  --llm         spashta dataset llm-compare --out  LLM review per provider
  --bench       bench_retrieval.py --json > f      speed and memory
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

METRIC_NAMES = (("recall_at_k", "Recall@{k}"), ("mrr", "MRR"), ("ndcg_at_k", "nDCG@{k}"), ("map_score", "MAP"))
MODE_NAMES = {"lexical": "BM25", "dense": "Dense", "xling": "Cross-language", "hybrid": "Hybrid (all three, RRF)"}


def _load(path: Optional[str | Path]) -> Optional[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def _f(v, digits: int = 3) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


def _ci(ci) -> str:
    return "" if not ci else f" [{ci[0]:.3f}, {ci[1]:.3f}]"


def corpus_table(m: dict) -> str:
    rows = ["| Outlet | Owner group | Articles |", "|---|---|---|"]
    groups = {}
    for host in m["by_outlet"]:
        from ..story import outlet_group
        groups[host] = outlet_group(host)
    for host, n in sorted(m["by_outlet"].items(), key=lambda kv: -kv[1]):
        rows.append(f"| {host} | {groups[host]} | {n} |")
    langs = ", ".join(f"{k}: {v}" for k, v in m["by_lang"].items())
    w = m["window"]
    head = (f"Snapshot `{m['snapshot_id']}`: **{m['articles']} articles** from {len(m['by_outlet'])} outlets "
            f"({len(m['by_outlet_group'])} owner groups), published {(w.get('first_article') or '')[:10]} to "
            f"{(w.get('last_article') or '')[:10]}. Languages: {langs}.\n\n")
    return head + "\n".join(rows) + "\n"


def retrieval_table(c: dict) -> str:
    k = c["k"]
    head = "| Method | " + " | ".join(n.format(k=k) for _, n in METRIC_NAMES) + " |"
    rows = [head, "|---" * (len(METRIC_NAMES) + 1) + "|"]
    for mode in ("lexical", "dense", "xling", "hybrid"):
        if mode not in c["mean"]:
            continue
        mean, ci = c["mean"][mode], c.get("ci95", {}).get(mode, {})
        rows.append(f"| {MODE_NAMES[mode]} | " + " | ".join(f"{_f(mean.get(key))}{_ci(ci.get(key))}" for key, _ in METRIC_NAMES) + " |")
    out = [f"{c.get('n', '')} queries, embedder `{c['embedder']}`, 95% bootstrap intervals in brackets.", "", *rows, ""]
    langs = sorted(t for t in c["by_tag"]["hybrid"] if t.startswith("lang:") or t in ("cross-language", "same-language"))
    if langs:
        out += [f"nDCG@{k} by query language:", "", "| Queries | n | " + " | ".join(MODE_NAMES[m] for m in c["by_tag"]) + " |",
                "|---|---" + "|---" * len(c["by_tag"]) + "|"]
        for t in langs:
            n = c["by_tag"]["hybrid"][t]["n"]
            label = {"lang:ne": "Nepali", "lang:en": "English", "lang:rom": "Romanised Nepali"}.get(t, t)
            out.append(f"| {label} | {n} | " + " | ".join(_f(c["by_tag"][m].get(t, {}).get("ndcg_at_k")) for m in c["by_tag"]) + " |")
        out.append("")
    sig = c.get("significance", {})
    if sig:
        out += [f"Hybrid against each method ({c['metric']}, paired permutation test):", "",
                "| Comparison | Δ | 95% CI | p |", "|---|---|---|---|"]
        for name, s in sig.items():
            out.append(f"| {name} | {_f(s['mean_delta'])} | [{_f(s['ci_low'])}, {_f(s['ci_high'])}] | {_f(s['p_value'], 4)} |")
        out.append("")
    return "\n".join(out)


def ablation_table(a: dict) -> str:
    metric = a["metric"]
    rows = ["| Cross-language index | Cross-language alone | Δ vs full | Hybrid | Δ vs full | Romanised queries (hybrid) |",
            "|---|---|---|---|---|---|"]
    for name, v in a["variants"].items():
        x, h = v.get("xling"), v["hybrid"]
        dx = _f(x["vs_full"]["mean_delta"]) + (" *" if x["vs_full"]["significant"] else "") if x and "vs_full" in x else "–"
        dh = _f(h["vs_full"]["mean_delta"]) + (" *" if h["vs_full"]["significant"] else "") if "vs_full" in h else "–"
        rom = h["by_tag"].get("lang:rom", {}).get(metric)
        rows.append(f"| {name} | {_f(x['mean'][metric]) if x else '–'} | {dx} | {_f(h['mean'][metric])} | {dh} | {_f(rom)} |")
    return f"{metric} at k={a['k']}; * = significant at p < 0.05 (paired permutation test).\n\n" + "\n".join(rows) + "\n"


def signals_table(s: dict) -> str:
    rows = ["| Signal | Target (from labels) | n (pos) | AUC [95% CI] | P / R / F1 at shipped threshold | Cross-validated F1 (threshold) |",
            "|---|---|---|---|---|---|"]
    for name, entry in s["signals"].items():
        for label, t in entry["targets"].items():
            sh, cv = t["shipped_some"], t.get("calibrated")
            rows.append(f"| {name} | {label} | {t['n']} ({t['positives']}) | {_f(t['auc'])}{_ci(t.get('auc_ci95'))} | "
                        f"{_f(sh['precision'], 2)} / {_f(sh['recall'], 2)} / {_f(sh['f1'], 2)} at {sh['threshold']} | "
                        f"{_f(cv['f1'], 2) if cv else '–'} ({cv['threshold'] if cv else '–'}) |")
    out = "\n".join(rows) + "\n"
    corr = s.get("corroboration")
    if corr:
        m = corr["matcher"]
        out += (f"\nSame-story matcher on {corr['pairs']} labelled pairs ({corr['same_event']} same event): precision "
                f"{_f(m['precision'], 2)}, recall {_f(m['recall'], 2)}, F1 {_f(m['f1'], 2)}.\n")
        cf = corr.get("conflicting_figures")
        if cf:
            out += (f"Conflicting figures on {cf['pairs']} same-event pairs ({cf['with_conflicts']} with conflicts): "
                    f"precision {_f(cf['precision'], 2)}, recall {_f(cf['recall'], 2)}.\n")
    return out


def llm_table(r: dict) -> str:
    rows = ["| Model | Articles ok | Headline accuracy | κ vs labels | Claims / article | Median s (p95) | Tokens in / out | USD / article |",
            "|---|---|---|---|---|---|---|---|"]
    for _, s in r["summary"].items():
        tok = s.get("tokens_per_article") or ["–", "–"]
        rows.append(f"| {s['model']} | {s['ok']}/{s['articles']} | {_f(s['headline_accuracy'], 2)} | {_f(s['headline_kappa'], 2)} | "
                    f"{s['claims_per_article']} | {s['seconds_median']} ({s['seconds_p95']}) | {tok[0]} / {tok[1]} | "
                    f"{_f(s['usd_per_article'], 5) if s['usd_per_article'] is not None else 'free tier'} |")
    out = "\n".join(rows) + "\n"
    for name, b in r.get("between_providers", {}).items():
        out += f"\n{name}: κ {_f(b['headline_fair_kappa'], 2)} on headline fairness, {_f(b['any_contradicted_kappa'], 2)} on any contradicted claim (n = {b['n']}).\n"
    return out


def bench_table(b: dict) -> str:
    q = b["query_ms"].get("hybrid") or next(iter(b["query_ms"].values()))
    return ("| Articles | Chunks | Embedder | Index time (per 1k) | Cold reload | Query median / p95 | DB | Peak memory |\n"
            "|---|---|---|---|---|---|---|---|\n"
            f"| {b['articles']} | {b['chunks']} | {b['embedder']} | {b['index_seconds']} s ({b['index_seconds_per_1k_articles']} s) | "
            f"{b['reload_seconds']} s | {q['median']} / {q['p95']} ms | {b['db_mb']} MB | "
            f"{b.get('process_peak_mb') or b.get('python_peak_mb')} MB |\n")


def resume_line(m: Optional[dict], c: Optional[dict], ag: Optional[dict]) -> str:
    n = m["articles"] if m else "N"
    outlets = len(m["by_outlet"]) if m else "M"
    kappas = []
    for e in ((ag or {}).get("articles") or {}).values():
        if isinstance(e, dict) and e.get("kappa") is not None:
            kappas.append(e["kappa"])
    kappa = f"κ = {min(kappas):.2f}–{max(kappas):.2f} across signals" if kappas else "κ = X"
    gain = "Y"
    if c and "lexical" in c["mean"]:
        h, b = c["mean"]["hybrid"]["ndcg_at_k"], c["mean"]["lexical"]["ndcg_at_k"]
        gain = f"{h - b:+.3f} ({(h - b) / b * 100:+.0f}%)" if b else f"{h - b:+.3f}"
    k = c["k"] if c else 10
    return (f"Built a hybrid Nepali–English retrieval and integrity-signal system over {n} real articles from {outlets} "
            f"outlets; released a labelled dataset ({kappa}); hybrid + cross-language retrieval changed nDCG@{k} by {gain} "
            f"over BM25.")


def build(manifest=None, compare=None, ablation=None, signals=None, agreement=None, llm=None, bench=None) -> str:
    from .agreement import to_markdown
    m, c, a, s, ag, lr, b = (_load(x) for x in (manifest, compare, ablation, signals, agreement, llm, bench))
    parts = ["# Results", "", "Generated by `spashta results`; regenerate it after any change to the data.", ""]
    for title, data, fn in (("Corpus", m, corpus_table), ("Retrieval", c, retrieval_table),
                            ("Cross-language ablation", a, ablation_table), ("Integrity signals against labels", s, signals_table),
                            ("Annotator agreement", ag, to_markdown), ("LLM claim review", lr, llm_table),
                            ("Speed and memory", b, bench_table)):
        if data:
            parts += [f"## {title}", "", fn(data), ""]
    parts += ["## One line for a resume", "", resume_line(m, c, ag), ""]
    return "\n".join(parts)


def fill_card(release_dir: str | Path, agreement: Optional[str | Path] = None, template: Optional[str | Path] = None) -> str:
    """The dataset card with every double-brace field the release and the agreement
    report can answer filled in; TODO markers are left for a person."""
    from importlib import resources
    rel = Path(release_dir)
    m = json.loads((rel / "manifest.json").read_text(encoding="utf-8"))
    ag = _load(agreement)
    text = Path(template).read_text(encoding="utf-8") if template else \
        (resources.files("spashta") / "data" / "dataset_card.md").read_text(encoding="utf-8")
    w = m.get("window", {})
    table = corpus_table({**m, "articles": m["files"].get("articles", 0), "window": w}).split("\n\n", 1)[1]
    sw = m.get("software", {})
    values = {
        "n_articles": m["files"].get("articles", 0), "n_labels": m["files"].get("labels", 0),
        "n_pairs": m["files"].get("pairs", 0), "n_queries": m["files"].get("queries", 0),
        "snapshot_id": m["snapshot_id"],
        "window": f"{(w.get('first_article') or '?')[:10]} to {(w.get('last_article') or '?')[:10]}",
        "n_outlets": len(m["by_outlet"]), "n_groups": len(m["by_outlet_group"]), "outlets_table": table.strip(),
        "langs": ", ".join(f"{ {'ne': 'Nepali', 'en': 'English'}.get(k, k)} {v}" for k, v in m["by_lang"].items()),
        "software": ", ".join(f"{k} {v}" for k, v in sw.items() if v) or "TODO",
        "min_chars": m.get("min_chars") or 200,
        "n_double": (ag or {}).get("articles", {}).get("articles_compared", "TODO"),
        "agreement_table": to_markdown_safe(ag),
    }
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def to_markdown_safe(ag: Optional[dict]) -> str:
    from .agreement import to_markdown
    return to_markdown(ag).strip() if ag else "TODO: run `spashta dataset agreement ... --out agreement.json`"
