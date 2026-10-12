"""
Hybrid retrieval: BM25 over language-aware terms + dense vectors, fused
with Reciprocal Rank Fusion.

BM25 is exact on names and numbers and is explainable (the matched terms
are reported with each hit). Dense vectors catch paraphrase and, with a
multilingual model, the same story in the other language. RRF combines
the two rankings without calibrating their scores:

    score(chunk) = sum over rankings of 1 / (RRF_K + rank)

The index lives in memory and is rebuilt from the store on open; that is
comfortably fast up to ~100k chunks. Past that, swap the dense half for
sqlite-vec or pgvector behind the same search() call.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from . import lang as L
from . import xling
from .chunk import chunk_article
from .embed import Embedder
from .models import Article, Chunk
from .store import Store

RRF_K = 60
MODES = ("hybrid", "lexical", "dense", "xling")
BM25_K1, BM25_B = 1.5, 0.75


@dataclass
class Hit:
    chunk: Chunk
    score: float
    lexical_rank: Optional[int] = None
    dense_rank: Optional[int] = None
    cosine: float = 0.0
    matched_terms: list[str] = field(default_factory=list)


def embedding_text(chunk: Chunk, title: str) -> str:
    """Contextual chunk text for the embedder: later chunks carry the
    headline, so a passage about "the toll" still knows which event it is
    about (a cheap form of contextual retrieval). Chunk 0 already starts
    with the headline."""
    return chunk.text if chunk.idx == 0 or not title else f"{title}\n{chunk.text}"


class BM25:
    def __init__(self):
        self.docs: list[Counter] = []
        self.df: Counter = Counter()
        self.lengths: list[int] = []

    def add(self, terms: list[str]) -> None:
        c = Counter(terms)
        self.docs.append(c)
        self.lengths.append(len(terms))
        self.df.update(c.keys())

    def scores(self, query_terms: list[str]) -> np.ndarray:
        n = len(self.docs)
        out = np.zeros(n, dtype=np.float32)
        if not n:
            return out
        avg = (sum(self.lengths) / n) or 1.0
        for t in set(query_terms):
            df = self.df.get(t)
            if not df:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for i, doc in enumerate(self.docs):
                tf = doc.get(t)
                if tf:
                    out[i] += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * self.lengths[i] / avg))
        return out


class Index:
    def __init__(self, store: Store, embedder: Embedder):
        self.store, self.embedder = store, embedder
        self.chunks: list[Chunk] = []
        self.terms: list[set[str]] = []
        self.bm25 = BM25()
        self.xbm25 = BM25()
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.article_source: dict[str, str] = {}
        self.reload()

    def reload(self) -> None:
        chunks, embedders, blobs = self.store.chunks()
        stale = [i for i, (e, b) in enumerate(zip(embedders, blobs)) if e != self.embedder.name or b is None]
        if stale:  # vectors from another embedder can't be compared with ours
            titles = self.store.article_titles()
            fresh = self.embedder.embed([embedding_text(chunks[i], titles.get(chunks[i].article_id, "")) for i in stale])
            self.store.set_vectors(self.embedder.name, [(chunks[i].id, fresh[j]) for j, i in enumerate(stale)])
            for j, i in enumerate(stale):
                blobs[i] = fresh[j].tobytes()
        self.chunks = chunks
        self.bm25 = BM25()
        self.xbm25 = BM25()
        self.terms = []
        for c in chunks:
            t = L.content_terms(c.text)
            self.bm25.add(t)
            self.xbm25.add(xling.xling_terms(c.text))
            self.terms.append(set(t))
        self.vectors = (np.vstack([np.frombuffer(b, dtype=np.float32) for b in blobs])
                        if blobs else np.zeros((0, 1), dtype=np.float32))
        self.article_source = self.store.article_sources()

    def add(self, article: Article) -> list[Chunk]:
        self.add_many([article])
        return chunk_article(article)

    def add_many(self, articles: Iterable[Article]) -> int:
        n = 0
        for a in articles:
            chunks = chunk_article(a)
            vectors = (self.embedder.embed([embedding_text(c, a.title) for c in chunks]) if chunks
                       else np.zeros((0, 1)))
            self.store.upsert_article(a, chunks, self.embedder.name, vectors)
            n += 1
        self.reload()
        return n

    def search(self, query: str, k: int = 10, exclude_articles: Iterable[str] = (),
               exclude_sources: Iterable[str] = (), depth: int = 50, mode: str = "hybrid") -> list[Hit]:
        """mode: hybrid (default), lexical or dense; the last two exist for
        ablations in `spashta eval --compare`."""
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if not self.chunks:
            return []
        skip_a, skip_s = set(exclude_articles), set(exclude_sources)
        allowed = np.array([c.article_id not in skip_a and self.article_source.get(c.article_id, "") not in skip_s
                            for c in self.chunks])
        q_terms = L.content_terms(query)
        lex = self.bm25.scores(q_terms)
        lex[~allowed] = 0
        lex_order = [i for i in np.argsort(-lex, kind="stable")[:depth] if lex[i] > 0]
        xl = self.xbm25.scores(xling.xling_terms(query))
        xl[~allowed] = 0
        xl_order = [i for i in np.argsort(-xl, kind="stable")[:depth] if xl[i] > 0]
        q_vec = self.embedder.embed([query], kind="query")[0]
        cos = self.vectors @ q_vec
        cos[~allowed] = -1
        dense_order = [i for i in np.argsort(-cos, kind="stable")[:depth] if allowed[i]]
        fused: dict[int, float] = defaultdict(float)
        lex_rank = {i: r for r, i in enumerate(lex_order, 1)}
        dense_rank = {i: r for r, i in enumerate(dense_order, 1)}
        if mode in ("hybrid", "lexical"):
            for i, r in lex_rank.items():
                fused[i] += 1 / (RRF_K + r)
        if mode in ("hybrid", "dense"):
            for i, r in dense_rank.items():
                fused[i] += 1 / (RRF_K + r)
        if mode in ("hybrid", "xling"):
            for r, i in enumerate(xl_order, 1):
                fused[i] += 1 / (RRF_K + r)
        q_set = set(q_terms)
        ranked = sorted(fused, key=lambda i: (-fused[i], i))[:k]
        return [Hit(self.chunks[i], fused[i], lex_rank.get(i), dense_rank.get(i), float(cos[i]),
                    sorted(q_set & self.terms[i])) for i in ranked]

    def related_articles(self, article: Article, k: int = 5, min_cosine: float = 0.2,
                         min_shared_terms: int = 3) -> list[tuple[str, list[Hit]]]:
        """Other outlets' articles about the same story: searched with the
        headline and lead, excluding the article itself and its own outlet,
        grouped by article, best first. A hit has to be close in vector
        space or share enough terms, so unrelated news isn't presented as
        coverage of the same event."""
        lead = " ".join(L.sentences(article.body)[:3])
        hits = self.search(f"{article.title} {lead}", k=k * 6, exclude_articles=[article.id],
                           exclude_sources=[article.source] if article.source else [])
        groups: dict[str, list[Hit]] = defaultdict(list)
        for h in hits:
            if h.cosine >= min_cosine or len(h.matched_terms) >= min_shared_terms:
                groups[h.chunk.article_id].append(h)
        ordered = sorted(groups.items(), key=lambda kv: -max(h.score for h in kv[1]))
        return ordered[:k]
