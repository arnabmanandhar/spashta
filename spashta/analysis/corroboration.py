"""
Cross-source corroboration through retrieval.

For the article being checked, the index finds other outlets' coverage of
the same story (hybrid search on headline + lead, own outlet excluded).
From that coverage:

  - how many independent outlets report the story at all
  - which numbers in the headline and lead appear in at least one other
    outlet's coverage, and which appear nowhere else
  - how sensational this article is compared with the others

No other coverage does not mean the story is false: it may be new,
exclusive or local. The report says "unverified", never "false".
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional

from .. import lang as L
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


@dataclass
class Corroboration:
    signal: Signal
    related: list[RelatedCoverage]


def corroborate(article: Article, index: Index, k: int = 5) -> Corroboration:
    groups = index.related_articles(article, k=k)
    related: list[RelatedCoverage] = []
    for aid, hits in groups:
        other = index.store.article(aid)
        if other is None:
            continue
        related.append(RelatedCoverage(aid, other.url, other.title, other.source, other.lang, hits,
                                       sensationalism(other).concern))
    outlets = sorted({r.source for r in related if r.source})
    key_numbers = L.numbers(article.title + " " + " ".join(L.sentences(article.body)[:3]))
    key_numbers = {n for n in key_numbers if not (len(n) == 4 and n[:2] in ("19", "20"))}  # skip bare years
    elsewhere: set[str] = set()
    for r in related:
        other = index.store.article(r.article_id)
        if other is not None:
            elsewhere |= L.numbers(other.text)
    confirmed = sorted(key_numbers & elsewhere)
    unconfirmed = sorted(key_numbers - elsewhere)
    own = sensationalism(article).concern
    others: Optional[float] = statistics.median(r.sensationalism for r in related) if related else None

    evidence = [f"also reported by: {', '.join(outlets)}"] if outlets else []
    if confirmed:
        evidence.append("figures matched in other coverage: " + ", ".join(confirmed))
    if unconfirmed and related:
        evidence.append("figures not found in other coverage: " + ", ".join(unconfirmed))
    if others is not None and own - others >= 0.25:
        evidence.append(f"more sensational than other coverage ({own:.2f} vs median {others:.2f})")

    if not related:
        concern, summary = 0.5, "No other coverage found in the index yet; the story is unverified, not disproven."
    else:
        breadth = min(1.0, len(outlets) / 3)
        figures = len(unconfirmed) / len(key_numbers) if key_numbers else 0.0
        tone = max(0.0, own - others) if others is not None else 0.0
        concern = round(min(1.0, (1 - breadth) * 0.5 + figures * 0.3 + tone * 0.4), 3)
        summary = (f"Reported by {len(outlets)} other outlet{'s' if len(outlets) != 1 else ''}"
                   + (f"; {len(unconfirmed)} figure{'s' if len(unconfirmed) != 1 else ''} not found elsewhere." if unconfirmed else "."))
    return Corroboration(
        Signal("corroboration", concern, summary, evidence,
               {"outlets": outlets, "confirmed_numbers": confirmed, "unconfirmed_numbers": unconfirmed,
                "own_sensationalism": own, "median_other_sensationalism": others}),
        related)
