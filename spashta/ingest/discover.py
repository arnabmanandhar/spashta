"""
Turning any URL into a way of listing articles.

resolve() tries, cheapest first:

  1. the URL is itself a feed (RSS, Atom, JSON Feed)
  2. the page advertises a feed with <link rel="alternate">
  3. a common feed path answers (/feed, /rss, /rss.xml, /feed.xml, /atom.xml)
  4. robots.txt lists a sitemap, or /sitemap.xml exists
  5. otherwise: same-site links on the page that look like articles

list_articles() then returns article URLs (with whatever the feed or
sitemap already says about them) for the resolved source.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

from ..fetch import Fetcher, FetchError
from ..models import FeedItem, Source, canonical_url, host_of
from .extract import parse_page
from .feeds import looks_like_feed, parse_feed
from .sitemap import looks_like_sitemap, parse_sitemap, sitemap_items

COMMON_FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/index.xml")
COMMON_SITEMAPS = ("/sitemap.xml", "/news-sitemap.xml", "/sitemap_index.xml")

_NOT_ARTICLE = re.compile(
    r"/(tag|tags|category|categories|author|authors|page|search|login|register|signup|about|contact|privacy|terms|"
    r"advertise|subscribe|epaper|video|videos|gallery|photo|photos|feed|rss|wp-admin|wp-login|cdn-cgi)(/|$)", re.I)
_ASSET = re.compile(r"\.(jpg|jpeg|png|gif|webp|svg|pdf|mp3|mp4|zip|css|js|xml)$", re.I)


def looks_like_article(url: str, site_host: str) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or host_of(url) != site_host:
        return False
    path = p.path.rstrip("/")
    if not path or _NOT_ARTICLE.search(path + "/") or _ASSET.search(path):
        return False
    last = path.rsplit("/", 1)[-1]
    # Article URLs end in an id or a long slug: /2026/10/123456, /news/some-long-headline-slug
    return bool(re.search(r"\d{3,}", path)) or last.count("-") >= 3 or len(last) >= 30


def resolve(source: Source, fetcher: Fetcher) -> Source:
    url = source.url
    r = fetcher.get(url)
    if looks_like_feed(r.content_type, r.text):
        return source.model_copy(update={"kind": "feed", "endpoint": r.url})
    if looks_like_sitemap(r.text):
        return source.model_copy(update={"kind": "sitemap", "endpoint": r.url})
    page = parse_page(r.text, r.url)
    candidates = page.feeds + [urljoin(r.url, p) for p in COMMON_FEED_PATHS]
    for feed in dict.fromkeys(candidates):
        try:
            fr = fetcher.get(feed)
        except FetchError:
            continue
        if looks_like_feed(fr.content_type, fr.text) and parse_feed(fr.text, fr.url):
            return source.model_copy(update={"kind": "feed", "endpoint": fr.url, "lang": source.lang or page.lang})
    sitemaps = fetcher.sitemaps_from_robots(r.url) + [urljoin(r.url, p) for p in COMMON_SITEMAPS]
    for sm in dict.fromkeys(sitemaps):
        try:
            sr = fetcher.get(sm)
        except FetchError:
            continue
        if looks_like_sitemap(sr.text):
            return source.model_copy(update={"kind": "sitemap", "endpoint": sr.url, "lang": source.lang or page.lang})
    return source.model_copy(update={"kind": "links", "endpoint": r.url, "lang": source.lang or page.lang})


def _sitemap_walk(url: str, fetcher: Fetcher, limit: int, depth: int = 0) -> list[FeedItem]:
    text = fetcher.get(url).text
    sm = parse_sitemap(text)
    if sm.entries:
        return sitemap_items(text)[:limit]
    out: list[FeedItem] = []
    # Indexes usually list newest child sitemaps last or name them by date; try the last few first.
    for child in reversed(sm.children[-5:]):
        if depth >= 2 or len(out) >= limit:
            break
        try:
            out.extend(_sitemap_walk(child, fetcher, limit - len(out), depth + 1))
        except FetchError:
            continue
    return out


def list_articles(source: Source, fetcher: Fetcher, limit: int = 50) -> list[FeedItem]:
    if source.kind == "unresolved" or not source.endpoint:
        source = resolve(source, fetcher)
    if source.kind == "feed":
        r = fetcher.get(source.endpoint)
        items = parse_feed(r.text, r.url)
    elif source.kind == "sitemap":
        items = _sitemap_walk(source.endpoint, fetcher, limit)
    else:
        r = fetcher.get(source.endpoint)
        page = parse_page(r.text, r.url)
        site = host_of(r.url)
        seen: dict[str, None] = {}
        for link in page.links:
            if looks_like_article(link, site):
                seen.setdefault(canonical_url(link), None)
        items = [FeedItem(url=u) for u in seen]
    unique: dict[str, FeedItem] = {}
    for it in items:
        url = canonical_url(it.url)
        unique.setdefault(url, it.model_copy(update={"url": url}))
    return list(unique.values())[:limit]


def resolve_safely(source: Source, fetcher: Fetcher) -> tuple[Source, Optional[str]]:
    try:
        return resolve(source, fetcher), None
    except FetchError as e:
        return source, str(e)
