"""
Word lists behind the deterministic signals, in English and Nepali.

Single words match a token's start, so Nepali inflections count
(सनसनी matches सनसनीपूर्ण, भ्रष्ट matches भ्रष्टहरूले). Entries with a space
match as phrases in the normalised, lowercased text.

These lists are a starting point, kept deliberately short and high
precision. Every match is shown in the report, so a bad entry is easy to
spot and remove. Add terms with a test that shows the match.
"""

from __future__ import annotations

SENSATIONAL = {
    "en": """
shocking shocked stunning stunned unbelievable incredible outrage outraged outrageous slams slammed blasts blasted
destroys destroyed horrifying horrific terrifying terrified explosive bombshell chaos mayhem devastating catastrophic
massive epic insane jaw-dropping mind-blowing miracle exposed scandalous sensational nightmare furious fury panic
disaster deadly brutal savage heartbreaking alarming
you won't believe|what happened next|goes viral|went viral|must see|must watch|the truth about|will shock you|
you need to know|never before|worst ever|biggest ever|once and for all|breaking news
""",
    "ne": """
सनसनी चौंकाउने चकित आश्चर्यजनक अचम्म भण्डाफोर हंगामा तहल्का खतरनाक डरलाग्दो भयावह कहालीलाग्दो दर्दनाक
भाइरल ब्रेकिङ विस्फोटक हल्लीखल्ली स्तब्ध त्राहिमाम आतंक भयानक लज्जाजनक ठूलो_खुलासा
हेर्नुहोस्|नसोचेको|होस उड्ने|सबै छक्क|यस्तो छ|थाहा पाउनुहोस्|भिडियो सहित|सामाजिक सञ्जालमा भाइरल|अहिलेसम्मकै ठूलो
""",
}

ATTRIBUTION = {
    "en": """
said says told stated confirmed announced explained added noted according spokesperson spokesman spokeswoman
statement interview testified
according to|in a statement|told reporters|press release
""",
    "ne": """
बताए बताइन् बताउनुभयो बताएका बताएकी बताएको जनाए जनाइन् जनाउनुभयो जनाएका जनाएको अनुसार उल्लेख पुष्टि विज्ञप्ति
प्रवक्ता भनाइ बताउँछन् बताउँछिन् दाबी
उनले भने|उनले भनिन्|उहाँले भन्नुभयो|पत्रकार सम्मेलन|प्रेस विज्ञप्ति
""",
}

ANONYMOUS = {
    "en": """
reportedly allegedly rumoured rumored unnamed unconfirmed
sources said|a source|sources say|on condition of anonymity|it is said|it is believed|insiders say|people familiar
""",
    "ne": """
हल्ला आशंका
स्रोतका अनुसार|स्रोतले|नाम नबताउने|नाम उल्लेख नगर्ने|भनिएको छ|भनिन्छ|चर्चा छ|बताइन्छ|सुनिएको छ
""",
}

LOADED = {
    "en": """
regime thug thugs corrupt puppet traitor traitors propaganda so-called disgrace disgraceful shameful notorious
looters mafia criminal crooks evil idiotic pathetic ridiculous clearly obviously undoubtedly
""",
    "ne": """
भ्रष्ट कठपुतली देशद्रोही गद्दार लज्जास्पद निर्लज्ज तथाकथित कुख्यात तानाशाह लुटेरा माफिया दलाल बेशर्म नालायक
पक्कै निश्चय_नै स्पष्ट_रूपमा
""",
}

HEDGES = {
    "en": "may might could possibly perhaps likely apparently suggests unclear",
    "ne": "सम्भवतः शायद हुनसक्ने हुनसक्छ देखिन्छ अनुमान",
}


def _phrases(block: str) -> list[str]:
    out = []
    for line in block.strip().splitlines():
        if "|" in line:
            out.extend(p.strip().lower() for p in line.split("|") if p.strip())
    return out


def _words(block: str) -> list[str]:
    out = []
    for line in block.strip().splitlines():
        if "|" not in line:
            out.extend(w.replace("_", " ").lower() for w in line.split())
    return out


def lexicon(table: dict[str, str], lang: str) -> tuple[list[str], list[str]]:
    """(words, phrases) for a language; 'mixed' text gets both."""
    langs = ["en", "ne"] if lang in ("mixed", "unknown") else ["ne" if lang in ("ne", "hi") else "en"]
    words, phrases = [], []
    for code in langs:
        block = table.get(code, "")
        w = _words(block)
        phrases += [x for x in w if " " in x] + _phrases(block)
        words += [x for x in w if " " not in x]
    return words, phrases
