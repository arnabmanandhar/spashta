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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..fetch import Fetcher, FetchError
from ..models import Article, FeedItem, Source, article_id
from . import boilerplate
from .discover import backfill_items, list_articles, looks_like_article, resolve
from .extract import extract_article, parse_page
from .feeds import parse_feed
from .sitemap import parse_sitemap

log = logging.getLogger("spashta.ingest")
MIN_BODY_CHARS = 200

__all__ = ["IngestResult", "backfill_items", "collect_source", "extract_article", "fetch_article", "ingest_source", "ingest_sources", "list_articles", "looks_like_article",
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


def collect_source(source: Source, fetcher: Fetcher, store, limit: int = 30, since: Optional[datetime] = None,
                   until: Optional[datetime] = None) -> tuple[IngestResult, list[Article]]:
    """Resolve the source if needed and fetch its new articles, with the
    site's boilerplate removed. With since, backfill the window from the
    site's sitemaps instead of reading only the latest feed items.
    Thread-safe: no index access."""
    result = IngestResult(source.url)
    try:
        if source.kind == "unresolved" or not source.endpoint:
            source = resolve(source, fetcher)
            store.add_source(source)
        result.kind = source.kind
        items = backfill_items(source, fetcher, since, until, limit) if since else list_articles(source, fetcher, limit)
    except Exception as e:  # noqa: BLE001 - one broken outlet must not stop the others
        msg = str(e) if isinstance(e, FetchError) else f"{type(e).__name__}: {e}"
        result.errors.append(msg)
        store.mark_polled(source.url, msg)
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
        except Exception as e:  # noqa: BLE001 - a malformed page skips that article only
            result.errors.append(f"{type(e).__name__}: {item.url}: {e}")
            continue
        new.append((item.url, a))
        store.mark_seen(item.url, a.id)
    # Learn this site's repeated paragraphs from the new batch plus recent stored articles.
    known = [a.body for a in store.articles_from(source.host, limit=20)]
    bp = boilerplate.learn([a.body for _, a in new] + known)
    kept = []
    for url, a in new:
        a = boilerplate.strip(a, bp)
        if len(a.body) < MIN_BODY_CHARS:  # section page, paywall or video: nothing to analyse
            store.mark_seen(url, None)  # remembered as skipped, not as a missing article
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


def ingest_sources(sources: list[Source], fetcher: Fetcher, index, limit: int = 30, workers: int = 4,
                   since: Optional[datetime] = None, until: Optional[datetime] = None,
                   on_done: Optional[Callable[[Source, IngestResult], None]] = None) -> list[IngestResult]:
    """Collect sources in parallel (Fetcher still spaces requests per host)
    and index each one as soon as it finishes, so an interrupted run keeps
    what it already fetched. Indexing stays on the calling thread."""
    results: list[Optional[IngestResult]] = [None] * len(sources)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(collect_source, s, fetcher, index.store, limit, since, until): i
                   for i, s in enumerate(sources)}
        for fut in as_completed(futures):
            i = futures[fut]
            result, batch = fut.result()
            if batch:
                index.add_many(batch)
            result.added = len(batch)
            results[i] = result
            if on_done:
                on_done(sources[i], result)
    return [r for r in results if r is not None]
