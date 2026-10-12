"""
Language handling for Nepali and English.

Everything downstream (search, signals, number checks) sees text that went
through normalize() first, so the same word or number always looks the same:

  - Unicode NFC, so a precomposed letter and its combining form compare equal
  - zero-width joiners and soft hyphens removed
  - Devanagari digits folded to ASCII (२०८३ -> 2083)
  - typographic quotes and dashes folded to plain ASCII

Nepali attaches case markers and plurals to the noun (सरकारले, सरकारको,
सरकारलाई, नेताहरूले), so stem() strips them; without it lexical search
misses most matches. English gets a light suffix stemmer.

detect_language() is script based: Devanagari share of letters, with a
Hindi check so Hindi text isn't scored with Nepali lexicons.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")
_FOLD = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
    "​": None, "‌": None, "‍": None, "­": None, "﻿": None,
})

# Devanagari letters and signs, excluding the danda (। ॥) which ends a sentence.
_DEVA = "ऀ-ॣॱ-ॿ"
TOKEN_RE = re.compile(rf"[{_DEVA}]+|[A-Za-z]+(?:'[A-Za-z]+)?|\d+(?:[.,]\d+)*")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
SENTENCE_END_RE = re.compile(r"(?<=[.!?।॥])\s+|\n{2,}")

NE_STOPWORDS = frozenset("""
र तथा एवं पनि नै छ छन् छु छौं हो होइन थियो थिए थिएन भयो भए भएको भएका भएकी हुन हुने हुन्छ हुँदा
यो त्यो यी ती यस त्यस उक्त ती उनी उनले उनको उनका वहाँ हामी तपाईं म मेरो आफ्नो आफू
गर्न गरे गरेको गरेका गरी गर्ने गर्दै गरेर गरिएको भने भन्ने भन्दै भनेर भनी लागि लागे सक्ने
एक दुई अब आज हिजो भोलि अझै अनि तर वा कि किनभने जस्तै जुन जो जसले जसको कुनै केही सबै धेरै
रहेको रहेका रहेकी रहे रहन्छ थप साथै अनुसार बारे बीच बिच माथि तल भित्र बाहिर
""".split())

EN_STOPWORDS = frozenset("""
a an the and or but if of to in on at by for with from as is are was were be been being it its
this that these those he she they them his her their we our you your i me my not no so than then
there here has have had do does did will would can could may might shall should into over about
after before up down out more most also just only very said says say
""".split())

# Longest first, so हरूलाई is removed whole rather than as लाई.
NE_SUFFIXES = tuple(sorted("""
हरूलाई हरूले हरूको हरूका हरूकी हरूमा हरूबाट हरूसँग हरुलाई हरुले हरुको हरुका हरुमा हरुबाट
हरू हरु लाई ले को का की मा बाट देखि सम्म सँग संग तिर भन्दा द्वारा प्रति माथि भित्र
""".split(), key=len, reverse=True))
MIN_STEM = 3  # codepoints; keeps short words like आमा or सीमा whole

# Words frequent in Hindi but rare in Nepali, and the reverse.
_HI_MARKERS = frozenset("है हैं में के की था थे नहीं और भी यह वह".split())
_NE_MARKERS = frozenset("छ छन् हो ले लाई को मा थियो पनि र यो त्यो".split())


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    return text.translate(_FOLD).translate(DEVANAGARI_DIGITS)


def is_devanagari(token: str) -> bool:
    return bool(token) and "ऀ" <= token[0] <= "ॿ"


def detect_language(text: str) -> str:
    """'ne', 'en', 'hi', 'mixed' or 'unknown'."""
    text = normalize(text)
    deva = sum(1 for c in text if "ऄ" <= c <= "ह" or "क़" <= c <= "ॡ" or "ॲ" <= c <= "ॿ")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    letters = deva + latin
    if letters < 3:
        return "unknown"
    share = deva / letters
    if share < 0.15:
        return "en"
    if share < 0.7:
        return "mixed"
    words = TOKEN_RE.findall(text)
    hi = sum(w in _HI_MARKERS for w in words)
    ne = sum(w in _NE_MARKERS for w in words)
    return "hi" if hi > ne * 1.5 and hi >= 3 else "ne"


def _stem_en(word: str) -> str:
    """Light, consistent suffix stripping: the stem only has to be the same
    for every form of a word (price, prices, priced -> pric), not readable."""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("sses"):
        word = word[:-2]
    elif re.search(r"(sh|ch|x|z)es$", word):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ingly", "edly", "ing", "ed", "ly"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            break
    if word.endswith("e") and len(word) > 3:
        word = word[:-1]
    return word


def _stem_ne(word: str) -> str:
    for _ in range(2):  # नेताहरूले -> नेता: plural and case marker
        for suffix in NE_SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= MIN_STEM:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word


@lru_cache(maxsize=200_000)
def stem(token: str) -> str:
    if is_devanagari(token):
        return _stem_ne(token)
    if token.isascii() and token.isalpha():
        return _stem_en(token.lower())
    return token


def tokens(text: str) -> list[str]:
    """Normalised surface tokens, lowercased for Latin script."""
    return [t.lower() if t.isascii() else t for t in TOKEN_RE.findall(normalize(text))]


def content_terms(text: str) -> list[str]:
    """Stemmed tokens without stopwords: what search and overlap checks use."""
    out = []
    for t in tokens(text):
        if t in NE_STOPWORDS or t in EN_STOPWORDS:
            continue
        s = stem(t)
        if len(s) > 1 or s.isdigit():
            out.append(s)
    return out


def numbers(text: str) -> set[str]:
    """Numbers as they'd be compared: Devanagari digits folded, thousands
    separators removed, so १,२०० and 1200 match."""
    out = set()
    for n in NUMBER_RE.findall(normalize(text)):
        n = re.sub(r",(?=\d{2,3}\b)", "", n).rstrip(".,")
        if n:
            out.add(n)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_END_RE.split(normalize(text)) if s and s.strip()]
