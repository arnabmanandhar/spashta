"""
Feed parsing: RSS 2.0, RSS 1.0 (RDF), Atom and JSON Feed.

Standard library only. Namespaces are stripped so `content:encoded`,
`dc:creator` and Atom elements are matched by local name.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET  # noqa: S405 - expat limits entity expansion
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional
from urllib.parse import urljoin

from ..models import FeedItem

_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG_RE.sub(" ", html or ""))).strip()


def parse_date(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip()
    try:
        d = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child(el: ET.Element, *names: str) -> Optional[ET.Element]:
    for c in el:
        if _local(c.tag) in names:
            return c
    return None


def _text(el: ET.Element, *names: str) -> str:
    c = _child(el, *names)
    return (c.text or "").strip() if c is not None else ""


_ROOT_RE = re.compile(r"<([A-Za-z][\w:.-]*)[\s>/]")


def parse_xml(text: str) -> Optional[ET.Element]:
    """Parse a feed or sitemap, tolerating what real sites append after the
    closing root tag (a PHP warning, a second document, tracking HTML).
    None when it is not XML at all."""
    data = text.lstrip()
    try:
        return ET.fromstring(data.encode("utf-8"))  # noqa: S314
    except ET.ParseError:
        pass
    body = re.sub(r"<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>", "", data, flags=re.S)
    m = _ROOT_RE.search(body)
    if not m:
        return None
    end = data.find(f"</{m.group(1)}>")
    if end < 0:
        return None
    try:
        return ET.fromstring(data[: end + len(m.group(1)) + 3].encode("utf-8"))  # noqa: S314
    except ET.ParseError:
        return None


def looks_like_feed(content_type: str, text: str) -> bool:
    head = text.lstrip()[:500].lower()
    if any(t in content_type for t in ("rss", "atom", "feed+json")):
        return True
    if content_type.endswith("json") and '"version"' in head and "jsonfeed" in head:
        return True
    return ("<rss" in head or "<feed" in head or "<rdf:rdf" in head) and "<urlset" not in head


def parse_feed(text: str, base_url: str = "") -> list[FeedItem]:
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _parse_json_feed(stripped, base_url)
    root = parse_xml(stripped)
    if root is None:
        return []
    lang = None
    channel = _child(root, "channel")
    if channel is not None:
        lang = _text(channel, "language") or None
    if lang is None:
        lang = root.get("{http://www.w3.org/XML/1998/namespace}lang")
    items = [el for el in root.iter() if _local(el.tag) in ("item", "entry")]
    out = []
    for el in items:
        link = _text(el, "link")
        if not link:  # Atom: <link rel="alternate" href="..."/>
            for c in el:
                if _local(c.tag) == "link" and c.get("rel", "alternate") == "alternate" and c.get("href"):
                    link = c.get("href")
                    break
        if not link:
            guid = _text(el, "guid", "id")
            link = guid if guid.startswith("http") else ""
        if not link:
            continue
        author = _text(el, "creator", "author")
        if not author:
            a = _child(el, "author")
            author = _text(a, "name") if a is not None else ""
        out.append(FeedItem(
            url=urljoin(base_url, link),
            title=strip_html(_text(el, "title")),
            summary=strip_html(_text(el, "description", "summary") or _text(el, "encoded", "content"))[:2000],
            published=parse_date(_text(el, "pubdate", "published", "updated", "date")),
            author=author or None,
            lang=lang,
        ))
    return out


def _parse_json_feed(text: str, base_url: str) -> list[FeedItem]:
    data = json.loads(text)
    out = []
    for it in data.get("items", []):
        link = it.get("url") or it.get("external_url") or ""
        if not link:
            continue
        authors = it.get("authors") or ([it["author"]] if it.get("author") else [])
        out.append(FeedItem(
            url=urljoin(base_url, link),
            title=strip_html(it.get("title", "")),
            summary=strip_html(it.get("summary") or it.get("content_html") or it.get("content_text") or "")[:2000],
            published=parse_date(it.get("date_published")),
            author=(authors[0].get("name") if authors and isinstance(authors[0], dict) else None),
            lang=it.get("language") or data.get("language"),
        ))
    return out
