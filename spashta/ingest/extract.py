"""
Article extraction from any web page.

Metadata comes from what publishers already declare: JSON-LD
(NewsArticle / Article / BlogPosting), OpenGraph and article: meta tags,
<html lang>, <link rel=canonical> and <title>. The body comes from
trafilatura when it is installed (the best maintained boilerplate remover,
and it handles Devanagari well) and otherwise from a built-in fallback that
keeps the paragraphs inside <article>/<main>, or failing that every
paragraph outside navigation, headers, footers and sidebars.

The same parser also lists a page's links and advertised feeds, which
discovery uses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin

from .. import lang as L
from ..bsdate import parse_bs_date
from ..models import Article, host_of
from .feeds import parse_date

FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json", "application/json")
_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form", "button", "figure"}
_BLOCK = {"p", "h2", "h3", "h4", "li", "blockquote"}


@dataclass
class Page:
    url: str
    lang: Optional[str] = None
    title: str = ""
    meta: dict[str, str] = field(default_factory=dict)
    canonical: Optional[str] = None
    feeds: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    jsonld: list[dict] = field(default_factory=list)
    article_paragraphs: list[str] = field(default_factory=list)
    paragraphs: list[str] = field(default_factory=list)


class _Parser(HTMLParser):
    def __init__(self, url: str):
        super().__init__(convert_charrefs=True)
        self.page = Page(url)
        self._skip = 0
        self._article = 0
        self._block: Optional[list[str]] = None
        self._in_title = False
        self._in_ld = False
        self._ld: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        p = self.page
        if tag == "html" and a.get("lang"):
            p.lang = a["lang"].split("-")[0].lower()
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in p.meta:
                p.meta[key] = unescape(a["content"]).strip()
        elif tag == "link":
            rel = a.get("rel", "").lower().split()
            href = a.get("href")
            if href and "canonical" in rel:
                p.canonical = urljoin(p.url, href)
            if href and "alternate" in rel and a.get("type", "").lower() in FEED_TYPES:
                p.feeds.append(urljoin(p.url, href))
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld, self._ld = True, []
            return
        elif tag == "title":
            self._in_title = True
        elif tag == "a" and a.get("href"):
            p.links.append(urljoin(p.url, a["href"]))
        if tag in _SKIP:
            self._skip += 1
        elif tag in ("article", "main"):
            self._article += 1
        elif tag in _BLOCK and not self._skip:
            self._block = []

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            try:
                data = json.loads("".join(self._ld))
            except ValueError:
                data = None
            for d in (data if isinstance(data, list) else [data]):
                if isinstance(d, dict):
                    self.page.jsonld.extend(d.get("@graph", [d]) if "@graph" in d else [d])
            return
        if tag == "title":
            self._in_title = False
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
        elif tag in ("article", "main"):
            self._article = max(0, self._article - 1)
        elif tag in _BLOCK and self._block is not None:
            text = re.sub(r"\s+", " ", "".join(self._block)).strip()
            self._block = None
            if len(text) >= 25:
                self.page.paragraphs.append(text)
                if self._article:
                    self.page.article_paragraphs.append(text)

    def handle_data(self, data):
        if self._in_ld:
            self._ld.append(data)
        elif self._in_title:
            self.page.title += data
        elif self._block is not None and not self._skip:
            self._block.append(data)


def parse_page(html: str, url: str) -> Page:
    parser = _Parser(url)
    parser.feed(html)
    parser.close()
    parser.page.title = re.sub(r"\s+", " ", parser.page.title).strip()
    return parser.page


def _news_ld(page: Page) -> dict:
    for d in page.jsonld:
        t = d.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in ("NewsArticle", "Article", "BlogPosting", "ReportageNewsArticle", "AnalysisNewsArticle",
                     "OpinionNewsArticle") for x in types):
            return d
    return {}


def _author(ld: dict, page: Page) -> Optional[str]:
    a = ld.get("author")
    if isinstance(a, list) and a:
        a = a[0]
    if isinstance(a, dict):
        a = a.get("name")
    return (a if isinstance(a, str) and a.strip() else None) or page.meta.get("author") or page.meta.get("article:author")


def _body_trafilatura(html: str, url: str) -> Optional[str]:
    try:
        import trafilatura
    except ImportError:
        return None
    try:
        return trafilatura.extract(html, url=url, include_comments=False, include_tables=False, favor_precision=True)
    except Exception:  # noqa: BLE001 - extraction failure falls back to the built-in parser
        return None


def extract_article(html: str, url: str, use_trafilatura: bool = True) -> Article:
    page = parse_page(html, url)
    ld = _news_ld(page)
    title = (ld.get("headline") or page.meta.get("og:title") or page.meta.get("twitter:title") or page.title or "").strip()
    body = (_body_trafilatura(html, url) if use_trafilatura else None) or ""
    if len(body) < 200:
        paras = page.article_paragraphs or page.paragraphs
        fallback = "\n\n".join(p for p in paras if p != title)
        if len(fallback) > len(body):
            body = fallback
    if not body:
        body = ld.get("articleBody") or page.meta.get("og:description") or page.meta.get("description") or ""
    final_url = page.canonical or url
    published = parse_date(ld.get("datePublished") or page.meta.get("article:published_time")
                           or page.meta.get("pubdate") or page.meta.get("date"))
    if published is None:  # many Nepali pages show only a Bikram Sambat date
        published = parse_bs_date(" ".join([page.meta.get("article:published_time", ""), title, body[:600]]))
    text = f"{title}\n{body}"
    detected = L.detect_language(text)
    return Article(
        url=final_url,
        title=L.clean(unescape(title)),
        body=L.clean(body.strip()),
        source=host_of(final_url),
        lang=detected if detected != "unknown" else (page.lang or "unknown"),
        published=published,
        author=_author(ld, page),
        summary=page.meta.get("og:description") or page.meta.get("description"),
    )
