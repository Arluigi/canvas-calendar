"""Inclusion rules.

Point value is NOT a proxy for importance. Measured 2026-08-25: MCB 364's 22
undated lab items are worth 2-3 points each, while FSHN 120 carries 20
zero-point items -- seven of which (PILLAR A-D reflective assignments,
PILLAR B/C/D DI quizzes) carry no extra-credit marker and read as required
coursework. An earlier `points > 0` filter would have dropped exactly those
seven, silently.

Rule order matters. A 0-point assignment is calendared by default: being wrong
that way adds an event, being wrong the other way loses coursework.
"""

from __future__ import annotations

import re
from enum import Enum

from canvas_calendar.models import Assignment

_EXTRA_CREDIT = re.compile(r"extra\s*credit", re.IGNORECASE)


class Disposition(str, Enum):
    CALENDAR = "calendar"
    DIGEST = "digest"
    SKIP = "skip"


def classify(assignment: Assignment, exclude: set[int] | None = None) -> Disposition:
    """Decide where an assignment goes. Order is significant."""
    if exclude and assignment.canvas_id in exclude:
        return Disposition.SKIP
    if _EXTRA_CREDIT.search(assignment.name):
        return Disposition.DIGEST
    return Disposition.CALENDAR
