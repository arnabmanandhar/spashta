"""
Site-level boilerplate removal.

Extractors miss lines a site repeats in every article: "Follow us on
Facebook", "प्रकाशित मिति", subscription prompts, reporter sign-offs. They
pollute retrieval (every article from the site looks alike) and the
signals ("share" prompts read as clickbait). A paragraph that appears in
at least MIN_ARTICLES of a source's articles, and in at least SHARE of
them, is boilerplate for that source and is removed.
"""

from __future__ import annotations

import re
from collections import Counter

from .. import lang as L
from ..models import Article

MIN_ARTICLES = 3
SHARE = 0.3


def _paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n+", body) if p.strip()]


def _key(p: str) -> str:
    return " ".join(L.tokens(p))


def learn(bodies: list[str]) -> set[str]:
    if len(bodies) < MIN_ARTICLES:
        return set()
    counts = Counter(k for b in bodies for k in {_key(p) for p in _paragraphs(b)})
    need = max(MIN_ARTICLES, SHARE * len(bodies))
    return {k for k, n in counts.items() if n >= need and k}


def strip(article: Article, boilerplate: set[str]) -> Article:
    if not boilerplate:
        return article
    kept = [p for p in _paragraphs(article.body) if _key(p) not in boilerplate]
    return article.model_copy(update={"body": "\n\n".join(kept)})
