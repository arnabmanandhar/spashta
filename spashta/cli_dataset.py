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


def cmd_release(args) -> int:
    from .dataset import release
    m = release.build_release(args.snapshot, args.out, args.labels, args.pairs, args.queries, args.sample, args.card)
    _print({"snapshot_id": m["snapshot_id"], "files": m["files"], "out": args.out})
    return 0


def cmd_rehydrate(args) -> int:
    from .dataset import release
    _print(release.rehydrate(args.release, args.out, limit=args.limit))
    return 0


def add_dataset_commands(sub) -> None:
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
