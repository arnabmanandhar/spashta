"""
Same-story matching and source independence.

Retrieval finds articles that *look* related; corroboration needs articles
that report *the same event*, from sources that are *independent* of each
other. Three checks turn the first into the second:

  story_similarity  overlap of the two articles' headline+lead in the
                    cross-language term space (phonetic keys, glossary
                    concepts) plus shared figures. Language independent.
  same_window       published within a few days of each other, so last
                    year's landslide in the same district doesn't count.
  near_duplicate    word-shingle Jaccard of the bodies: a syndicated or
                    copied text is one source however many sites carry it.

Outlets with common ownership (data/outlet_groups.json) also count once.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from functools import lru_cache
from importlib import resources
from typing import Optional

from . import lang as L
from . import xling
from .models import Article, host_of

WINDOW = timedelta(days=3)
DUPLICATE_THRESHOLD = 0.5
SHINGLE = 5


def signature(a: Article) -> tuple[set[str], set[str], set[str]]:
    """(what happened, named things, phonetic keys) for an article."""
    terms = xling.xling_terms(a.text)
    concepts = {t[2:] for t in terms if t.startswith("c:")}
    keys = {t[2:] for t in terms if t.startswith("p:")}
    return concepts & xling.event_concepts(), concepts & xling.named_concepts(), keys


def same_story(a: Article, b: Article, names: set[str]) -> tuple[bool, str]:
    """Do a and b report the same event? Needs the same kind of event
    (landslide, price increase, exam result...) AND a shared specific
    subject (a proper name learned from English text, or a named concept
    such as petrol or UAE), within the time window. Returns the reason, so
    reports can show why two articles were matched."""
    if same_window(a, b) is False:
        return False, "published too far apart"
    ea, na, ka = signature(a)
    eb, nb, kb = signature(b)
    events = ea & eb
    if not events:
        return False, "different kind of event"
    shared_names = (ka & kb & names) | (na & nb)
    if not shared_names:
        return False, "no shared subject"
    return True, f"{', '.join(sorted(events))} · {', '.join(sorted(shared_names))}"


def same_window(a: Article, b: Article, window: timedelta = WINDOW) -> Optional[bool]:
    """None when either date is unknown: the caller decides."""
    if a.published is None or b.published is None:
        return None
    return abs(a.published - b.published) <= window


def _shingles(text: str) -> set[tuple[str, ...]]:
    toks = L.tokens(text)
    return {tuple(toks[i:i + SHINGLE]) for i in range(max(0, len(toks) - SHINGLE + 1))}


def near_duplicate(a: Article, b: Article) -> float:
    """Jaccard of 5-word shingles: ~1.0 for a copied text, ~0 otherwise.
    Same-language only by construction, which is what syndication is."""
    sa, sb = _shingles(a.body), _shingles(b.body)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


_OPINION_MARKERS = ("in my view", "in my opinion", "i think", "i believe", "my own opinion", "i argue", "i would argue",
                    "मेरो विचारमा", "मलाई लाग्छ", "मेरो बुझाइमा", "मेरो धारणा")
_OPINION_PATH = re.compile(r"/(opinion|opinions|blog|blogs|editorial|column|columns|bichar|bichaar|manthan)(/|$)", re.I)


def genre(a: Article) -> str:
    """'opinion' for commentary (first-person argument, or an opinion/blog
    section in the URL), else 'news'. Commentary on an event is listed in a
    report but never counted as corroboration of its facts."""
    text = L.normalize(a.text).lower()
    if _OPINION_PATH.search(a.url) or any(m in text for m in _OPINION_MARKERS):
        return "opinion"
    return "news"


@lru_cache(maxsize=1)
def _groups() -> dict[str, str]:
    data = json.loads((resources.files("spashta") / "data" / "outlet_groups.json").read_text(encoding="utf-8"))
    return {host: group for group, hosts in data["groups"].items() for host in hosts}


def outlet_group(source_or_url: str) -> str:
    host = host_of(source_or_url) if "://" in source_or_url else source_or_url.lower().removeprefix("www.")
    groups = _groups()
    while host:
        if host in groups:
            return groups[host]
        host = host.partition(".")[2] if host.count(".") > 1 else ""
    return host_of(source_or_url) if "://" in source_or_url else source_or_url


def independent_groups(article: Article, others: list[Article]) -> list[list[Article]]:
    """Cluster others into independent sources: copies of each other (or of
    the article) and outlets of the same owner collapse into one group.
    Groups that copy the article itself or share its owner are dropped."""
    own = outlet_group(article.source)
    groups: list[list[Article]] = []
    for o in others:
        if outlet_group(o.source) == own or near_duplicate(article, o) >= DUPLICATE_THRESHOLD:
            continue
        for g in groups:
            if any(outlet_group(x.source) == outlet_group(o.source) or near_duplicate(x, o) >= DUPLICATE_THRESHOLD for x in g):
                g.append(o)
                break
        else:
            groups.append([o])
    return groups
