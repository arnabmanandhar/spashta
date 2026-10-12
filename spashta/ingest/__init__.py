"""
Ingestion: sources to stored, indexed articles.

    any URL ──resolve──► feed | sitemap | links ──list_articles──► article URLs
                                                                      │
                                         fetch + extract_article ◄────┘
                                                                      │
                                                    Index.add_many ◄──┘

Feed and sitemap metadata (title, date, author, language) fill in whatever
the page itself doesn't declare. Articles already in the store are skipped,
so polling is cheap.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from ..fetch import Fetcher, FetchError
from ..models import Article, FeedItem, Source, article_id
from . import boilerplate
from .discover import list_articles, looks_like_article, resolve
from .extract import extract_article, parse_page
from .feeds import parse_feed
from .sitemap import parse_sitemap

log = logging.getLogger("spashta.ingest")
MIN_BODY_CHARS = 200

__all__ = ["IngestResult", "collect_source", "extract_article", "fetch_article", "ingest_source", "ingest_sources", "list_articles", "looks_like_article",
           "parse_feed", "parse_page", "parse_sitemap", "resolve"]


@dataclass
class IngestResult:
    source: str
    kind: str = ""
    found: int = 0
    added: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


def fetch_article(url: str, fetcher: Fetcher, hint: FeedItem | None = None) -> Article:
    r = fetcher.get(url)
    a = extract_article(r.text, r.url)
    if hint is not None:
        updates = {}
        if not a.title and hint.title:
            updates["title"] = hint.title
        if a.published is None and hint.published:
            updates["published"] = hint.published
        if not a.author and hint.author:
            updates["author"] = hint.author
        if updates:
            a = a.model_copy(update=updates)
    return a


def collect_source(source: Source, fetcher: Fetcher, store, limit: int = 30) -> tuple[IngestResult, list[Article]]:
    """Resolve the source if needed and fetch its new articles, with the
    site's boilerplate removed. Thread-safe: no index access."""
    result = IngestResult(source.url)
    try:
        if source.kind == "unresolved" or not source.endpoint:
            source = resolve(source, fetcher)
            store.add_source(source)
        result.kind = source.kind
        items = list_articles(source, fetcher, limit)
    except FetchError as e:
        result.errors.append(str(e))
        store.mark_polled(source.url, str(e))
        return result, []
    result.found = len(items)
    new: list[Article] = []
    for item in items:
        if store.seen(item.url) or store.has_article(article_id(item.url)):
            result.skipped += 1
            continue
        try:
            a = fetch_article(item.url, fetcher, item)
        except FetchError as e:
            result.errors.append(str(e))
            continue
        new.append(a)
        store.mark_seen(item.url, a.id)
    # Learn this site's repeated paragraphs from the new batch plus recent stored articles.
    known = [a.body for a in store.articles_from(source.host, limit=20)]
    bp = boilerplate.learn([a.body for a in new] + known)
    kept = []
    for a in new:
        a = boilerplate.strip(a, bp)
        if len(a.body) < MIN_BODY_CHARS:  # section page, paywall or video: nothing to analyse
            result.skipped += 1
            continue
        kept.append(a)
    store.mark_polled(source.url, "; ".join(result.errors[:3]) or None)
    return result, kept


def ingest_source(source: Source, fetcher: Fetcher, index, limit: int = 30) -> IngestResult:
    """Collect one source and add its new articles to the index."""
    result, articles = collect_source(source, fetcher, index.store, limit)
    result.added = index.add_many(articles) if articles else 0
    log.info("%s: %d found, %d added, %d skipped, %d errors", source.url, result.found, result.added,
             result.skipped, len(result.errors))
    return result


def ingest_sources(sources: list[Source], fetcher: Fetcher, index, limit: int = 30, workers: int = 4) -> list[IngestResult]:
    """Collect sources in parallel (Fetcher still spaces requests per host),
    then index everything in one pass."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        collected = list(pool.map(lambda s: collect_source(s, fetcher, index.store, limit), sources))
    articles = [a for _, batch in collected for a in batch]
    if articles:
        index.add_many(articles)
    for result, batch in collected:
        result.added = len(batch)
    return [r for r, _ in collected]
