"""
Sitemap parsing: `<urlset>` pages and `<sitemapindex>` indexes, including
Google News sitemap fields (title, language, publication date).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET  # noqa: S405 - expat limits entity expansion
from dataclasses import dataclass, field

from ..models import FeedItem
from .feeds import _child, _local, _text, parse_date


@dataclass
class Sitemap:
    entries: list[FeedItem] = field(default_factory=list)
    children: list[str] = field(default_factory=list)  # nested sitemaps from an index


def looks_like_sitemap(text: str) -> bool:
    head = text.lstrip()[:1000].lower()
    return "<urlset" in head or "<sitemapindex" in head


def parse_sitemap(text: str) -> Sitemap:
    root = ET.fromstring(text.lstrip().encode("utf-8"))  # noqa: S314
    out = Sitemap()
    if _local(root.tag) == "sitemapindex":
        out.children = [_text(s, "loc") for s in root if _local(s.tag) == "sitemap" and _text(s, "loc")]
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
