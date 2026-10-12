"""Bundled synthetic sample data for the demo, tests and retrieval evaluation."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from . import lang as L
from .models import Article, Source, host_of

DATA = resources.files("spashta") / "data"


def sample_articles() -> list[Article]:
    data = json.loads((DATA / "sample_articles.json").read_text(encoding="utf-8"))
    out = []
    for a in data["articles"]:
        text = f"{a['title']}\n{a['body']}"
        out.append(Article(url=a["url"], title=L.normalize(a["title"]), body=L.normalize(a["body"]), source=host_of(a["url"]),
                           lang=L.detect_language(text), published=a.get("published"), author=a.get("author") or None))
    return out


def sample_questions_path() -> Path:
    return Path(str(DATA / "sample_questions.json"))


def seed_sources() -> list[Source]:
    data = json.loads((DATA / "sources.json").read_text(encoding="utf-8"))
    return [Source(url=s["url"], name=s["name"], lang=s.get("lang")) for s in data["sources"]]
