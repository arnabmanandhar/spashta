"""Data shapes shared by ingestion, storage, retrieval and analysis."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse, urlunparse

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def canonical_url(url: str) -> str:
    """Lowercase scheme and host, no fragment, no tracking parameters,
    no trailing slash, so one article has one id however it was linked."""
    p = urlparse(url.strip())
    query = "&".join(q for q in p.query.split("&") if q and not q.lower().startswith(("utm_", "fbclid", "gclid")))
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower(), path, "", query, ""))


def host_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def article_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode(), usedforsecurity=False).hexdigest()[:16]


class Source(BaseModel):
    """Anything a user can point Spashta at: a news site, a blog, a feed."""

    url: str
    name: str = ""
    lang: Optional[str] = None  # hint; each article is still detected
    kind: str = "unresolved"  # feed | sitemap | links | unresolved
    endpoint: Optional[str] = None  # the feed or sitemap URL once resolved
    enabled: bool = True

    @property
    def host(self) -> str:
        return host_of(self.url)


class Article(BaseModel):
    url: str
    title: str
    body: str
    source: str = ""  # host the article came from
    lang: str = "unknown"
    published: Optional[datetime] = None
    author: Optional[str] = None
    summary: Optional[str] = None
    fetched_at: datetime = Field(default_factory=utcnow)

    @property
    def id(self) -> str:
        return article_id(self.url)

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.body}"


class Chunk(BaseModel):
    id: str
    article_id: str
    idx: int
    text: str
    lang: str = "unknown"


class FeedItem(BaseModel):
    url: str
    title: str = ""
    summary: str = ""
    published: Optional[datetime] = None
    author: Optional[str] = None
    lang: Optional[str] = None
