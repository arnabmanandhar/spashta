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
from dataclasses import dataclass, field

from ..fetch import Fetcher, FetchError
from ..models import Article, FeedItem, Source, article_id
from .discover import list_articles, looks_like_article, resolve
from .extract import extract_article, parse_page
from .feeds import parse_feed
from .sitemap import parse_sitemap

log = logging.getLogger("spashta.ingest")
MIN_BODY_CHARS = 200

__all__ = ["IngestResult", "extract_article", "fetch_article", "ingest_source", "list_articles", "looks_like_article",
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


def ingest_source(source: Source, fetcher: Fetcher, index, limit: int = 30) -> IngestResult:
    """Resolve the source if needed, fetch new articles, add them to the index."""
    store = index.store
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
        return result
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
        if len(a.body) < MIN_BODY_CHARS:  # section page, paywall or video: nothing to analyse
            store.mark_seen(item.url, None)
            result.skipped += 1
            continue
        store.mark_seen(item.url, a.id)
        new.append(a)
    result.added = index.add_many(new) if new else 0
    store.mark_polled(source.url, "; ".join(result.errors[:3]) or None)
    log.info("%s: %d found, %d added, %d skipped, %d errors", source.url, result.found, result.added,
             result.skipped, len(result.errors))
    return result
