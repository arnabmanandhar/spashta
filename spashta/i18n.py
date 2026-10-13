"""
Nepali labels for the report page.

Signal names, score labels, coverage roles and the fixed summary
sentences in Nepali, so a shared report reads in both languages.
Summaries built from numbers (corroboration counts) stay in English.
Corrections from native readers are welcome: change the string here and
keep the English key exactly as the code produces it.
"""

from __future__ import annotations

SIGNALS = {
    "headline": "शीर्षक",
    "sensationalism": "सनसनी",
    "sourcing": "स्रोत",
    "framing": "शब्द चयन",
    "corroboration": "अरू स्रोतबाट पुष्टि",
    "claims": "दाबी जाँच",
}
SIGNALS_EN = {"headline": "Headline", "sensationalism": "Sensationalism", "sourcing": "Sourcing",
              "framing": "Framing", "corroboration": "Corroboration", "claims": "Claim check"}

LABELS = {
    "Few warning signs": "थोरै चेतावनी संकेत",
    "Some warning signs": "केही चेतावनी संकेत",
    "Many warning signs": "धेरै चेतावनी संकेत",
}

ROLES = {"independent": "स्वतन्त्र", "copy": "प्रतिलिपि", "same-owner": "एउटै मालिक", "commentary": "टिप्पणी"}

SUMMARIES = {
    "Measured wording.": "सन्तुलित शब्द।",
    "Some emotive or clickbait wording.": "केही भावनात्मक वा क्लिकबेट शब्द।",
    "Heavily sensational wording.": "अत्यधिक सनसनीपूर्ण शब्द।",
    "Claims are regularly attributed to named sources.": "दाबीहरू प्रायः नाम खुलेका स्रोतमा आधारित छन्।",
    "Some claims lack clear attribution.": "केही दाबीको स्रोत स्पष्ट छैन।",
    "Few claims are attributed, or sources are mostly anonymous.": "थोरै दाबीको मात्र स्रोत खुलेको छ, वा स्रोतहरू प्रायः अज्ञात छन्।",
    "Neutral wording.": "तटस्थ शब्द।",
    "Some loaded or opinionated wording.": "केही पक्षपाती वा विचारयुक्त शब्द।",
    "Strongly loaded or opinionated wording for a news report.": "समाचारका लागि धेरै पक्षपाती वा विचारयुक्त शब्द।",
    "The story supports its headline.": "समाचारले शीर्षकलाई पुष्टि गर्छ।",
    "The headline goes partly beyond the story.": "शीर्षकले समाचारभन्दा केही बढी दाबी गर्छ।",
    "The headline is not supported by the story.": "समाचारले शीर्षकलाई पुष्टि गर्दैन।",
    "No independent coverage found in the index yet; the story is unverified, not disproven.":
        "अरू स्वतन्त्र सञ्चारमाध्यमको समाचार अहिलेसम्म भेटिएन; समाचार पुष्टि भएको छैन, गलत प्रमाणित पनि भएको छैन।",
}
LOW_CONFIDENCE = (" (Short text: low confidence.)", " (छोटो पाठ: कम भरपर्दो।)")

UI = {
    "points": ("अंक", "points"),
    "other_coverage": ("अरू सञ्चारमाध्यमको समाचार", "Other coverage"),
    "created": ("रिपोर्ट बनाइएको", "Report created"),
    "snapshot": ("यो रिपोर्ट बनाइएको बेलाको अवस्था हो; पछि आएका समाचार यसमा समावेश छैनन्।",
                 "This report is a snapshot from when it was made; later coverage isn't included."),
    "check_another": ("अर्को समाचार जाँच्नुहोस्", "Check another story"),
    "share": ("सेयर गर्ने स्थायी लिङ्क", "Permanent link to share"),
    "copy": ("कपी", "Copy"),
}


def summary_ne(summary: str) -> str:
    """Nepali for a signal summary, or '' when there is none."""
    low = summary.endswith(LOW_CONFIDENCE[0])
    base = summary[: -len(LOW_CONFIDENCE[0])] if low else summary
    ne = SUMMARIES.get(base, "")
    return ne + LOW_CONFIDENCE[1] if ne and low else ne


def bundle() -> dict:
    """Everything the browser page needs, as one JSON-able dict."""
    return {"signals": SIGNALS, "signals_en": SIGNALS_EN, "labels": LABELS, "roles": ROLES, "summaries": SUMMARIES,
            "low": list(LOW_CONFIDENCE), "ui": UI}
