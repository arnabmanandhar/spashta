"""
SQLite storage: sources, articles, chunks and their vectors, in one file.

Vectors are float32 blobs tagged with the embedder that made them, so
switching embedders re-embeds instead of mixing incompatible vectors.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from .models import Article, Chunk, Source, host_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    url TEXT PRIMARY KEY, name TEXT, lang TEXT, kind TEXT, endpoint TEXT, enabled INTEGER DEFAULT 1,
    last_polled TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY, url TEXT UNIQUE, source TEXT, title TEXT, body TEXT, lang TEXT,
    published TEXT, author TEXT, summary TEXT, fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS articles_source ON articles(source);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY, article_id TEXT REFERENCES articles(id) ON DELETE CASCADE,
    idx INTEGER, text TEXT, lang TEXT, embedder TEXT, vector BLOB
);
CREATE INDEX IF NOT EXISTS chunks_article ON chunks(article_id);
CREATE TABLE IF NOT EXISTS review_cache (key TEXT PRIMARY KEY, data TEXT, created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS http_cache (url TEXT PRIMARY KEY, etag TEXT, last_modified TEXT, body BLOB, content_type TEXT);
CREATE TABLE IF NOT EXISTS seen (url TEXT PRIMARY KEY, article_id TEXT, seen_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created_at TEXT DEFAULT (datetime('now')), url TEXT, data TEXT);
"""


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() if d else None


class Store:
    def __init__(self, path: str | Path = "spashta.db"):
        self.path = str(path)
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._db.execute("PRAGMA journal_mode = WAL")
            self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ---------------------------------------------------------- sources

    def add_source(self, s: Source) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sources(url, name, lang, kind, endpoint, enabled) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET name=excluded.name, lang=COALESCE(excluded.lang, sources.lang), "
                "kind=excluded.kind, endpoint=excluded.endpoint, enabled=excluded.enabled",
                (s.url, s.name, s.lang, s.kind, s.endpoint, int(s.enabled)))

    def sources(self, enabled_only: bool = False) -> list[Source]:
        q = "SELECT * FROM sources" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY url"  # noqa: S608 - constant strings
        return [Source(url=r["url"], name=r["name"] or "", lang=r["lang"], kind=r["kind"] or "unresolved",
                       endpoint=r["endpoint"], enabled=bool(r["enabled"])) for r in self._db.execute(q)]

    def mark_polled(self, url: str, error: Optional[str] = None) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE sources SET last_polled = datetime('now'), last_error = ? WHERE url = ?", (error, url))

    def source_status(self) -> list[dict]:
        counts = {r[0]: r[1] for r in self._db.execute("SELECT source, COUNT(*) FROM articles GROUP BY source")}
        rows = [dict(r) for r in self._db.execute("SELECT url, name, kind, last_polled, last_error FROM sources ORDER BY url")]
        for r in rows:
            r["articles"] = counts.get(host_of(r["url"]), 0)
        return rows

    def get_review(self, key: str) -> Optional[dict]:
        r = self._db.execute("SELECT data FROM review_cache WHERE key = ?", (key,)).fetchone()
        return json.loads(r[0]) if r else None

    def put_review(self, key: str, data: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO review_cache(key, data) VALUES (?, ?)", (key, json.dumps(data, ensure_ascii=False)))

    def put_report(self, report_id: str, url: str, data: dict) -> None:
        """Shared reports are immutable: a re-analysis gets a new id."""
        with self._lock, self._db:
            self._db.execute("INSERT INTO reports(id, url, data) VALUES (?, ?, ?)",
                             (report_id, url, json.dumps(data, ensure_ascii=False, default=str)))

    def get_report(self, report_id: str) -> Optional[dict]:
        r = self._db.execute("SELECT data, created_at FROM reports WHERE id = ?", (report_id,)).fetchone()
        return {**json.loads(r[0]), "created_at": r[1]} if r else None

    def get_cached(self, url: str):
        r = self._db.execute("SELECT etag, last_modified, body, content_type FROM http_cache WHERE url = ?", (url,)).fetchone()
        return (r[0], r[1], r[2], r[3] or "") if r else None

    def put_cached(self, url: str, etag, last_modified, body: bytes, content_type: str) -> None:
        if not etag and not last_modified:
            return
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO http_cache VALUES (?,?,?,?,?)", (url, etag, last_modified, body, content_type))

    # --------------------------------------------------------- articles

    def has_article(self, article_id: str) -> bool:
        return self._db.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone() is not None

    def seen(self, url: str) -> bool:
        """A listed URL already fetched, whether it became an article (maybe
        under its canonical URL) or was skipped as too short."""
        return self._db.execute("SELECT 1 FROM seen WHERE url = ?", (url,)).fetchone() is not None

    def mark_seen(self, url: str, article_id: Optional[str]) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO seen(url, article_id) VALUES (?, ?)", (url, article_id))

    def upsert_article(self, a: Article, chunks: Iterable[Chunk], embedder: str, vectors: np.ndarray) -> None:
        chunks = list(chunks)
        with self._lock, self._db:
            self._db.execute("DELETE FROM chunks WHERE article_id = ?", (a.id,))
            self._db.execute(
                "INSERT OR REPLACE INTO articles VALUES (?,?,?,?,?,?,?,?,?,?)",
                (a.id, a.url, a.source, a.title, a.body, a.lang, _iso(a.published), a.author, a.summary, _iso(a.fetched_at)))
            self._db.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?)",
                [(c.id, c.article_id, c.idx, c.text, c.lang, embedder, np.asarray(v, dtype=np.float32).tobytes())
                 for c, v in zip(chunks, vectors)])

    def _article(self, r: sqlite3.Row) -> Article:
        return Article(url=r["url"], title=r["title"], body=r["body"], source=r["source"] or "", lang=r["lang"] or "unknown",
                       published=r["published"], author=r["author"], summary=r["summary"], fetched_at=r["fetched_at"])

    def article(self, article_id: str) -> Optional[Article]:
        r = self._db.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        return self._article(r) if r else None

    def articles_from(self, source: str, limit: int = 20) -> list[Article]:
        return [self._article(r) for r in self._db.execute(
            "SELECT * FROM articles WHERE source = ? ORDER BY fetched_at DESC LIMIT ?", (source, limit))]

    def articles(self, limit: int = 100) -> list[Article]:
        return [self._article(r) for r in self._db.execute(
            "SELECT * FROM articles ORDER BY COALESCE(published, fetched_at) DESC LIMIT ?", (limit,))]

    def iter_articles(self, batch: int = 500):
        """Every stored article, oldest first, without loading the corpus at once."""
        last = ""
        while True:
            rows = self._db.execute("SELECT * FROM articles WHERE id > ? ORDER BY id LIMIT ?", (last, batch)).fetchall()
            if not rows:
                return
            for r in rows:
                yield self._article(r)
            last = rows[-1]["id"]

    def count(self) -> dict[str, int]:
        one = lambda q: self._db.execute(q).fetchone()[0]  # noqa: E731
        return {"sources": one("SELECT COUNT(*) FROM sources"), "articles": one("SELECT COUNT(*) FROM articles"),
                "chunks": one("SELECT COUNT(*) FROM chunks")}

    # ----------------------------------------------------------- chunks

    def chunks(self) -> tuple[list[Chunk], list[str], list[Optional[bytes]]]:
        """All chunks with the embedder name and raw vector of each."""
        rows = self._db.execute("SELECT * FROM chunks ORDER BY article_id, idx").fetchall()
        chunks = [Chunk(id=r["id"], article_id=r["article_id"], idx=r["idx"], text=r["text"], lang=r["lang"]) for r in rows]
        return chunks, [r["embedder"] for r in rows], [r["vector"] for r in rows]

    def set_vectors(self, embedder: str, items: list[tuple[str, np.ndarray]]) -> None:
        with self._lock, self._db:
            self._db.executemany("UPDATE chunks SET embedder = ?, vector = ? WHERE id = ?",
                                 [(embedder, np.asarray(v, dtype=np.float32).tobytes(), cid) for cid, v in items])

    def article_titles(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self._db.execute("SELECT id, title FROM articles")}

    def article_sources(self) -> dict[str, str]:
        return {r[0]: r[1] for r in self._db.execute("SELECT id, source FROM articles")}

