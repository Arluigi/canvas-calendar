"""Extract dates from Canvas module titles and SubHeader text.

Courses publish real schedules here, not in due-date fields. Verified
2026-08-25 across MCB 364 (dated module titles), MCB 320 (dated SubHeaders),
and MCB 436 (slash-dated lecture modules).

This is extraction from authored text, not inference. Where a course omits a
date, that fact is reported rather than guessed around.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# "August 26th", "October 2nd", "December 9"
_NAMED = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)

# A bare day following a named month via a slash: the "28th" in "August 26th/28th".
# Anchored to a leading slash so lecture numbers and part numbers are never picked up.
_BARE_DAY = re.compile(r"^\s*/\s*(\d{1,2})(?:st|nd|rd|th)?\b")

# "8/24 - Lecture 1"
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")

_LOOKAHEAD = 10  # chars after a named date to scan for a slash-joined bare day


def extract_dates(text: str, year: int) -> list[date]:
    """Every date mentioned in a module title, in order of appearance."""
    out: list[date] = []

    for m in _SLASH.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            out.append(date(year, month, day))
    if out:
        return out

    for m in _NAMED.finditer(text):
        month = _MONTHS[m.group(1).lower()]
        out.append(date(year, month, int(m.group(2))))
        bare = _BARE_DAY.match(text[m.end() : m.end() + _LOOKAHEAD])
        if bare:
            out.append(date(year, month, int(bare.group(1))))
    return out


def parse_subheader_date(text: str, year: int) -> date | None:
    """First date in a SubHeader, or None. Ranges take the start date."""
    found = extract_dates(text, year)
    return found[0] if found else None
