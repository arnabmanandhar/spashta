"""
Coarse topic tags for stratified sampling: politics, economy, disaster,
crime, sports, or other.

The section in the URL decides when there is one (/politics/, /khelkud/,
/business/: Nepali outlets file stories by section). Otherwise keywords in
the headline (counted twice) and the first paragraphs vote. This only has
to spread a labelling sample across topics; annotators correct the tag,
and the corrected tag is what gets published.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlparse

from .. import lang as L

TOPICS = ("politics", "economy", "disaster", "crime", "sports", "other")

_SECTIONS = {
    "politics": r"politics|political|rajniti|rajneeti|parliament|election|nirwachan",
    "economy": r"economy|economic|business|arthatantra|artha|bazar|market|finance|banking|share-market|biz|money",
    "sports": r"sports?|khelkud|khel|cricket|football",
    "crime": r"crime|aparadh|court|law",
    "disaster": r"disaster|bipad|weather",
}
_SECTION_RE = {t: re.compile(rf"/({p})(/|$|-)", re.I) for t, p in _SECTIONS.items()}

KEYWORDS = {
    "politics": """parliament government minister prime party election congress uml maoist coalition president lawmaker
        cabinet opposition speaker federal provincial mayor ward vote संसद सरकार प्रधानमन्त्री मन्त्री दल पार्टी कांग्रेस
        एमाले माओवादी निर्वाचन चुनाव राष्ट्रपति सांसद गठबन्धन मन्त्रिपरिषद् प्रतिपक्ष सभामुख प्रदेश मेयर वडाध्यक्ष मतदान""",
    "economy": """budget bank banks market nepse inflation price prices trade investment revenue interest remittance economy
        economic tax tariff export import fuel petrol loan deposit बजेट बैंक बजार नेप्से मुद्रास्फीति मूल्य महँगी व्यापार
        लगानी राजस्व ब्याज रेमिट्यान्स अर्थतन्त्र आर्थिक कर निर्यात आयात इन्धन पेट्रोल ऋण निक्षेप शेयर""",
    "disaster": """landslide landslides flood floods flooding earthquake quake fire inundation lightning avalanche disaster
        rescue missing displaced पहिरो बाढी भूकम्प आगलागी डुबान चट्याङ हिमपहिरो विपद् उद्धार बेपत्ता विस्थापित""",
    "crime": """police arrested arrest murder murdered theft rape fraud court case drugs smuggling robbery charged
        custody prison verdict प्रहरी पक्राउ हत्या चोरी बलात्कार ठगी अदालत मुद्दा लागूऔषध तस्करी डकैती अभियोग हिरासत
        जेल फैसला""",
    "sports": """cricket football match goal goals wicket wickets tournament champion championship league player players
        coach olympic क्रिकेट फुटबल खेल खेलाडी गोल विकेट म्याच प्रतियोगिता च्याम्पियन लिग प्रशिक्षक""",
}
_KW = {t: {L.stem(L.normalize(w).lower()) for w in words.split()} for t, words in KEYWORDS.items()}


def section_topic(url: str) -> str | None:
    path = urlparse(url).path.lower()
    hits = [t for t, rx in _SECTION_RE.items() if rx.search(path)]
    return hits[0] if len(hits) == 1 else None


def _hits(text: str) -> Counter:
    c: Counter = Counter()
    for t in L.content_terms(text):
        for topic, words in _KW.items():
            if t in words or (L.is_devanagari(t) and any(t.startswith(w) for w in words if len(w) >= 3)):
                c[topic] += 1
    return c


def topic_of(url: str, title: str, body: str) -> str:
    section = section_topic(url)
    if section:
        return section
    votes = _hits(title) + _hits(title) + _hits(" ".join(L.sentences(body)[:4]))
    if not votes:
        return "other"
    (best, n), *rest = votes.most_common(2) + [("", 0)]
    return best if n >= 2 and n > rest[0][1] else "other"
