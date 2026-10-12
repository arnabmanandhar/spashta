"""
Command line.

  spashta demo                         index the bundled samples and print two reports
  spashta sources add URL [--name N]   add any news site, blog or feed
  spashta sources seed                 add the bundled list of Nepali and English outlets
  spashta sources list                 sources with their resolved kind and article counts
  spashta ingest [--limit N]           poll every enabled source and index new articles
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
from .ingest import fetch_article, ingest_source, resolve
from .models import Article, Source, host_of
from .report import analyze
from .retrieve import Index
from .samples import sample_articles, sample_questions_path, seed_sources
from .store import Store


def _index(args) -> Index:
    return Index(Store(args.db), get_embedder(args.embedder))


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


def cmd_ingest(args) -> int:
    index = _index(args)
    fetcher = Fetcher(delay_s=args.delay)
    failed = 0
    for s in index.store.sources(enabled_only=True):
        r = ingest_source(s, fetcher, index, limit=args.limit)
        failed += bool(r.errors and not r.added)
        print(f"{r.kind or '?':<8} found {r.found:>3} · added {r.added:>3} · skipped {r.skipped:>3}"
              f" · errors {len(r.errors):>2}  {s.url}")
    print(json.dumps(index.store.count()))
    return 1 if failed and failed == len(index.store.sources(enabled_only=True)) else 0


def _article_from_args(args, fetcher: Fetcher) -> Article:
    if args.text is not None:
        text = sys.stdin.read() if args.text == "-" else args.text
        title, _, body = text.strip().partition("\n")
        return Article(url="text:", title=L.normalize(title), body=L.normalize(body.strip()), lang=L.detect_language(text))
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


def cmd_eval(args) -> int:
    from .evaluate import evaluate_retrieval
    if args.questions:
        index = _index(args)
    else:
        index = Index(Store(":memory:"), get_embedder(args.embedder))
        index.add_many(sample_articles())
    result = evaluate_retrieval(index, args.questions or sample_questions_path(), k=args.k, records_out=args.records)
    print(json.dumps({k: v for k, v in result.items() if k != "per_question" or args.verbose}, ensure_ascii=False, indent=2))
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
    i.add_argument("--limit", type=int, default=30, help="articles per source")
    i.add_argument("--delay", type=float, default=1.0, help="seconds between requests to one host")
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
    e.set_defaults(func=cmd_eval)
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
