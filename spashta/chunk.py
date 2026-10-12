"""
Language-aware chunking.

Chunk 0 is always the headline plus the lead (first sentences), because
that is where a story's key facts and framing sit and what corroboration
compares first. The rest of the body is packed into chunks of whole
sentences (split on . ! ? and the Devanagari danda) up to a word budget,
with one sentence of overlap so a fact spanning a boundary is still found.
"""

from __future__ import annotations

from . import lang as L
from .models import Article, Chunk

MAX_WORDS = 120
LEAD_WORDS = 60


def _words(s: str) -> int:
    return len(s.split())


def chunk_article(article: Article, max_words: int = MAX_WORDS) -> list[Chunk]:
    sents = L.sentences(article.body)
    lead: list[str] = []
    while sents and sum(map(_words, lead)) < LEAD_WORDS:
        lead.append(sents.pop(0))
    pieces = [" ".join([article.title, *lead]).strip()]
    current: list[str] = lead[-1:]  # overlap
    for s in sents:
        if current and sum(map(_words, current)) + _words(s) > max_words:
            pieces.append(" ".join(current))
            current = current[-1:]
        current.append(s)
    if len(current) > 1:  # more than the overlap sentence
        pieces.append(" ".join(current))
    aid = article.id
    return [Chunk(id=f"{aid}:{i}", article_id=aid, idx=i, text=t, lang=L.detect_language(t))
            for i, t in enumerate(pieces) if t.strip()]
