"""
Command line.

  spashta demo                         index the bundled samples and print two reports
  spashta sources add URL [--name N]   add any news site, blog or feed
  spashta sources seed                 add the bundled list of Nepali and English outlets
  spashta sources list                 sources with their resolved kind and article counts
  spashta ingest [--limit N]           poll every enabled source and index new articles
  spashta ingest --since 2026-09-15    backfill a date window from the sites' sitemaps
  spashta dataset ...                  snapshots, sampling, labels, agreement, release (spashta dataset -h)
  spashta qrels ...                    pooled relevance judgments for retrieval queries (spashta qrels -h)
  spashta analyze URL|--file F|--text  integrity report for one article
  spashta search QUERY                 hybrid search over the index
  spashta eval [QUESTIONS]             retrieval metrics (Anchor) over a question set

Common options: --db PATH (default $SPASHTA_DB or spashta.db), --embedder NAME
(hashing, e5-small, e5-base, e5-large, bge-m3; default $SPASHTA_EMBEDDER or hashing).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import lang as L
from .embed import get_embedder
from .fetch import Fetcher, FetchError
from .ingest import fetch_article, ingest_sources, resolve
from .models import Article, Source, host_of
from .report import analyze
from .retrieve import Index
from .samples import sample_articles, sample_articles_path, sample_questions_path, seed_sources
from .store import Store


def _index(args) -> Index:
    reranker = None
    if os.environ.get("SPASHTA_RERANKER"):
        from .embed import CrossEncoderReranker
        reranker = CrossEncoderReranker(os.environ["SPASHTA_RERANKER"])
    return Index(Store(args.db), get_embedder(args.embedder), reranker)


def _print_report(report, as_json: bool) -> None:
    if as_json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(report.to_markdown())


def cmd_demo(args) -> int:
    index = Index(Store(":memory:"), get_embedder(args.embedder))
    articles = sample_articles()
    index.add_many(articles)
    print(f"Indexed {len(articles)} synthetic sample articles ({index.embedder.name}).\n")
    by_url = {a.url: a for a in articles}
    for url in ("https://khabar-sample.example/samachar/2083/06/101234",
                "https://sanoportal.example/2083/06/10/bhayanak-pahiro-50-janako-mrityu"):
        _print_report(analyze(by_url[url], index, use_llm=args.llm), args.json)
    return 0


def cmd_sources(args) -> int:
    store = Store(args.db)
    if args.action == "add":
        store.add_source(Source(url=args.url, name=args.name or host_of(args.url), lang=args.lang))
        if args.resolve:
            try:
                s = resolve(Source(url=args.url, name=args.name or host_of(args.url), lang=args.lang), Fetcher())
                store.add_source(s)
                print(f"{s.url}: {s.kind} ({s.endpoint})")
            except FetchError as e:
                print(f"added, but could not resolve yet: {e}", file=sys.stderr)
        else:
            print(f"added {args.url}")
    elif args.action == "seed":
        for s in seed_sources():
            store.add_source(s)
        print(f"added {len(seed_sources())} sources")
    else:
        for row in store.source_status():
            err = f"  ! {row['last_error']}" if row["last_error"] else ""
            print(f"{row['kind']:<10} {row['articles']:>5}  {row['url']}  {row['name'] or ''}{err}")
    return 0


def parse_day(value: str | None, end: bool = False):
    """2026-09-15 -> that day's start (or end) in Nepal time; full ISO timestamps pass through."""
    if not value:
        return None
    from datetime import datetime, time, timedelta, timezone
    nepal = timezone(timedelta(hours=5, minutes=45))
    if len(value) == 10:
        d = datetime.combine(datetime.fromisoformat(value).date(), time.max if end else time.min)
        return d.replace(tzinfo=nepal)
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=nepal)


def cmd_ingest(args) -> int:
    index = _index(args)
    fetcher = Fetcher(delay_s=args.delay, cache=index.store)
    sources = index.store.sources(enabled_only=True)
    since, until = parse_day(args.since), parse_day(args.until, end=True)
    limit = args.limit if args.limit is not None else (5000 if since else 30)
    results = ingest_sources(sources, fetcher, index, limit=limit, workers=args.workers, since=since, until=until)
    for s, r in zip(sources, results):
        print(f"{r.kind or '?':<8} found {r.found:>3} · added {r.added:>3} · skipped {r.skipped:>3}"
              f" · errors {len(r.errors):>2}  {s.url}")
    print(json.dumps(index.store.count()))
    failed = sum(bool(r.errors and not r.added) for r in results)
    return 1 if sources and failed == len(sources) else 0


def _article_from_args(args, fetcher: Fetcher) -> Article:
    if args.text is not None:
        text = sys.stdin.read() if args.text == "-" else args.text
        title, _, body = text.strip().partition("\n")
        return Article(url="text:", title=L.clean(title), body=L.clean(body.strip()), lang=L.detect_language(text))
    if args.file:
        from .ingest import extract_article
        with open(args.file, encoding="utf-8") as f:
            return extract_article(f.read(), args.url or "file://" + os.path.abspath(args.file))
    return fetch_article(args.url, fetcher)


def cmd_analyze(args) -> int:
    index = _index(args)
    try:
        article = _article_from_args(args, Fetcher())
    except FetchError as e:
        print(f"could not fetch: {e}", file=sys.stderr)
        return 2
    if args.save and article.url.startswith("http"):
        index.add(article)
    _print_report(analyze(article, index, use_llm=args.llm), args.json)
    return 0


def cmd_search(args) -> int:
    index = _index(args)
    for h in index.search(args.query, k=args.k):
        a = index.store.article(h.chunk.article_id)
        terms = f" [{', '.join(h.matched_terms)}]" if h.matched_terms else ""
        print(f"{h.score:.4f}  {a.source if a else ''}  {a.title if a else h.chunk.article_id}{terms}")
        print(f"         {h.chunk.text[:160]}")
    return 0


def cmd_eval_gold(args) -> int:
    from .dataset import labels as LB
    from .dataset import signal_eval, snapshot
    if not args.snapshot:
        build_parser().error("--gold needs --snapshot (the labelled articles' text)")
    records = {r["id"]: r for r in snapshot.read_records(args.snapshot)}
    gold = LB.read(args.gold)
    report = signal_eval.evaluate(records, gold)
    if args.pairs:
        report["corroboration"] = signal_eval.evaluate_pairs(records, LB.read(args.pairs))
    report["suggested_thresholds"] = signal_eval.suggested_thresholds(report)
    if args.write_thresholds:
        with open(args.write_thresholds, "w", encoding="utf-8") as f:
            json.dump(report["suggested_thresholds"], f, indent=2)
    return _emit(report, args.out)


def cmd_eval(args) -> int:
    from .evaluate import compare_modes, evaluate_retrieval
    if args.gold:
        return cmd_eval_gold(args)
    if args.questions:
        index = _index(args)
    else:
        index = Index(Store(":memory:"), get_embedder(args.embedder))
        index.add_many(sample_articles())
    questions = args.questions or sample_questions_path()
    if args.signals:
        from .evaluate import evaluate_signals
        print(json.dumps(evaluate_signals(args.signals if args.signals != "samples" else sample_articles_path(), index),
                         ensure_ascii=False, indent=2))
        return 0
    if args.ablate_xling:
        from .evaluate import ablate_xling
        return _emit(ablate_xling(index.store, index.embedder, questions, k=args.k), args.out)
    if args.compare:
        return _emit(compare_modes(index, questions, k=args.k), args.out)
    result = evaluate_retrieval(index, questions, k=args.k, records_out=args.records, mode=args.mode)
    return _emit({k: v for k, v in result.items() if k != "per_question" or args.verbose or args.out}, args.out)


def _emit(result: dict, out: str | None) -> int:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="spashta", description="News integrity reports for Nepali and English.")
    p.add_argument("--db", default=os.environ.get("SPASHTA_DB", "spashta.db"))
    p.add_argument("--embedder", default=os.environ.get("SPASHTA_EMBEDDER", "hashing"))
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("demo", help="report on the bundled samples")
    d.add_argument("--json", action="store_true")
    d.add_argument("--llm", action="store_true", help="add the LLM claim review (needs a provider key)")
    d.set_defaults(func=cmd_demo)

    s = sub.add_parser("sources", help="manage sources")
    s.add_argument("action", choices=["add", "list", "seed"])
    s.add_argument("url", nargs="?")
    s.add_argument("--name")
    s.add_argument("--lang", choices=["ne", "en"])
    s.add_argument("--resolve", action="store_true", help="find the feed or sitemap now")
    s.set_defaults(func=cmd_sources)

    i = sub.add_parser("ingest", help="poll sources and index new articles")
    i.add_argument("--limit", type=int, help="articles per source (default 30; 5000 with --since)")
    i.add_argument("--since", help="backfill articles published on or after this date (YYYY-MM-DD) from sitemaps")
    i.add_argument("--until", help="end of the backfill window (YYYY-MM-DD, default now)")
    i.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host")
    i.add_argument("--workers", type=int, default=4, help="sources fetched in parallel")
    i.set_defaults(func=cmd_ingest)

    a = sub.add_parser("analyze", help="integrity report for one article")
    a.add_argument("url", nargs="?")
    a.add_argument("--file", help="a saved HTML page")
    a.add_argument("--text", help="headline on the first line, then the story; '-' reads stdin")
    a.add_argument("--json", action="store_true")
    a.add_argument("--llm", action="store_true")
    a.add_argument("--save", action="store_true", help="also add the article to the index")
    a.set_defaults(func=cmd_analyze)

    q = sub.add_parser("search", help="hybrid search")
    q.add_argument("query")
    q.add_argument("-k", type=int, default=5)
    q.set_defaults(func=cmd_search)

    e = sub.add_parser("eval", help="retrieval metrics via Anchor")
    e.add_argument("questions", nargs="?")
    e.add_argument("-k", type=int, default=5)
    e.add_argument("--records", help="also write Anchor records (JSONL) here")
    e.add_argument("--mode", choices=["hybrid", "lexical", "dense", "xling"], default="hybrid")
    e.add_argument("--compare", action="store_true", help="ablation: lexical vs dense vs hybrid, with significance")
    e.add_argument("--ablate-xling", action="store_true", help="remove romanisation, phonetic keys, glossary one at a time")
    e.add_argument("--out", help="also write the result as JSON (for `spashta results`)")
    e.add_argument("--gold", help="gold article labels (JSONL): AUC, precision/recall and calibrated thresholds per signal")
    e.add_argument("--snapshot", help="snapshot holding the labelled articles (with --gold)")
    e.add_argument("--pairs", help="gold same-event pair labels (with --gold): matcher precision/recall")
    e.add_argument("--write-thresholds", help="with --gold: write suggested thresholds here (copy into data/thresholds.json)")
    e.add_argument("--signals", nargs="?", const="samples", help="AUC of each signal against labelled articles (default: samples)")
    e.set_defaults(func=cmd_eval)

    from .cli_dataset import add_dataset_commands
    add_dataset_commands(sub)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")
    if args.cmd == "sources" and args.action == "add" and not args.url:
        build_parser().error("sources add needs a URL")
    if args.cmd == "analyze" and not (args.url or args.file or args.text is not None):
        build_parser().error("analyze needs a URL, --file or --text")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
