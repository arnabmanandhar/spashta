"""
Spashta (स्पष्ट): news integrity reports for Nepali and English.

Ingest news from feeds, sitemaps or any website, index it with hybrid
bilingual retrieval, and report on how a story is told and sourced:
headline consistency, sensationalism, sourcing, framing and cross-outlet
corroboration, each with its evidence.
"""

from .analysis.signals import Signal
from .embed import get_embedder
from .fetch import Fetcher, FetchError
from .models import Article, Chunk, Source
from .report import IntegrityReport, analyze
from .retrieve import Index
from .store import Store

__all__ = ["Article", "Chunk", "Fetcher", "FetchError", "Index", "IntegrityReport", "Signal", "Source", "Store",
           "analyze", "get_embedder"]

__version__ = "0.3.0"
