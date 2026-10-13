"""
Dataset commands: from a live store to a frozen, labelled, measured corpus.

  spashta dataset freeze DIR [--since D --until D]   hashed snapshot of the store
  spashta dataset verify DIR                         check every hash and the snapshot id
  spashta dataset index DIR                          index a snapshot into --db for evaluation
  spashta dataset sample SNAP -o sample.jsonl        stratified labelling sample (pilot + double-annotated subsets)
  spashta dataset pairs SNAP sample.jsonl -o P       same-event candidate pairs for corroboration labels
  spashta dataset labeltool SNAP sample.jsonl -o page.html [--batch pilot|main] [--double]
  spashta dataset labeltool SNAP --pairs P -o page.html   labelling page for same-event pairs
  spashta dataset check SNAP labels.jsonl            validate labels against the snapshot
  spashta dataset agreement SNAP a.jsonl b.jsonl     Cohen's kappa per signal, with 95% intervals
  spashta dataset gold primary.jsonl [--adjudicated c.jsonl] -o gold.jsonl
  spashta qrels pool QUERIES -o pool.jsonl [--depth 10]   pool top results of every retrieval mode for judging
  spashta qrels build QUERIES judgments.jsonl -o questions.json --pool pool.jsonl
  spashta dataset errors SNAP gold.jsonl [--retrieval eval.json] -o candidates.md   failure cases to write up
  spashta dataset llm-compare SNAP gold.jsonl --providers gemini groq:openai/gpt-oss-120b   LLM review vs labels
  spashta dataset release SNAP OUT [--labels ...]    the public part: no article text, hashes and offsets only
  spashta dataset rehydrate RELEASE OUT              re-fetch the text of a release and re-anchor its labels
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .embed import get_embedder
from .retrieve import Index
from .store import Store


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def cmd_freeze(args) -> int:
    from .cli import parse_day
    from .dataset import snapshot
    store = Store(args.db)
    sources = {s.host: {"name": s.name, "lang": s.lang, "kind": s.kind} for s in store.sources()}
    m = snapshot.freeze(store.iter_articles(), args.dir, parse_day(args.since), parse_day(args.until, end=True),
                        args.min_chars, sources, args.note or "")
    _print({k: m[k] for k in ("snapshot_id", "articles", "window", "by_lang", "by_outlet")})
    return 0


def cmd_verify(args) -> int:
    from .dataset import snapshot
    problems = snapshot.verify(args.dir)
    for p in problems:
        print(p, file=sys.stderr)
    m = snapshot.read_manifest(args.dir)
    print(f"snapshot {m['snapshot_id']}: {m['articles']} articles, " + ("OK" if not problems else f"{len(problems)} problems"))
    return 1 if problems else 0


def cmd_index(args) -> int:
    from .dataset import snapshot
    index = Index(Store(args.db), get_embedder(args.embedder))
    articles = snapshot.load(args.dir)
    for i in range(0, len(articles), 200):
        index.add_many(articles[i:i + 200])
        print(f"indexed {min(i + 200, len(articles))}/{len(articles)}", file=sys.stderr)
    _print(index.store.count())
    return 0


def _write_jsonl(path, rows) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _read_jsonl(path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def cmd_sample(args) -> int:
    from .dataset import sample, snapshot
    chosen = sample.stratified_sample(snapshot.read_records(args.snapshot), n=args.n, multi_weight=args.multi_weight,
                                      seed=args.seed, minimum=args.minimum, pilot=args.pilot, double=args.double)
    _write_jsonl(args.out, chosen)
    _print(sample.summary(chosen))
    return 0


def cmd_pairs(args) -> int:
    from collections import Counter

    from .dataset import sample, snapshot
    pairs = sample.candidate_pairs(snapshot.read_records(args.snapshot), _read_jsonl(args.sample),
                                   per_article=args.per_article, seed=args.seed, limit=args.limit)
    _write_jsonl(args.out, pairs)
    _print({"pairs": len(pairs), "by_reason": dict(Counter(p["reason"] for p in pairs))})
    return 0


def _records_by_id(snapshot_dir) -> dict[str, dict]:
    from .dataset import snapshot
    return {r["id"]: r for r in snapshot.read_records(snapshot_dir)}


def cmd_labeltool(args) -> int:
    from .dataset import labeltool
    records = _records_by_id(args.snapshot)
    if args.pairs:
        items, mode = labeltool.pair_items(_read_jsonl(args.pairs), records), "pair"
    elif args.pool:
        items, mode = labeltool.qrel_items(_read_jsonl(args.pool), records), "qrel"
    elif args.sample:
        items, mode = labeltool.article_items(_read_jsonl(args.sample), records, args.batch, args.double), "article"
    else:
        print("give a sample file, --pairs or --pool", file=sys.stderr)
        return 2
    batch = args.name or "-".join(x for x in (mode, args.batch, "double" if args.double else "") if x)
    labeltool.build(mode, items, args.out, batch)
    print(f"{len(items)} {mode} items -> {args.out} (contains article text: share only with annotators)")
    return 0


def cmd_check(args) -> int:
    from .dataset import labels as LB
    records = _records_by_id(args.snapshot)
    bad = 0
    for i, r in enumerate(LB.read(args.labels), 1):
        body = records.get(r.get("article_id", ""), {}).get("body")
        problems = LB.validate(r, len(body) if body is not None else None)
        if r.get("task", "article") == "article" and body is None:
            problems.append("article not in snapshot")
        for p in problems:
            print(f"line {i}: {p}", file=sys.stderr)
        bad += bool(problems)
    print(f"{bad} records with problems")
    return 1 if bad else 0


def cmd_agreement(args) -> int:
    from .dataset import agreement
    from .dataset import labels as LB
    bodies = {k: r["body"] for k, r in _records_by_id(args.snapshot).items()}
    a, b = LB.read(args.first), LB.read(args.second)
    report = {"articles": agreement.article_agreement(a, b, bodies), "pairs": agreement.pair_agreement(a, b),
              "qrels": agreement.qrel_agreement(a, b)}
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(agreement.to_markdown(report) if args.markdown else json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_gold(args) -> int:
    from .dataset import labels as LB
    primary = [r for path in args.primary for r in LB.read(path)]
    rows = LB.gold(primary, LB.read(args.adjudicated) if args.adjudicated else None)
    LB.write(args.out, rows)
    print(f"{len(rows)} gold labels -> {args.out}")
    return 0


def cmd_errors(args) -> int:
    from .dataset import labels as LB
    from .dataset import signal_eval
    records = _records_by_id(args.snapshot)
    lines = ["# Error candidates", "",
             "Generated by `spashta dataset errors`. Pick 15-20 real failures from here for docs/ERROR_ANALYSIS.md:",
             "say what the system did, what the label says, the cause, and what would fix it.", ""]
    for e in signal_eval.error_cases(records, LB.read(args.gold), args.per_signal):
        lines += [f"## {e['signal']} · {e['kind']} · concern {e['concern']:.2f}", "",
                  f"[{e['title']}]({e['url']}) · `{e['article_id']}`", "",
                  f"Target: {e['target']} · gold: {json.dumps(e['gold'], ensure_ascii=False)}", ""]
        lines += [f"- {x}" for x in e["evidence"]] or ["- (no evidence shown)"]
        if e["notes"]:
            lines += ["", f"Annotator note: {e['notes']}"]
        lines.append("")
    if args.retrieval:
        res = json.loads(Path(args.retrieval).read_text(encoding="utf-8"))
        rows = res.get("per_question") or []
        if not rows:
            print("the retrieval file has no per_question rows: run `spashta eval QUESTIONS --out FILE`", file=sys.stderr)
        title = lambda a: (records.get(a) or {}).get("title", a)  # noqa: E731
        lines += ["# Retrieval failures (lowest nDCG first)", ""]
        for r in sorted(rows, key=lambda r: r["ndcg_at_k"])[:args.per_signal * 3]:
            missed = [a for a in r["relevant"] if a not in r["ranked"]]
            lines += [f"## {r['query']} · nDCG {r['ndcg_at_k']:.2f} · {', '.join(r['tags'])}", "", "Retrieved:"]
            lines += [f"{i}. {'✓' if a in r['relevant'] else '✗'} {title(a)}" for i, a in enumerate(r["ranked"], 1)]
            lines += ["", "Relevant but not retrieved:"] + [f"- {title(a)}" for a in missed] + [""]
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"-> {args.out}")
    return 0


def cmd_llm_compare(args) -> int:
    from .dataset import labels as LB
    from .dataset import llm_compare
    prices = llm_compare.parse_prices(args.price)
    conns = {spec: llm_compare.connection(spec, prices.get(spec)) for spec in args.providers}
    index = Index(Store(args.db), get_embedder(args.embedder))
    if not index.chunks:
        print("the index is empty: run `spashta --db DB dataset index SNAP` first", file=sys.stderr)
        return 2
    result = llm_compare.compare(index, _records_by_id(args.snapshot), LB.read(args.gold), conns, n=args.n,
                                 pause_s=args.pause)
    if args.out:
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print({k: v for k, v in result.items() if k != "per_article"})
    return 0


def cmd_release(args) -> int:
    from .dataset import release
    m = release.build_release(args.snapshot, args.out, args.labels, args.pairs, args.queries, args.sample, args.card)
    _print({"snapshot_id": m["snapshot_id"], "files": m["files"], "out": args.out})
    return 0


def cmd_rehydrate(args) -> int:
    from .dataset import release
    _print(release.rehydrate(args.release, args.out, limit=args.limit))
    return 0


def cmd_qrels_pool(args) -> int:
    from .dataset import qrels
    index = Index(Store(args.db), get_embedder(args.embedder))
    rows = qrels.pool(index, args.queries, depth=args.depth)
    _write_jsonl(args.out, rows)
    per_q = len({r["qid"] for r in rows})
    print(f"{len(rows)} (query, article) pairs to judge for {per_q} queries -> {args.out}")
    return 0


def cmd_qrels_build(args) -> int:
    from .dataset import labels as LB
    from .dataset import qrels
    judgments = [r for path in args.judgments for r in LB.read(path)]
    pool_rows = _read_jsonl(args.pool) if args.pool else None
    data = qrels.build_questions(args.queries, judgments, pool_rows, args.min_grade)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if pool_rows:
        _print(qrels.pool_stats(pool_rows, judgments, args.min_grade))
    print(f"{len(data['questions'])} questions -> {args.out}")
    return 0


def add_dataset_commands(sub) -> None:
    q = sub.add_parser("qrels", help="pooled relevance judgments for retrieval queries")
    qs = q.add_subparsers(dest="action", required=True)
    qp = qs.add_parser("pool", help="union of every retrieval mode's top results, shuffled, for judging")
    qp.add_argument("queries", help="queries (JSON or JSONL) with query, lang (ne, en, rom) and optional qid, tags")
    qp.add_argument("-o", "--out", required=True)
    qp.add_argument("--depth", type=int, default=10)
    qp.set_defaults(func=cmd_qrels_pool)
    qb = qs.add_parser("build", help="questions file for `spashta eval` from judgments")
    qb.add_argument("queries")
    qb.add_argument("judgments", nargs="+", help="relevance labels (JSONL, task qrel)")
    qb.add_argument("-o", "--out", required=True)
    qb.add_argument("--pool", help="the pool file, for URLs and pool statistics")
    qb.add_argument("--min-grade", type=int, default=1, help="lowest grade counted as relevant (1 = partly)")
    qb.set_defaults(func=cmd_qrels_build)

    d = sub.add_parser("dataset", help="snapshots, sampling, labels, agreement and release")
    ds = d.add_subparsers(dest="action", required=True)

    f = ds.add_parser("freeze", help="write a hashed snapshot of the store")
    f.add_argument("dir")
    f.add_argument("--since", help="first publication date to include (YYYY-MM-DD)")
    f.add_argument("--until", help="last publication date to include (YYYY-MM-DD)")
    f.add_argument("--min-chars", type=int, default=200, help="drop articles with shorter bodies")
    f.add_argument("--note", help="free text stored in the manifest")
    f.set_defaults(func=cmd_freeze)

    v = ds.add_parser("verify", help="check a snapshot's hashes")
    v.add_argument("dir")
    v.set_defaults(func=cmd_verify)

    x = ds.add_parser("index", help="index a snapshot into --db (use a fresh db for evaluation)")
    x.add_argument("dir")
    x.set_defaults(func=cmd_index)

    sp = ds.add_parser("sample", help="stratified sample of articles to label")
    sp.add_argument("snapshot")
    sp.add_argument("-o", "--out", required=True)
    sp.add_argument("--n", type=int, default=400)
    sp.add_argument("--multi-weight", type=float, default=3.0, help="how much more often multi-outlet stories are drawn")
    sp.add_argument("--minimum", type=int, default=8, help="articles per language x topic stratum, when available")
    sp.add_argument("--pilot", type=int, default=30, help="size of the pilot batch")
    sp.add_argument("--double", type=int, default=100, help="articles (besides the pilot) for the second annotator")
    sp.add_argument("--seed", type=int, default=13)
    sp.set_defaults(func=cmd_sample)

    pp = ds.add_parser("pairs", help="same-event candidate pairs for the sample")
    pp.add_argument("snapshot")
    pp.add_argument("sample")
    pp.add_argument("-o", "--out", required=True)
    pp.add_argument("--per-article", type=int, default=1, help="likely-positive pairs per sampled article")
    pp.add_argument("--limit", type=int)
    pp.add_argument("--seed", type=int, default=13)
    pp.set_defaults(func=cmd_pairs)

    lt = ds.add_parser("labeltool", help="write a self-contained HTML labelling page")
    lt.add_argument("snapshot")
    lt.add_argument("sample", nargs="?", help="sample file from `dataset sample` (article labels)")
    lt.add_argument("--pairs", help="pairs file from `dataset pairs` (same-event labels)")
    lt.add_argument("--pool", help="pool file from `qrels pool` (relevance judgments)")
    lt.add_argument("--batch", choices=["pilot", "main"], help="only this batch of the sample")
    lt.add_argument("--double", action="store_true", help="only the articles marked for the second annotator")
    lt.add_argument("--name", help="batch name (keeps separate progress in the browser)")
    lt.add_argument("-o", "--out", required=True)
    lt.set_defaults(func=cmd_labeltool)

    ck = ds.add_parser("check", help="validate a labels file against the snapshot")
    ck.add_argument("snapshot")
    ck.add_argument("labels")
    ck.set_defaults(func=cmd_check)

    ag = ds.add_parser("agreement", help="inter-annotator agreement per signal")
    ag.add_argument("snapshot")
    ag.add_argument("first")
    ag.add_argument("second")
    ag.add_argument("--out", help="also write the full report as JSON")
    ag.add_argument("--markdown", action="store_true", help="print a table for the dataset card")
    ag.set_defaults(func=cmd_agreement)

    gd = ds.add_parser("gold", help="gold labels: primary annotator, overridden by adjudication")
    gd.add_argument("primary", nargs="+", help="the primary annotator's label files")
    gd.add_argument("--adjudicated", help="records deciding disagreements (only the fields decided)")
    gd.add_argument("-o", "--out", required=True)
    gd.set_defaults(func=cmd_gold)

    er = ds.add_parser("errors", help="most confident signal and retrieval mistakes, for the error analysis")
    er.add_argument("snapshot")
    er.add_argument("gold")
    er.add_argument("--retrieval", help="output of `spashta eval QUESTIONS --out FILE` (hybrid)")
    er.add_argument("--per-signal", type=int, default=5)
    er.add_argument("-o", "--out", required=True)
    er.set_defaults(func=cmd_errors)

    lc = ds.add_parser("llm-compare", help="LLM claim review per provider: agreement with labels, cost, latency")
    lc.add_argument("snapshot")
    lc.add_argument("gold")
    lc.add_argument("--providers", nargs="+", required=True, help="provider or provider:model, keys from the environment")
    lc.add_argument("--price", nargs="*", default=[], help="SPEC=IN/OUT USD per million tokens, e.g. gemini:gemini-2.5-flash=0.30/2.50")
    lc.add_argument("--n", type=int, default=50)
    lc.add_argument("--pause", type=float, default=4.0, help="seconds between calls (free tiers limit requests per minute)")
    lc.add_argument("--out")
    lc.set_defaults(func=cmd_llm_compare)

    r = ds.add_parser("release", help="write the redistributable dataset (no article text)")
    r.add_argument("snapshot")
    r.add_argument("out")
    r.add_argument("--labels", help="gold labels (JSONL, from `dataset gold`)")
    r.add_argument("--pairs", help="gold same-event pairs (JSONL)")
    r.add_argument("--queries", help="judged retrieval queries (from `qrels build`)")
    r.add_argument("--sample", help="the labelling sample (adds topic tags)")
    r.add_argument("--card", help="dataset card to include as README.md")
    r.set_defaults(func=cmd_release)

    h = ds.add_parser("rehydrate", help="re-fetch a release's articles and re-anchor its labels")
    h.add_argument("release")
    h.add_argument("out")
    h.add_argument("--limit", type=int)
    h.set_defaults(func=cmd_rehydrate)
