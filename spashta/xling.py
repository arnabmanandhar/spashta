"""
Cross-language and cross-script matching without a neural model.

Three bridges, each producing extra index terms that live in their own
BM25 index (the "xling" ranking fused into hybrid search):

  romanize()       Devanagari -> simplified Latin (सिन्धुपाल्चोक -> sindhupalchok,
                   पेट्रोल -> petrol), with word-final schwa deletion.
  phonetic_key()   collapses spelling variation on both sides (aa/a, ee/i,
                   v/w/b, aspirates, sh/s), so a romanised query
                   ("petrol ko mulya badhyo") meets Devanagari text, and
                   English loanwords and proper nouns (petrol, Sindhupalchok,
                   Kirtipur) meet their Nepali spellings.
  concepts()       a small bilingual glossary of news vocabulary
                   (पहिरो = landslide, मृत्यु = killed/dead/death), so common
                   event words match across languages.

This is the offline floor. A multilingual embedder (e5, bge-m3) does the
same job better for paraphrase; the two are complementary and RRF fuses
them.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources

from . import lang as L

_VOWELS = {"अ": "a", "आ": "a", "इ": "i", "ई": "i", "उ": "u", "ऊ": "u", "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au"}
_SIGNS = {"ा": "a", "ि": "i", "ी": "i", "ु": "u", "ू": "u", "ृ": "ri", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॅ": "e", "ॉ": "o"}
_CONS = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ng", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "ny",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "w", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "क़": "k", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f", "य़": "y",
}
_HALANT, _NUKTA = "्", "़"
_NASAL = {"ं": "n", "ँ": "n", "ः": "h"}


def romanize(word: str) -> str:
    """Simplified romanisation of one Devanagari word, as Nepali readers
    type it: no diacritics, inherent 'a' dropped at the end of the word
    unless the last letter is a conjunct (मूल्य -> mulya, घर -> ghar)."""
    out: list[str] = []
    pending_a = False  # a consonant waiting for its inherent vowel
    last_was_conjunct = False
    chars = list(L.normalize(word))
    i = 0
    while i < len(chars):
        c = chars[i]
        if i + 1 < len(chars) and chars[i + 1] == _NUKTA:
            c += _NUKTA
            i += 1
        if c in _CONS:
            if pending_a:
                out.append("a")
            last_was_conjunct = i > 0 and chars[i - 1] == _HALANT
            out.append(_CONS[c])
            pending_a = True
        elif c == _HALANT:
            pending_a = False
        elif c in _SIGNS:
            out.append(_SIGNS[c])
            pending_a = False
        elif c in _VOWELS:
            if pending_a:
                out.append("a")
            out.append(_VOWELS[c])
            pending_a = False
        elif c in _NASAL:
            if pending_a:
                out.append("a")
                pending_a = False
            out.append(_NASAL[c])
        i += 1
    if pending_a and last_was_conjunct:
        out.append("a")
    return "".join(out)


_KEY_RULES = [
    (r"chh", "ch"), (r"ph", "f"), (r"sh", "s"), (r"([kgjtdpb])h", r"\1"), (r"[vw]", "b"), (r"z", "j"), (r"q", "k"),
    (r"c(?!h)", "k"), (r"x", "ks"), (r"y$", "i"), (r"aa+", "a"), (r"ee+|ii+", "i"), (r"oo+|uu+", "u"), (r"(.)\1+", r"\1"),
]


def phonetic_key(latin: str) -> str:
    key = latin.lower()
    for pattern, repl in _KEY_RULES:
        key = re.sub(pattern, repl, key)
    return key


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads((resources.files("spashta") / "data" / "glossary.json").read_text(encoding="utf-8"))


def event_concepts() -> frozenset[str]:
    return frozenset(_data()["event_concepts"])


def named_concepts() -> frozenset[str]:
    return frozenset(_data()["named_concepts"])


_NAME_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\b")
_ACRONYM_RE = re.compile(r"\b([A-Z]{2,5})\b")


def name_keys(text: str) -> set[str]:
    """Phonetic keys of likely proper names in English text: capitalised
    words that don't start a sentence, plus short acronyms. Used to learn
    which keys are names, which then also applies to Devanagari text."""
    out = set()
    for line in L.normalize(text).splitlines():
        for m in [*_NAME_RE.finditer(line), *_ACRONYM_RE.finditer(line)]:
            w = m.group(1).lower()
            if w not in L.EN_STOPWORDS:
                out.add(phonetic_key(L.stem(w)))
    return out - glossary_keys()


@lru_cache(maxsize=1)
def glossary_keys() -> frozenset[str]:
    """Phonetic keys of every glossary word: common vocabulary, never names."""
    keys = set()
    for _concept, en, ne in _data()["concepts"]:
        for w in en + ne:
            for t in L.content_terms(w):
                keys.add(phonetic_key(romanize(t) if L.is_devanagari(t) else t))
    return frozenset(keys)


@lru_cache(maxsize=1)
def _glossary() -> tuple[dict[str, str], list[tuple[str, str]]]:
    data = _data()
    words: dict[str, str] = {}
    phrases: list[tuple[str, str]] = []
    for concept, en, ne in data["concepts"]:
        for w in en + ne:
            w = L.normalize(w).lower()
            if " " in w:
                phrases.append((w, concept))
            else:
                words.setdefault(L.stem(w), concept)
    return words, phrases


def concepts(text: str) -> list[str]:
    words, phrases = _glossary()
    out = [words[t] for t in L.content_terms(text) if t in words]
    norm = L.normalize(text).lower()
    for phrase, concept in phrases:
        out.extend([concept] * norm.count(phrase))
    return out


def xling_terms(text: str) -> list[str]:
    """Index terms for the cross-language ranking: phonetic keys of every
    content word (romanised first if Devanagari) and glossary concepts."""
    terms = []
    for t in L.content_terms(text):
        if t.isdigit():
            continue
        latin = romanize(t) if L.is_devanagari(t) else t
        if len(latin) >= 3:
            terms.append("p:" + phonetic_key(latin))
    terms += ["c:" + c for c in concepts(text)]
    return terms
