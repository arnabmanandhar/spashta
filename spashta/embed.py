"""
Embedders: text to unit-length vectors.

  hashing   dependency-free and deterministic: stemmed terms and character
            n-grams hashed into a fixed-size vector. Matches morphology and
            spelling variants within a language; does not match across
            languages. The default for tests, CI and offline use.
  e5-small  intfloat/multilingual-e5-small (sentence-transformers). Nepali
            and English in one space, so a Nepali headline finds English
            coverage of the same event. CPU friendly. Recommended default
            once `pip install spashta[ml]` is done.
  e5-base   intfloat/multilingual-e5-base: better, slower.
  bge-m3    BAAI/bge-m3: best multilingual quality here, largest.

E5 models are trained with "query: " / "passage: " prefixes; embed() adds
them so callers don't have to remember.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

import numpy as np

from . import lang as L


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray: ...


def _unit(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class HashingEmbedder:
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.name = f"hashing-{dim}"

    def _features(self, text: str) -> list[tuple[str, float]]:
        feats: list[tuple[str, float]] = []
        for term in L.content_terms(text):
            feats.append(("w:" + term, 1.0))
            padded = f"<{term}>"
            for n in (3, 4):
                feats.extend(("c:" + padded[i:i + n], 0.3) for i in range(max(1, len(padded) - n + 1)))
        return feats

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feat, w in self._features(text):
                h = int.from_bytes(hashlib.blake2b(feat.encode(), digest_size=8).digest(), "little")
                m[row, h % self.dim] += w if (h >> 63) & 1 else -w
        return _unit(m)


class SentenceTransformerEmbedder:
    def __init__(self, model_id: str, name: str, prefixes: bool):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise RuntimeError(f"{name} needs sentence-transformers: pip install 'spashta[ml]'") from e
        self.model = SentenceTransformer(model_id)
        self.name, self.prefixes = name, prefixes

    def embed(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        if self.prefixes:
            texts = [f"{'query' if kind == 'query' else 'passage'}: {t}" for t in texts]
        texts = [L.normalize(t) for t in texts]
        return _unit(np.asarray(self.model.encode(texts, batch_size=32, show_progress_bar=False), dtype=np.float32))


MODELS = {
    "e5-small": ("intfloat/multilingual-e5-small", True),
    "e5-base": ("intfloat/multilingual-e5-base", True),
    "e5-large": ("intfloat/multilingual-e5-large", True),
    "bge-m3": ("BAAI/bge-m3", False),
}


def get_embedder(name: str = "hashing") -> Embedder:
    if name == "hashing" or name.startswith("hashing-"):
        return HashingEmbedder(int(name.split("-")[1]) if "-" in name else 1024)
    if name in MODELS:
        model_id, prefixes = MODELS[name]
        return SentenceTransformerEmbedder(model_id, name, prefixes)
    raise ValueError(f"unknown embedder {name!r}; choose from hashing, {', '.join(MODELS)}")


class CrossEncoderReranker:
    """Second-stage reranker: scores (query, passage) pairs jointly, which
    is more precise than comparing separate embeddings. BAAI/bge-reranker-v2-m3
    is multilingual (Nepali included) and pairs naturally with bge-m3. Slow
    on CPU, so it only reorders the top of the fused list."""

    def __init__(self, model_id: str = "BAAI/bge-reranker-v2-m3"):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as e:
            raise RuntimeError("the reranker needs sentence-transformers: pip install 'spashta[ml]'") from e
        self.model = CrossEncoder(model_id)
        self.name = model_id

    def score(self, query: str, texts: list[str]) -> list[float]:
        q = L.normalize(query)
        return [float(x) for x in self.model.predict([(q, L.normalize(t)) for t in texts], show_progress_bar=False)]
