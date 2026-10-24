"""
Sitemap parsing: `<urlset>` pages and `<sitemapindex>` indexes, including
Google News sitemap fields (title, language, publication date).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from ..models import FeedItem
from .feeds import _child, _local, _text, parse_date, parse_xml


@dataclass
class Sitemap:
    entries: list[FeedItem] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # nested sitemaps from an index
    child_lastmod: dict[str, datetime] = field(default_factory=dict)  # when an index says a child last changed


def looks_like_sitemap(text: str) -> bool:
    head = text.lstrip()[:1000].lower()
    return "<urlset" in head or "<sitemapindex" in head


def parse_sitemap(text: str) -> Sitemap:
    root = parse_xml(text)
    out = Sitemap()
    if root is None:
        return out
    if _local(root.tag) == "sitemapindex":
        for s in root:
            loc = _text(s, "loc") if _local(s.tag) == "sitemap" else ""
            if loc:
                out.children.append(loc)
                lastmod = parse_date(_text(s, "lastmod"))
                if lastmod:
                    out.child_lastmod[loc] = lastmod
        return out
    for u in root:
        if _local(u.tag) != "url":
            continue
        loc = _text(u, "loc")
        if not loc:
            continue
        item = FeedItem(url=loc, published=parse_date(_text(u, "lastmod")))
        news = _child(u, "news")
        if news is not None:
            item.title = _text(news, "title")
            item.published = parse_date(_text(news, "publication_date")) or item.published
            pub = _child(news, "publication")
            if pub is not None:
                item.lang = _text(pub, "language") or None
        out.entries.append(item)
    return out


def sitemap_items(text: str, newest_first: bool = True) -> list[FeedItem]:
    items = parse_sitemap(text).entries
    if newest_first:
        items.sort(key=lambda i: i.published.timestamp() if i.published else 0, reverse=True)
    return items
