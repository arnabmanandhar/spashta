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

from .. import story
from ..facts import compare_facts, extract_facts
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

    # One fact list per independent source (its copies add nothing new).
    checks = compare_facts(extract_facts(article.text),
                           {story.outlet_group(g[0].source): extract_facts(g[0].text) for g in groups})
    confirmed = [c for c in checks if c.status == "confirmed"]
    conflicts = [c for c in checks if c.status in ("conflict", "disputed")]
    unreported = [c for c in checks if c.status == "unreported"]
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
    for c in conflicts:
        theirs = sorted({v for vs in c.dissent.values() for v in vs})
        shown = ", ".join(str(int(v)) if v == int(v) else str(v) for v in theirs)
        evidence.append(f"{c.status}: {c.fact.kind} {c.fact.shown} here; other sources report {shown}"
                        f" ({', '.join(sorted(c.dissent))})")
    if confirmed:
        evidence.append("figures confirmed elsewhere: " + ", ".join(f"{c.fact.kind} {c.fact.shown}" for c in confirmed))
    if unreported and groups:
        evidence.append("figures no other source reports: " + ", ".join(f"{c.fact.kind} {c.fact.shown}" for c in unreported))
    if others is not None and own - others >= 0.25:
        evidence.append(f"more sensational than other coverage ({own:.2f} vs median {others:.2f})")

    if not groups:
        concern, summary = 0.5, "No independent coverage found in the index yet; the story is unverified, not disproven."
    else:
        breadth = min(1.0, len(groups) / 3)
        conflict_share = len(conflicts) / len(checks) if checks else 0.0
        tone = max(0.0, own - others) if others is not None else 0.0
        concern = round(min(1.0, (1 - breadth) * 0.4 + conflict_share * 0.6 + (0.3 if conflicts else 0) + tone * 0.3), 3)
        n = len(groups)
        summary = f"Reported by {n} independent source{'s' if n != 1 else ''}"
        if conflicts:
            summary += f"; {len(conflicts)} figure{'s' if len(conflicts) != 1 else ''} conflict{'s' if len(conflicts) == 1 else ''} with other coverage."
        elif confirmed:
            summary += f"; {len(confirmed)} figure{'s' if len(confirmed) != 1 else ''} confirmed."
        else:
            summary += "."
    return Corroboration(
        Signal("corroboration", concern, summary, evidence,
               {"independent_sources": outlets,
                "facts": [{"kind": c.fact.kind, "value": c.fact.value, "hedged": c.fact.hedged, "status": c.status,
                           "support": c.support, "dissent": c.dissent} for c in checks],
                "own_sensationalism": own, "median_other_sensationalism": others}),
        related)
