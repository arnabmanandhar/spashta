"""
Cross-source corroboration through retrieval.

For the article being checked:

  1. retrieval proposes candidates from other outlets (hybrid search on
     headline + lead, in both languages)
  2. story.same_story keeps those reporting the same event (same kind of
     event, a shared specific subject, published within days), and records
     why each was kept
  3. commentary (opinion pieces, blogs) is listed but not counted
  4. copies of the same text and outlets with one owner collapse into one
     independent source

From the independent coverage: how many sources report the story, which
of the article's key figures appear elsewhere, and how its tone compares.
No other coverage does not mean the story is false: it may be new,
exclusive or local, so the report says "unverified", never "false".
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from .. import lang as L
from .. import story
from ..models import Article
from ..retrieve import Hit, Index
from .signals import Signal, sensationalism


@dataclass
class RelatedCoverage:
    article_id: str
    url: str
    title: str
    source: str
    lang: str
    passages: list[Hit] = field(default_factory=list)
    sensationalism: float = 0.0
    reason: str = ""  # why it was matched as the same story
    role: str = "independent"  # independent | copy | same-owner | commentary


@dataclass
class Corroboration:
    signal: Signal
    related: list[RelatedCoverage]


def _key_numbers(a: Article) -> set[str]:
    nums = L.numbers(a.title + " " + " ".join(L.sentences(a.body)[:3]))
    return {n for n in nums if not (len(n) == 4 and n[:2] in ("19", "20"))}  # bare years say little


def corroborate(article: Article, index: Index, k: int = 8) -> Corroboration:
    related: list[RelatedCoverage] = []
    news: list[Article] = []
    for aid, hits in index.candidate_articles(article, k=k * 2):
        other = index.store.article(aid)
        if other is None:
            continue
        ok, reason = story.same_story(article, other, index.names)
        if not ok:
            continue
        rc = RelatedCoverage(aid, other.url, other.title, other.source, other.lang, hits, sensationalism(other).concern, reason)
        if story.genre(other) == "opinion":
            rc.role = "commentary"
        else:
            news.append(other)
        related.append(rc)
        if len(related) >= k:
            break

    groups = story.independent_groups(article, news)
    independent_ids = {g[0].id for g in groups}
    by_id = {r.article_id: r for r in related}
    own_group = story.outlet_group(article.source)
    for o in news:
        r = by_id[o.id]
        if o.id in independent_ids:
            continue
        r.role = "same-owner" if story.outlet_group(o.source) == own_group else "copy"
    outlets = sorted({story.outlet_group(g[0].source) for g in groups})

    key_numbers = _key_numbers(article)
    elsewhere: set[str] = set()
    for g in groups:
        for o in g:
            elsewhere |= L.numbers(o.text)
    confirmed = sorted(key_numbers & elsewhere)
    unconfirmed = sorted(key_numbers - elsewhere)
    own = sensationalism(article).concern
    indep = [by_id[g[0].id].sensationalism for g in groups]
    others: Optional[float] = statistics.median(indep) if indep else None

    evidence = []
    if outlets:
        evidence.append(f"independent sources: {', '.join(outlets)}")
    copies = [r for r in related if r.role in ("copy", "same-owner")]
    if copies:
        evidence.append("not counted separately (copy or same owner): " + ", ".join(sorted({r.source for r in copies})))
    commentary = [r for r in related if r.role == "commentary"]
    if commentary:
        evidence.append("commentary on the story (not counted): " + ", ".join(sorted({r.source for r in commentary})))
    if confirmed:
        evidence.append("figures matched in other coverage: " + ", ".join(confirmed))
    if unconfirmed and groups:
        evidence.append("figures not found in other coverage: " + ", ".join(unconfirmed))
    if others is not None and own - others >= 0.25:
        evidence.append(f"more sensational than other coverage ({own:.2f} vs median {others:.2f})")

    if not groups:
        concern, summary = 0.5, "No independent coverage found in the index yet; the story is unverified, not disproven."
    else:
        breadth = min(1.0, len(groups) / 3)
        figures = len(unconfirmed) / len(key_numbers) if key_numbers else 0.0
        tone = max(0.0, own - others) if others is not None else 0.0
        concern = round(min(1.0, (1 - breadth) * 0.5 + figures * 0.3 + tone * 0.4), 3)
        n = len(groups)
        summary = (f"Reported by {n} independent source{'s' if n != 1 else ''}"
                   + (f"; {len(unconfirmed)} figure{'s' if len(unconfirmed) != 1 else ''} not found elsewhere." if unconfirmed else "."))
    return Corroboration(
        Signal("corroboration", concern, summary, evidence,
               {"independent_sources": outlets, "confirmed_numbers": confirmed, "unconfirmed_numbers": unconfirmed,
                "own_sensationalism": own, "median_other_sensationalism": others}),
        related)
