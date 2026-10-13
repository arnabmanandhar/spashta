"""
Frozen corpus snapshots.

A snapshot is a directory:

  manifest.json   what was collected, from which outlets, over which dates,
                  and the snapshot id
  articles.jsonl  one article per line with its full text and hashes.
                  Private: it stays on your machine and is never published
                  (release.py builds the part that can be shared).

Every article carries SHA-256 hashes of its title and body, and the
snapshot id is a SHA-256 over the sorted (article id, content hash)
pairs. Two people quoting the same snapshot id measured the same corpus,
and `spashta dataset verify` proves a copy hasn't changed.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import Article, utcnow

FORMAT = "spashta-snapshot"
VERSION = 1
ARTICLES = "articles.jsonl"
MANIFEST = "manifest.json"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(title: str, body: str) -> str:
    return sha256(f"{title}\n\n{body}")


def snapshot_id(pairs: list[tuple[str, str]]) -> str:
    """Order-independent id of a corpus from (article id, content hash) pairs."""
    return sha256("\n".join(f"{a}:{h}" for a, h in sorted(pairs)))[:16]


def _when(a: Article) -> datetime:
    d = a.published or a.fetched_at
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def record(a: Article) -> dict:
    from ..story import outlet_group
    return {"id": a.id, "url": a.url, "source": a.source, "outlet_group": outlet_group(a.source) if a.source else "",
            "lang": a.lang, "published": a.published.isoformat() if a.published else None,
            "fetched_at": a.fetched_at.isoformat(), "author": a.author, "title": a.title, "body": a.body,
            "title_sha256": sha256(a.title), "body_sha256": sha256(a.body),
            "sha256": content_hash(a.title, a.body), "chars": len(a.body)}


def to_article(r: dict) -> Article:
    return Article(url=r["url"], title=r["title"], body=r["body"], source=r.get("source", ""), lang=r.get("lang", "unknown"),
                   published=r.get("published"), author=r.get("author"), fetched_at=r.get("fetched_at") or utcnow())


def _extractor_versions() -> dict:
    from importlib import metadata
    out = {}
    for pkg in ("spashta", "trafilatura"):
        try:
            out[pkg] = metadata.version(pkg)
        except metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def freeze(articles, out_dir: str | Path, since: Optional[datetime] = None, until: Optional[datetime] = None,
           min_chars: int = 200, sources: Optional[dict[str, dict]] = None, note: str = "") -> dict:
    """Write the articles published in [since, until] (fetch time when the
    publication date is unknown) to a snapshot directory. articles is any
    iterable of Article, usually Store.iter_articles(). Returns the manifest."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    for a in articles:
        when = _when(a)
        if (since and when < since) or (until and when > until) or len(a.body) < min_chars:
            continue
        rows.append(record(a))
    rows.sort(key=lambda r: (r["published"] or r["fetched_at"], r["id"]))
    with open(out / ARTICLES, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    dates = [r["published"] or r["fetched_at"] for r in rows]
    by_outlet = Counter(r["source"] for r in rows)
    manifest = {
        "format": FORMAT, "version": VERSION,
        "snapshot_id": snapshot_id([(r["id"], r["sha256"]) for r in rows]),
        "created_at": utcnow().isoformat(timespec="seconds"),
        "window": {"since": since.isoformat() if since else None, "until": until.isoformat() if until else None,
                   "first_article": min(dates) if dates else None, "last_article": max(dates) if dates else None},
        "articles": len(rows),
        "by_lang": dict(sorted(Counter(r["lang"] for r in rows).items())),
        "by_outlet": dict(sorted(by_outlet.items())),
        "by_outlet_group": dict(sorted(Counter(r["outlet_group"] for r in rows).items())),
        "outlets": {host: (sources or {}).get(host, {}) for host in sorted(by_outlet)},
        "min_chars": min_chars, "software": _extractor_versions(), "note": note,
        "files": {ARTICLES: sha256((out / ARTICLES).read_text(encoding="utf-8"))},
    }
    (out / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def read_manifest(snapshot_dir: str | Path) -> dict:
    return json.loads((Path(snapshot_dir) / MANIFEST).read_text(encoding="utf-8"))


def read_records(snapshot_dir: str | Path) -> list[dict]:
    with open(Path(snapshot_dir) / ARTICLES, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load(snapshot_dir: str | Path) -> list[Article]:
    return [to_article(r) for r in read_records(snapshot_dir)]


def verify(snapshot_dir: str | Path) -> list[str]:
    """Problems found, empty when every hash and the snapshot id check out."""
    manifest = read_manifest(snapshot_dir)
    problems = []
    rows = read_records(snapshot_dir)
    for r in rows:
        if sha256(r["title"]) != r["title_sha256"] or sha256(r["body"]) != r["body_sha256"]:
            problems.append(f"{r['id']}: text does not match its hash ({r['url']})")
        elif content_hash(r["title"], r["body"]) != r["sha256"]:
            problems.append(f"{r['id']}: content hash mismatch")
    if len(rows) != manifest["articles"]:
        problems.append(f"manifest says {manifest['articles']} articles, file has {len(rows)}")
    sid = snapshot_id([(r["id"], r["sha256"]) for r in rows])
    if sid != manifest["snapshot_id"]:
        problems.append(f"snapshot id is {sid}, manifest says {manifest['snapshot_id']}")
    return problems
