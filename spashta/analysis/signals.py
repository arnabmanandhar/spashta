"""
Deterministic credibility signals.

Each signal returns a concern between 0 (no warning signs) and 1 (strong
warning signs) plus the exact evidence that produced it, so a reader can
check every number in a report. No model call, no randomness: the same
article always gets the same signals.

  sensationalism   emotive and clickbait wording, exclamation marks,
                   shouting capitals; headline matches count double
  sourcing         how often claims are attributed, and how many of those
                   attributions are anonymous
  framing          loaded and opinion words in what presents as news
  headline         whether the body supports the headline: shared terms,
                   numbers in the headline that the body never mentions,
                   and a headline more sensational than its own story
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .. import lang as L
from ..models import Article
from . import lexicons as X


@dataclass
class Signal:
    name: str
    concern: float  # 0 = no warning signs, 1 = strong warning signs
    summary: str
    evidence: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _saturate(x: float, scale: float) -> float:
    """0 at x=0, ~0.63 at x=scale, approaching 1."""
    return round(1 - math.exp(-max(x, 0.0) / scale), 3)


def find_terms(text: str, table: dict[str, str], lang: str) -> list[str]:
    """Every lexicon match in text, in order, repeats included."""
    words, phrases = X.lexicon(table, lang)
    norm = L.normalize(text).lower()
    hits: list[str] = []
    for p in phrases:
        hits.extend([p] * norm.count(p))
    for tok in L.tokens(norm):
        for w in words:
            if tok == w or (L.is_devanagari(w) and tok.startswith(w)):
                hits.append(tok)
                break
    return hits


def _word_count(text: str) -> int:
    return max(1, len(L.tokens(text)))


def sensationalism(a: Article) -> Signal:
    head = find_terms(a.title, X.SENSATIONAL, a.lang)
    body = find_terms(a.body, X.SENSATIONAL, a.lang)
    title = L.normalize(a.title)
    exclaims = title.count("!") + min(a.body.count("!"), 10) * 0.25
    caps = [w for w in re.findall(r"\b[A-Z]{3,}\b", title) if w not in {"USA", "NEA", "CIAA", "NRB", "UML", "RSP", "UN"}]
    per100 = len(body) * 100 / _word_count(a.body)
    raw = 2 * len(head) + per100 + exclaims + 0.5 * len(caps)
    concern = _saturate(raw, 3.0)
    evidence = [f"headline: {t}" for t in dict.fromkeys(head)] + [f"body: {t}" for t in dict.fromkeys(body)][:10]
    if exclaims:
        evidence.append(f"exclamation marks: {title.count('!')} in headline, {a.body.count('!')} in body")
    if caps:
        evidence.append("capitals in headline: " + ", ".join(caps))
    if concern < 0.25:
        summary = "Measured wording."
    elif concern < 0.6:
        summary = "Some emotive or clickbait wording."
    else:
        summary = "Heavily sensational wording."
    return Signal("sensationalism", concern, summary, evidence,
                  {"headline_terms": len(head), "body_terms": len(body), "body_terms_per_100_words": round(per100, 2)})


def sourcing(a: Article) -> Signal:
    words = _word_count(a.body)
    attributed = find_terms(a.body, X.ATTRIBUTION, a.lang)
    anonymous = find_terms(a.body, X.ANONYMOUS, a.lang)
    quotes = len(re.findall(r'"[^"]{10,}"', L.normalize(a.body)))
    per300 = (len(attributed) + quotes) * 300 / words
    anon_share = len(anonymous) / max(1, len(attributed) + len(anonymous))
    concern = 0.0
    if words >= 80:  # too short to judge sourcing
        concern = max(0.0, 1 - per300 / 3) * 0.7 + anon_share * 0.3
    concern = round(min(1.0, concern), 3)
    evidence = [f"attribution: {t}" for t in dict.fromkeys(attributed)][:8]
    evidence += [f"anonymous or unverified: {t}" for t in dict.fromkeys(anonymous)][:6]
    if quotes:
        evidence.append(f"direct quotes: {quotes}")
    if words < 80:
        summary = "Too short to judge sourcing."
    elif concern < 0.3:
        summary = "Claims are regularly attributed to named sources."
    elif concern < 0.6:
        summary = "Some claims lack clear attribution."
    else:
        summary = "Few claims are attributed, or sources are mostly anonymous."
    return Signal("sourcing", concern, summary, evidence,
                  {"attributions": len(attributed), "anonymous": len(anonymous), "quotes": quotes,
                   "attributions_per_300_words": round(per300, 2)})


def framing(a: Article) -> Signal:
    loaded = find_terms(a.text, X.LOADED, a.lang)
    per100 = len(loaded) * 100 / _word_count(a.text)
    concern = _saturate(per100, 1.0)
    summary = ("Neutral wording." if concern < 0.25 else
               "Some loaded or opinionated wording." if concern < 0.6 else
               "Strongly loaded or opinionated wording for a news report.")
    return Signal("framing", concern, summary, [f"loaded: {t}" for t in dict.fromkeys(loaded)][:10],
                  {"loaded_terms": len(loaded), "per_100_words": round(per100, 2)})


def headline_consistency(a: Article) -> Signal:
    head_terms = set(L.content_terms(a.title))
    body_terms = set(L.content_terms(a.body))
    lead_terms = set(L.content_terms(" ".join(L.sentences(a.body)[:3])))
    coverage = len(head_terms & body_terms) / len(head_terms) if head_terms else 1.0
    lead_cov = len(head_terms & lead_terms) / len(head_terms) if head_terms else 1.0
    missing_numbers = sorted(L.numbers(a.title) - L.numbers(a.body))
    head_sens = find_terms(a.title, X.SENSATIONAL, a.lang)
    body_sens = find_terms(a.body, X.SENSATIONAL, a.lang)
    escalation = bool(head_sens) and not body_sens
    concern = (1 - coverage) * 0.45 + (1 - lead_cov) * 0.15 + min(1, len(missing_numbers)) * 0.3 + (0.2 if escalation else 0)
    concern = round(min(1.0, concern), 3)
    evidence = []
    absent = sorted(head_terms - body_terms)
    if absent:
        evidence.append("headline words not in the story: " + ", ".join(absent[:8]))
    if missing_numbers:
        evidence.append("headline numbers not in the story: " + ", ".join(missing_numbers))
    if escalation:
        evidence.append("headline uses sensational words the story itself doesn't: " + ", ".join(dict.fromkeys(head_sens)))
    summary = ("The story supports its headline." if concern < 0.25 else
               "The headline goes partly beyond the story." if concern < 0.55 else
               "The headline is not supported by the story.")
    return Signal("headline", concern, summary, evidence,
                  {"headline_term_coverage": round(coverage, 3), "lead_coverage": round(lead_cov, 3),
                   "missing_numbers": missing_numbers, "escalation": escalation})


def all_signals(a: Article) -> list[Signal]:
    return [headline_consistency(a), sensationalism(a), sourcing(a), framing(a)]
