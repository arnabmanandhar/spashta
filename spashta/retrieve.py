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
    """Okapi BM25 over an inverted index: scoring touches only the postings
    of the query's terms, and documents can be appended without a rebuild."""

    def __init__(self):
        self.postings: dict[str, tuple[list[int], list[int]]] = {}
        self.lengths: list[int] = []
        self._total = 0

    def add(self, terms: list[str]) -> None:
        doc = len(self.lengths)
        for t, tf in Counter(terms).items():
            docs, tfs = self.postings.setdefault(t, ([], []))
            docs.append(doc)
            tfs.append(tf)
        self.lengths.append(len(terms))
        self._total += len(terms)

    def scores(self, query_terms: list[str]) -> np.ndarray:
        n = len(self.lengths)
        out = np.zeros(n, dtype=np.float32)
        if not n:
            return out
        avg = (self._total / n) or 1.0
        lengths = np.asarray(self.lengths, dtype=np.float32)
        for t in set(query_terms):
            if t not in self.postings:
                continue
            docs, tfs = self.postings[t]
            idx = np.asarray(docs)
            tf = np.asarray(tfs, dtype=np.float32)
            idf = math.log(1 + (n - len(docs) + 0.5) / (len(docs) + 0.5))
            out[idx] += idf * tf * (BM25_K1 + 1) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * lengths[idx] / avg))
        return out


class Index:
    """In-memory search index over the store's chunks. Opening loads
    everything once; add_many appends incrementally (a re-added article's
    old chunks are tombstoned), so polling costs grow with what's new, not
    with the size of the corpus."""

    def __init__(self, store: Store, embedder: Embedder):
        self.store, self.embedder = store, embedder
        self.reload()

    def _reset(self) -> None:
        self.chunks: list[Chunk] = []
        self.alive: list[bool] = []
        self.terms: list[set[str]] = []
        self.bm25 = BM25()
        self.xbm25 = BM25()
        self._rows: list[np.ndarray] = []
        self._matrix: Optional[np.ndarray] = None
        self.rows_of: dict[str, list[int]] = {}
        self.article_source: dict[str, str] = {}
        # Proper names learned from English text (capitalisation), as phonetic
        # keys, so they also recognise the same names written in Devanagari.
        self.names: set[str] = set()

    def _append(self, chunk: Chunk, vector: np.ndarray) -> None:
        row = len(self.chunks)
        t = L.content_terms(chunk.text)
        self.chunks.append(chunk)
        self.alive.append(True)
        self.terms.append(set(t))
        self.bm25.add(t)
        self.xbm25.add(xling.xling_terms(chunk.text))
        self._rows.append(np.asarray(vector, dtype=np.float32))
        self._matrix = None
        self.rows_of.setdefault(chunk.article_id, []).append(row)
        self.names |= xling.name_keys(chunk.text)

    @property
    def vectors(self) -> np.ndarray:
        if self._matrix is None:
            self._matrix = np.vstack(self._rows) if self._rows else np.zeros((0, 1), dtype=np.float32)
        return self._matrix

    def reload(self) -> None:
        self._reset()
        chunks, embedders, blobs = self.store.chunks()
        stale = [i for i, (e, b) in enumerate(zip(embedders, blobs)) if e != self.embedder.name or b is None]
        if stale:  # vectors from another embedder can't be compared with ours
            titles = self.store.article_titles()
            fresh = self.embedder.embed([embedding_text(chunks[i], titles.get(chunks[i].article_id, "")) for i in stale])
            self.store.set_vectors(self.embedder.name, [(chunks[i].id, fresh[j]) for j, i in enumerate(stale)])
            for j, i in enumerate(stale):
                blobs[i] = fresh[j].tobytes()
        for c, b in zip(chunks, blobs):
            self._append(c, np.frombuffer(b, dtype=np.float32))
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
            for row in self.rows_of.pop(a.id, []):
                self.alive[row] = False
            for c, v in zip(chunks, vectors):
                self._append(c, v)
            self.article_source[a.id] = a.source
            n += 1
        dead = self.alive.count(False)
        if dead > 1000 and dead > len(self.alive) // 5:  # compact once tombstones pile up
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
        allowed = np.array(self.alive)
        if skip_a or skip_s:
            allowed &= np.array([c.article_id not in skip_a and self.article_source.get(c.article_id, "") not in skip_s
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

    def candidate_articles(self, article: Article, k: int = 10) -> list[tuple[str, list[Hit]]]:
        """Articles from other outlets that retrieval ranks close to this
        one (searched with headline and lead), grouped, best first. These
        are candidates only: story.same_story decides which report the same
        event."""
        lead = " ".join(L.sentences(article.body)[:3])
        hits = self.search(f"{article.title} {lead}", k=k * 6, exclude_articles=[article.id],
                           exclude_sources=[article.source] if article.source else [])
        groups: dict[str, list[Hit]] = defaultdict(list)
        for h in hits:
            groups[h.chunk.article_id].append(h)
        ordered = sorted(groups.items(), key=lambda kv: -max(h.score for h in kv[1]))
        return ordered[:k]
