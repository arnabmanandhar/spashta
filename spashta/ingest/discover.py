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

import html
import json
import re
from calendar import monthrange
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode, urljoin, urlparse

from ..fetch import Fetcher, FetchError
from ..models import FeedItem, Source, canonical_url, host_of
from .extract import parse_page
from .feeds import looks_like_feed, parse_date, parse_feed
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
    text = fetcher.get(url, conditional=True).text
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
        r = fetcher.get(source.endpoint, conditional=True)
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


_DATE_IN_URL = re.compile(r"(20\d\d)[-_/]?(0[1-9]|1[0-2])(?:[-_/]?([0-3]\d))?(?!\d)")


def _url_period(url: str) -> Optional[tuple[datetime, datetime]]:
    """The month (or day) a dated sitemap or article URL covers:
    sitemap-2026-09.xml, post-sitemap2026_09.xml, /2026/09/14/slug."""
    m = _DATE_IN_URL.search(urlparse(url).path)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    if m.group(3) and 1 <= int(m.group(3)) <= monthrange(year, month)[1]:
        day = int(m.group(3))
        return (datetime(year, month, day, tzinfo=timezone.utc), datetime(year, month, day, 23, 59, 59, tzinfo=timezone.utc))
    return (datetime(year, month, 1, tzinfo=timezone.utc),
            datetime(year, month, monthrange(year, month)[1], 23, 59, 59, tzinfo=timezone.utc))


def _overlaps(period: Optional[tuple[datetime, datetime]], since: datetime, until: datetime) -> bool:
    return period is None or (period[0] <= until and period[1] >= since)


_PAGE_PARAM = re.compile(r"[?&](?:p|page|paged)=(\d+)")
WP_MAX_PAGES = 60


def _base(url: str) -> str:
    return url.split("?", 1)[0]


def _order_children(children: list[str]) -> tuple[list[str], set[str]]:
    """Child sitemaps in the order to walk them, and the bases that are
    paged (sitemap-news.xml, then ?p=2, ?p=3, ...). Paged sitemaps list
    newest first and hold no dates in their names, so they are walked in
    page order, ahead of the rest, until a page is older than the window.
    Other children keep the old rule: newest last in most indexes."""
    groups: dict[str, list[str]] = {}
    for c in children:
        groups.setdefault(_base(c), []).append(c)
    paged = {b for b, cs in groups.items() if len(cs) > 1 and any(_PAGE_PARAM.search(c) for c in cs)}

    def page_no(url: str) -> int:
        m = _PAGE_PARAM.search(url)
        return int(m.group(1)) if m else 1

    first = sorted((c for c in children if _base(c) in paged), key=lambda c: (_base(c), page_no(c)))
    return first + [c for c in reversed(children) if _base(c) not in paged], paged


def wordpress_items(source: Source, fetcher: Fetcher, since: datetime, until: datetime,
                    limit: int = 5000) -> list[FeedItem]:
    """Posts in the window from a WordPress site's REST API, for sites that
    publish no usable sitemap. Returns [] for sites that are not WordPress
    or have the API switched off."""
    base = source.url.rstrip("/")
    out: list[FeedItem] = []
    for page in range(1, WP_MAX_PAGES + 1):
        query = urlencode({"per_page": 100, "page": page, "orderby": "date", "order": "asc",
                           "after": since.astimezone(timezone.utc).isoformat(),
                           "before": until.astimezone(timezone.utc).isoformat(), "_fields": "link,date_gmt,title"})
        try:
            posts = json.loads(fetcher.get(f"{base}/wp-json/wp/v2/posts?{query}").text)
        except (FetchError, ValueError):
            break  # not WordPress, API off, or past the last page (WordPress answers 400)
        if not isinstance(posts, list) or not posts:
            break
        for post in posts:
            link = post.get("link") if isinstance(post, dict) else None
            if not link or not looks_like_article(link, source.host):
                continue
            title = post.get("title")
            out.append(FeedItem(url=canonical_url(link),
                                title=html.unescape(title.get("rendered", "")) if isinstance(title, dict) else "",
                                published=parse_date(post.get("date_gmt"))))
        if len(posts) < 100 or len(out) >= limit:
            break
    return out[:limit]


def backfill_items(source: Source, fetcher: Fetcher, since: datetime, until: Optional[datetime] = None,
                   limit: int = 5000, max_sitemaps: int = 80) -> list[FeedItem]:
    """Article URLs published between since and until, for building a corpus
    that reaches back weeks. Feeds only list the last day or two, so this
    walks the site's sitemaps, skipping child sitemaps that by their lastmod
    or the date in their name can't hold anything in the window. Sites
    whose sitemaps hold nothing in the window try the WordPress API, then
    fall back to the feed or the home page (recent only)."""
    until = until or datetime.now(timezone.utc)
    if source.kind == "sitemap" and source.endpoint:
        roots = [source.endpoint]
    else:
        roots = fetcher.sitemaps_from_robots(source.url) + [urljoin(source.url, p) for p in COMMON_SITEMAPS]
    queue, seen_maps, fetched, parsed = list(dict.fromkeys(roots)), set(), 0, 0
    found: dict[str, FeedItem] = {}
    paged: set[str] = set()
    done_bases: set[str] = set()  # paged sitemaps already walked past the window
    while queue and fetched < max_sitemaps and len(found) < limit:
        url = queue.pop(0)
        if url in seen_maps or _base(url) in done_bases:
            continue
        seen_maps.add(url)
        try:
            text = fetcher.get(url).text
            fetched += 1
            if not looks_like_sitemap(text):
                continue
            sm = parse_sitemap(text)
            parsed += 1
        except (FetchError, ValueError, SyntaxError):  # ET.ParseError is a SyntaxError
            continue
        children, new_paged = _order_children(sm.children)
        paged |= new_paged
        wanted = [c for c in children if (sm.child_lastmod.get(c) is None or sm.child_lastmod[c] >= since)
                  and _overlaps(_url_period(c), since, until)]
        queue = [c for c in wanted if _base(c) in new_paged] + queue + [c for c in wanted if _base(c) not in new_paged]
        dated = [i.published for i in sm.entries if i.published]
        if _base(url) in paged and dated and len(dated) == len(sm.entries) and max(dated) < since:
            done_bases.add(_base(url))
        for item in sm.entries:
            when = item.published
            if when is not None and not since <= when <= until:
                continue
            if when is None and not (_url_period(item.url) and _overlaps(_url_period(item.url), since, until)):
                continue
            found.setdefault(canonical_url(item.url), item.model_copy(update={"url": canonical_url(item.url)}))
    if not found:
        wp = wordpress_items(source, fetcher, since, until, limit)
        if wp:
            return sorted(wp, key=lambda i: i.published.timestamp() if i.published else 0)
    if not parsed:
        recent = list_articles(source, fetcher, limit)
        return [i for i in recent if i.published is None or since <= i.published <= until][:limit]
    return sorted(found.values(), key=lambda i: i.published.timestamp() if i.published else 0)[:limit]


def resolve_safely(source: Source, fetcher: Fetcher) -> tuple[Source, Optional[str]]:
    try:
        return resolve(source, fetcher), None
    except FetchError as e:
        return source, str(e)
