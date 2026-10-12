"""
Integrity report: every signal for one article, an overall score derived
from them, and renderings as JSON and Markdown.

The overall score is a weighted mean of the signals' concerns, turned
into 0-100 where higher means fewer warning signs. It describes how the
story is presented and sourced; it is not a verdict on whether the story
is true, and the report says so.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .analysis.corroboration import Corroboration, RelatedCoverage, corroborate
from .analysis.llm_review import Review, review
from .analysis.signals import Signal, all_signals
from .models import Article
from .retrieve import Index

WEIGHTS = {"headline": 0.25, "sensationalism": 0.2, "sourcing": 0.2, "framing": 0.15, "corroboration": 0.2, "claims": 0.25}

DISCLAIMER = ("Spashta measures how a story is told and sourced, and whether other outlets report it. "
              "It does not decide whether the story is true. Read the evidence, then judge.")
DISCLAIMER_NE = ("स्पष्टले समाचार कसरी लेखिएको छ, स्रोत खुलाइएको छ कि छैन र अरू सञ्चारमाध्यमले पनि त्यही "
                 "रिपोर्ट गरेका छन् कि छैनन् भनेर मात्र जाँच्छ। समाचार सत्य हो वा होइन भन्ने निर्णय पाठकको हो।")


@dataclass
class IntegrityReport:
    url: str
    title: str
    source: str
    lang: str
    score: int
    label: str
    signals: list[Signal]
    related: list[RelatedCoverage] = field(default_factory=list)
    review: Optional[Review] = None
    disclaimer: str = DISCLAIMER

    def to_dict(self) -> dict:
        return {
            "url": self.url, "title": self.title, "source": self.source, "lang": self.lang,
            "score": self.score, "label": self.label,
            "signals": [asdict(s) for s in self.signals],
            "related": [{"url": r.url, "title": r.title, "source": r.source, "lang": r.lang,
                         "sensationalism": r.sensationalism, "role": r.role, "reason": r.reason,
                         "passages": [{"id": h.chunk.id, "text": h.chunk.text, "score": round(h.score, 5),
                                       "matched_terms": h.matched_terms} for h in r.passages]} for r in self.related],
            "review": None if self.review is None else {
                "model": self.review.model, "error": self.review.error, "headline_fair": self.review.headline_fair,
                "headline_note": self.review.headline_note, "claims": [asdict(c) for c in self.review.claims]},
            "disclaimer": self.disclaimer,
        }

    def to_markdown(self) -> str:
        out = [f"# Integrity report: {self.title}", "", f"{self.url}  ", f"Outlet: {self.source or 'unknown'} · Language: {self.lang}",
               "", f"**{self.score}/100 · {self.label}**", ""]
        for s in self.signals:
            conf = "" if s.confidence >= 0.8 else f" · confidence {s.confidence:.2f}"
            out.append(f"## {s.name.capitalize()} · concern {s.concern:.2f}{conf}")
            out.append(s.summary)
            out += [f"- {e}" for e in s.evidence]
            out.append("")
        if self.related:
            out.append("## Other coverage")
            out += [f"- [{r.title}]({r.url}) · {r.source} · {r.lang} · {r.role}" + (f" ({r.reason})" if r.reason else "") for r in self.related]
            out.append("")
        out += ["---", self.disclaimer, "", DISCLAIMER_NE if self.lang == "ne" else ""]
        return "\n".join(out).rstrip() + "\n"


def label_for(score: int) -> str:
    if score >= 75:
        return "Few warning signs"
    if score >= 50:
        return "Some warning signs"
    return "Many warning signs"


def overall(signals: list[Signal]) -> int:
    """Weighted mean concern, each signal's weight scaled by its confidence,
    so a signal with little to go on counts less instead of counting as
    "no concern"."""
    w = {s.name: WEIGHTS.get(s.name, 0.1) * s.confidence for s in signals}
    total = sum(w.values())
    concern = sum(w[s.name] * s.concern for s in signals) / total if total else 0.0
    return round(100 * (1 - concern))


def analyze(article: Article, index: Optional[Index] = None, use_llm: bool = False, connection=None) -> IntegrityReport:
    signals = all_signals(article)
    corr: Optional[Corroboration] = None
    rev: Optional[Review] = None
    if index is not None and index.chunks:
        corr = corroborate(article, index)
        signals.append(corr.signal)
        if use_llm:
            # Only independent coverage is evidence; copies and commentary are not.
            passages = [h for r in corr.related if r.role == "independent" for h in r.passages][:12]
            rev = review(article, passages, connection, cache=index.store)
            if rev.error is None:
                signals.append(rev.signal)
    score = overall(signals)
    return IntegrityReport(article.url, article.title, article.source, article.lang, score, label_for(score),
                           signals, corr.related if corr else [], rev)
