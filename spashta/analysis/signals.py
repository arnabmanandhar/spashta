"""
Deterministic credibility signals.

Each signal returns a concern between 0 (no warning signs) and 1 (strong
warning signs), a confidence between 0 and 1 (how much text it had to go
on), and the exact evidence that produced it, so a reader can check every
number in a report. No model call, no randomness: the same article always
gets the same signals.

  sensationalism   emotive wording, clickbait structures (curiosity gaps,
                   question headlines, listicles, calls to share), shouting
                   capitals, exclamation marks; headline matches count double
  sourcing         how often claims are attributed, and how many of those
                   attributions are anonymous
  framing          loaded words, and first-person opinion inside news
  headline         whether the body supports the headline: shared terms,
                   headline figures the body doesn't contain, uncertainty
                   the headline drops, sensational words only the headline uses

Confidence matters: a 60-word item can't show good sourcing, but that is
not evidence of good sourcing either. Low-confidence signals count less in
the overall score instead of counting as "no concern".
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

from .. import lang as L
from ..facts import extract_facts
from ..models import Article
from . import lexicons as X


@dataclass
class Signal:
    name: str
    concern: float  # 0 = no warning signs, 1 = strong warning signs
    summary: str
    evidence: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)
    confidence: float = 1.0  # how much the overall score should trust this signal


@lru_cache(maxsize=1)
def thresholds() -> dict[str, dict[str, float]]:
    """Concern levels where each signal's summary changes (data/thresholds.json)."""
    data = json.loads((resources.files("spashta") / "data" / "thresholds.json").read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _level(name: str, concern: float) -> int:
    t = thresholds()[name]
    return 0 if concern < t["some"] else 1 if concern < t["strong"] else 2


def _saturate(x: float, scale: float) -> float:
    """0 at x=0, ~0.63 at x=scale, approaching 1."""
    return round(1 - math.exp(-max(x, 0.0) / scale), 3)


def _length_confidence(words: int, full: int = 150) -> float:
    return round(min(1.0, 0.25 + 0.75 * words / full), 2)


def find_terms(text: str, table: dict[str, str], lang: str) -> list[str]:
    """Every lexicon match in text, in order, repeats included. A word inside
    a matched phrase is not matched again on its own."""
    words, phrases = X.lexicon(table, lang)
    norm = L.normalize(text).lower()
    hits: list[str] = []
    for p in sorted(phrases, key=len, reverse=True):
        n = norm.count(p)
        if n:
            hits.extend([p] * n)
            norm = norm.replace(p, " ")
    for tok in L.tokens(norm):
        for w in words:
            if tok == w or (L.is_devanagari(w) and tok.startswith(w)):
                hits.append(tok)
                break
    return hits


def _word_count(text: str) -> int:
    return max(1, len(L.tokens(text)))


# Clickbait structures: the headline withholds or teases instead of informing.
_CLICKBAIT = [
    ("curiosity gap", re.compile(r"\b(this is why|here's why|here is why|what happened next|you won't believe|the reason why|"
                                 r"this is what|here's what|find out)\b|यस्तो छ|यी हुन्|यसकारण|थाहा पाउनुहोस्|के भयो|यस्तो भयो", re.I)),
    ("listicle", re.compile(r"^\s*\d+\s+(things|reasons|ways|facts|signs|photos)\b|^\s*\d+\s+(कुरा|कारण|तरिका|तस्बिर)", re.I)),
    ("call to share", re.compile(r"\b(share this|share before|must watch|must see|watch till the end)\b|सेयर गर्नुहोस्|सबैलाई सेयर|"
                                 r"हेर्नुहोस्|पूरा भिडियो", re.I)),
]
_ACRONYMS = {"NEPSE", "NRB", "CIAA", "NOC", "NEA", "UML", "RSP", "LPG", "SEE", "UAE", "USA", "COVID", "FIFA", "ICC", "UNICEF"}


def sensationalism(a: Article) -> Signal:
    head = find_terms(a.title, X.SENSATIONAL, a.lang)
    body = find_terms(a.body, X.SENSATIONAL, a.lang)
    title = L.normalize(a.title)
    exclaims = title.count("!") + min(a.body.count("!"), 10) * 0.25
    # Shouting: long all-caps words that aren't known acronyms.
    caps = [w for w in re.findall(r"\b[A-Z]{5,}\b", title) if w not in _ACRONYMS]
    structures = [name for name, rx in _CLICKBAIT if rx.search(title)]
    structures += [f"{name} (body)" for name, rx in _CLICKBAIT if rx.search(a.body) and name == "call to share"]
    question = title.rstrip().endswith("?")
    words = _word_count(a.body)
    per100 = len(body) * 100 / words
    raw = 2 * len(head) + per100 + exclaims + 0.75 * len(caps) + 1.5 * len(structures) + (0.75 if question else 0)
    concern = _saturate(raw, 3.0)
    evidence = [f"headline: {t}" for t in dict.fromkeys(head)] + [f"body: {t}" for t in dict.fromkeys(body)][:10]
    evidence += [f"clickbait structure: {s}" for s in structures]
    if question:
        evidence.append("headline is a question")
    if exclaims:
        evidence.append(f"exclamation marks: {title.count('!')} in headline, {a.body.count('!')} in body")
    if caps:
        evidence.append("capitals in headline: " + ", ".join(caps))
    summary = ("Measured wording.", "Some emotive or clickbait wording.", "Heavily sensational wording.")[
        _level("sensationalism", concern)]
    return Signal("sensationalism", concern, summary, evidence,
                  {"headline_terms": len(head), "body_terms": len(body), "body_terms_per_100_words": round(per100, 2),
                   "structures": structures}, confidence=1.0 if head or structures else _length_confidence(words, 80))


def sourcing(a: Article) -> Signal:
    words = _word_count(a.body)
    anonymous = find_terms(a.body, X.ANONYMOUS, a.lang)
    # Text inside anonymous phrases ("स्रोतका अनुसार", "sources said") is not also named attribution.
    named_text = L.normalize(a.body).lower()
    for p in X.lexicon(X.ANONYMOUS, a.lang)[1]:
        named_text = named_text.replace(p, " ")
    attributed = find_terms(named_text, X.ATTRIBUTION, a.lang)
    quotes = len(re.findall(r'"[^"]{10,}"', L.normalize(a.body)))
    per300 = (len(attributed) + quotes) * 300 / words
    anon_share = len(anonymous) / max(1, len(attributed) + len(anonymous))
    concern = round(min(1.0, max(0.0, 1 - per300 / 3) * 0.6 + anon_share * 0.4), 3)
    confidence = _length_confidence(words)
    evidence = [f"named attribution: {t}" for t in dict.fromkeys(attributed)][:8]
    evidence += [f"anonymous or unverified: {t}" for t in dict.fromkeys(anonymous)][:6]
    if quotes:
        evidence.append(f"direct quotes: {quotes}")
    summary = ("Claims are regularly attributed to named sources.", "Some claims lack clear attribution.",
               "Few claims are attributed, or sources are mostly anonymous.")[_level("sourcing", concern)]
    if confidence < 0.6:
        summary += " (Short text: low confidence.)"
    return Signal("sourcing", concern, summary, evidence,
                  {"attributions": len(attributed), "anonymous": len(anonymous), "quotes": quotes,
                   "attributions_per_300_words": round(per300, 2), "anonymous_share": round(anon_share, 2)},
                  confidence=confidence)


_FIRST_PERSON = re.compile(r"\b(i think|i believe|in my view|in my opinion|we must|we should)\b|मलाई लाग्छ|मेरो विचारमा|हामीले .{0,20}पर्छ",
                           re.I)


def framing(a: Article) -> Signal:
    loaded = find_terms(a.text, X.LOADED, a.lang)
    first_person = _FIRST_PERSON.findall(L.normalize(a.text))
    per100 = (len(loaded) + 0.5 * len(first_person)) * 100 / _word_count(a.text)
    concern = _saturate(per100, 1.0)
    summary = ("Neutral wording.", "Some loaded or opinionated wording.",
               "Strongly loaded or opinionated wording for a news report.")[_level("framing", concern)]
    evidence = [f"loaded: {t}" for t in dict.fromkeys(loaded)][:10]
    if first_person:
        evidence.append(f"first-person opinion: {len(first_person)}")
    return Signal("framing", concern, summary, evidence,
                  {"loaded_terms": len(loaded), "first_person": len(first_person), "per_100_words": round(per100, 2)},
                  confidence=_length_confidence(_word_count(a.text), 100))


def headline_consistency(a: Article) -> Signal:
    head_terms = set(L.content_terms(a.title))
    body_terms = set(L.content_terms(a.body))
    lead_terms = set(L.content_terms(" ".join(L.sentences(a.body)[:3])))
    coverage = len(head_terms & body_terms) / len(head_terms) if head_terms else 1.0
    lead_cov = len(head_terms & lead_terms) / len(head_terms) if head_terms else 1.0
    head_facts, body_facts = extract_facts(a.title), extract_facts(a.body)
    body_values = {(f.kind, f.value) for f in body_facts}
    unsupported = [f for f in head_facts if (f.kind, f.value) not in body_values]
    body_hedged = {(f.kind, f.value) for f in body_facts if f.hedged}
    dropped_hedge = [f for f in head_facts if not f.hedged and (f.kind, f.value) in body_hedged]
    missing_numbers = sorted(L.numbers(a.title) - L.numbers(a.body))
    head_sens = find_terms(a.title, X.SENSATIONAL, a.lang)
    body_sens = find_terms(a.body, X.SENSATIONAL, a.lang)
    escalation = bool(head_sens) and not body_sens
    concern = ((1 - coverage) * 0.35 + (1 - lead_cov) * 0.1 + min(1, len(unsupported) + 0.5 * len(missing_numbers)) * 0.35
               + (0.25 if dropped_hedge else 0) + (0.2 if escalation else 0))
    concern = round(min(1.0, concern), 3)
    evidence = []
    absent = sorted(t for t in head_terms - body_terms if not t.isdigit())
    if absent:
        evidence.append("headline words not in the story: " + ", ".join(absent[:8]))
    for f in unsupported:
        evidence.append(f"headline figure not in the story: {f.kind} {f.shown}")
    other_missing = [n for n in missing_numbers if all(str(int(f.value)) != n for f in unsupported)]
    if other_missing:
        evidence.append("headline numbers not in the story: " + ", ".join(other_missing))
    for f in dropped_hedge:
        evidence.append(f"headline states {f.kind} {f.shown} as fact; the story says it is uncertain")
    if escalation:
        evidence.append("headline uses sensational words the story itself doesn't: " + ", ".join(dict.fromkeys(head_sens)))
    summary = ("The story supports its headline.", "The headline goes partly beyond the story.",
               "The headline is not supported by the story.")[_level("headline", concern)]
    return Signal("headline", concern, summary, evidence,
                  {"headline_term_coverage": round(coverage, 3), "lead_coverage": round(lead_cov, 3),
                   "unsupported_figures": [f"{f.kind} {f.shown}" for f in unsupported],
                   "missing_numbers": missing_numbers, "dropped_hedge": bool(dropped_hedge), "escalation": escalation},
                  confidence=1.0 if a.body else 0.0)


def all_signals(a: Article) -> list[Signal]:
    return [headline_consistency(a), sensationalism(a), sourcing(a), framing(a)]
