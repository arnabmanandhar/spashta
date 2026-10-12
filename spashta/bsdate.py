"""
Bikram Sambat dates.

Nepali outlets often print only the BS date (२०८३ असोज २४) and use BS
years in URLs (/2083/06/...). Same-story matching needs a Gregorian date,
so parse_bs_date() finds the first BS date in a text or URL and converts
it with the `nepali-datetime` package (optional extra: spashta[nepali]).
Without the package, BS dates are recognised but not converted.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from . import lang as L

MONTHS = {
    "बैशाख": 1, "वैशाख": 1, "जेठ": 2, "जेष्ठ": 2, "असार": 3, "आषाढ": 3, "साउन": 4, "श्रावण": 4, "भदौ": 5, "भाद्र": 5,
    "असोज": 6, "आश्विन": 6, "कात्तिक": 7, "कार्तिक": 7, "मंसिर": 8, "मङ्सिर": 8, "मार्ग": 8, "पुस": 9, "पौष": 9,
    "माघ": 10, "फागुन": 11, "फाल्गुन": 11, "चैत": 12, "चैत्र": 12,
}
_NAMED = re.compile(r"\b(20[6-9]\d)\s*(?:साल\s*)?(" + "|".join(MONTHS) + r")\s*(\d{1,2})\b")
_NUMERIC = re.compile(r"(?<!\d)(20[6-9]\d)[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")


def to_gregorian(year: int, month: int, day: int) -> Optional[datetime]:
    try:
        import nepali_datetime
    except ImportError:
        return None
    try:
        d = nepali_datetime.date(year, month, day).to_datetime_date()
    except (ValueError, OverflowError):
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def find_bs_date(text: str) -> Optional[tuple[int, int, int]]:
    text = L.normalize(text)
    m = _NAMED.search(text)
    if m:
        return int(m.group(1)), MONTHS[m.group(2)], int(m.group(3))
    m = _NUMERIC.search(text)
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 32:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    return None


def parse_bs_date(text: str) -> Optional[datetime]:
    found = find_bs_date(text)
    return to_gregorian(*found) if found else None
