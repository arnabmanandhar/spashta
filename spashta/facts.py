"""
Quantified facts: "12 dead", "५ जना बेपत्ता", "Rs 180 a litre", "48 percent".

A bare number means little; a number attached to what it counts is a fact
two outlets can agree or disagree on. extract_facts() finds quantities
(digits in either script, number words in English and Nepali, lakh/crore/
million scaling) and labels each with the nearest fact kind from the
glossary (death, missing, injured, house, price, percent...). It also
notes when a figure is hedged ("feared", "आशंका", "up to").

compare_facts() then says, kind by kind, whether other coverage reports
the same value, a different one (a conflict worth showing), or nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import lang as L
from . import xling

# "one" and एक are left out: far more often "a" than a count.
EN_UNITS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
            "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
            "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
            "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "dozen": 12}
# छ (six) is left out on purpose: it is also the commonest verb ("is").
NE_UNITS = {"दुई": 2, "तीन": 3, "चार": 4, "पाँच": 5, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "एघार": 11,
            "बाह्र": 12, "तेह्र": 13, "चौध": 14, "पन्ध्र": 15, "सोह्र": 16, "सत्र": 17, "अठार": 18, "उन्नाइस": 19,
            "बीस": 20, "तीस": 30, "चालीस": 40, "पचास": 50, "साठी": 60, "सत्तरी": 70, "असी": 80, "नब्बे": 90}
SCALES = {"hundred": 100, "thousand": 1_000, "lakh": 100_000, "lakhs": 100_000, "million": 1_000_000, "crore": 10_000_000,
          "crores": 10_000_000, "billion": 1_000_000_000, "सय": 100, "हजार": 1_000, "लाख": 100_000, "करोड": 10_000_000,
          "अर्ब": 1_000_000_000, "खर्ब": 100_000_000_000}
HEDGES = ("feared", "suspected", "up to", "could", "may", "estimated", "unconfirmed", "reportedly", "about", "nearly",
          "आशंका", "सम्भावना", "अनुमान", "करिब", "झन्डै", "लगभग", "हुनसक्ने", "हुन सक्ने")

# What a figure can count, most specific first; the nearest one wins.
FACT_KINDS = ("death", "missing", "injured", "house", "wicket", "run", "percent", "litre", "kilometre", "rupee",
              "student", "people", "family")
WINDOW = 4  # tokens either side

_TOKEN = re.compile(rf"{L.TOKEN_RE.pattern}|%")
# Facts don't cross clause boundaries: "12 dead, five missing" is two facts.
_CLAUSE = re.compile(r"[,;:]\s|\s(?:तर|but|while|whereas)\s")


@dataclass(frozen=True)
class Fact:
    kind: str
    value: float
    text: str  # the words it came from
    hedged: bool = False

    @property
    def shown(self) -> str:
        v = int(self.value) if self.value == int(self.value) else self.value
        return f"{v}{' (hedged)' if self.hedged else ''}"


def _value(tok: str) -> float | None:
    if L.NUMBER_RE.fullmatch(tok):
        try:
            return float(re.sub(r",(?=\d{2,3}\b)", "", tok))
        except ValueError:
            return None
    return EN_UNITS.get(tok) if tok.isascii() else NE_UNITS.get(tok)


def _kinds(tokens: list[str]) -> list[str | None]:
    out = []
    for t in tokens:
        if t == "%":
            out.append("percent")
            continue
        cs = xling.concepts(t)
        out.append(next((c for c in cs if c in FACT_KINDS), None))
    return out


def extract_facts(text: str) -> list[Fact]:
    text = L.normalize(text)
    facts: list[Fact] = []
    clauses = [c for s in L.sentences(text) for c in _CLAUSE.split(s) if c.strip()]
    for clause in clauses:
        toks = [t.lower() if t.isascii() else t for t in _TOKEN.findall(clause)]
        kinds = _kinds(toks)
        low = clause.lower()
        hedged_sentence = any(h in low for h in HEDGES)
        i = 0
        while i < len(toks):
            v = _value(toks[i])
            if v is None:
                i += 1
                continue
            start = i
            # "Rs 5 crore", "२ लाख", "five hundred": apply scale words that follow
            while i + 1 < len(toks) and toks[i + 1] in SCALES:
                v *= SCALES[toks[i + 1]]
                i += 1
            if len(toks[start]) == 4 and toks[start].isdigit() and toks[start][:2] in ("19", "20") and start == i:
                i += 1  # a bare year
                continue
            candidates = []
            for j in range(max(0, start - WINDOW), min(len(toks), i + 1 + WINDOW)):
                if kinds[j] and not start <= j <= i:
                    d = j - i if j > i else start - j
                    # generic counts lose to specific ones; then nearest; then the word
                    # after the number, which is how both languages name what was counted
                    candidates.append((kinds[j] in ("people", "family"), d, j < start, kinds[j]))
            best = min(candidates)[3] if candidates else None
            if best:
                facts.append(Fact(best, v, " ".join(toks[max(0, start - 2):i + 3]), hedged_sentence))
            i += 1
    return facts


@dataclass
class FactCheck:
    fact: Fact
    support: list[str]  # sources reporting the same value
    dissent: dict[str, list[float]]  # sources reporting this kind of figure with other values
    status: str  # confirmed | conflict | disputed | unreported


def compare_facts(ours: list[Fact], others: dict[str, list[Fact]]) -> list[FactCheck]:
    """For each of our figures: which independent sources report the same
    value for the same kind of fact, and which report a different one.
    Majority decides confirmed or conflict; a tie is disputed."""
    out, seen = [], set()
    for f in ours:
        if (f.kind, f.value) in seen:
            continue
        seen.add((f.kind, f.value))
        support, dissent = [], {}
        for source, facts in others.items():
            values = {g.value for g in facts if g.kind == f.kind}
            if f.value in values:
                support.append(source)
            elif values:
                dissent[source] = sorted(values)
        if not support and not dissent:
            status = "unreported"
        elif len(support) > len(dissent):
            status = "confirmed"
        elif len(dissent) > len(support):
            status = "conflict"
        else:
            status = "disputed"
        out.append(FactCheck(f, support, dissent, status))
    return out
