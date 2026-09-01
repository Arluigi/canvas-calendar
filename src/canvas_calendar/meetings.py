"""Class meeting times: Canvas enrollment -> Course Explorer -> recurring event.

Section resolution turned out to be free. Canvas section names embed the CRN
that Course Explorer uses as its section id:

    "MCB 354 ADI Fall 2026 CRN40604"  ->  section 40604

so the mapping is deterministic and needs no input from the user. The design
spec listed this as an open question expected to require manual confirmation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from canvas_calendar.models import Meeting
from canvas_calendar.terms import DEFAULT_TERM, Term

_CRN = re.compile(r"CRN(\d{5,6})")

# Retained as module-level aliases so existing callers and tests keep working.
# The term itself now lives in terms.py and is overridable from config.
TERM_START = DEFAULT_TERM.start
TERM_END = DEFAULT_TERM.end
NON_INSTRUCTION: list[date] = list(DEFAULT_TERM.holidays)

# Graph wants lowercase English day names in its recurrence pattern.
_GRAPH_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


@dataclass
class ClassMeeting:
    """One recurring class meeting, ready to become a calendar series."""

    crn: str
    course: str
    section: str
    meeting: Meeting
    start_date: date
    end_date: date

    @property
    def uid(self) -> str:
        # Distinct namespace: a meeting series is not an assignment, and the
        # two ID spaces must never collide.
        return f"cc-mtg-{self.crn}"

    @property
    def title(self) -> str:
        kind = self.meeting.kind or "Class"
        return f"{self.course} {kind}"

    @property
    def location(self) -> str:
        parts = [self.meeting.building, self.meeting.room]
        return " ".join(p for p in parts if p and p.lower() != "n.a.").strip()


def extract_crn(section_name: str) -> str | None:
    """Pull the Course Explorer section id out of a Canvas section name."""
    m = _CRN.search(section_name or "")
    return m.group(1) if m else None


def parse_clock(raw: str) -> time | None:
    """'02:00PM' -> time(14, 0). Returns None for ARRANGED/online sections."""
    raw = (raw or "").strip()
    if not raw or raw.upper() in {"ARRANGED", "N.A."}:
        return None
    # A clock time carries no date or zone; .time() is the whole point.
    return datetime.strptime(raw, "%I:%M%p").time()  # noqa: DTZ007


def first_occurrence(weekdays: list[int], on_or_after: date) -> date | None:
    """Earliest date on or after `on_or_after` falling on one of `weekdays`.

    Graph requires the series start to be a real occurrence of the pattern; a
    start that does not match silently shifts the whole series.
    """
    if not weekdays:
        return None
    for offset in range(7):
        candidate = on_or_after + timedelta(days=offset)
        if candidate.weekday() in weekdays:
            return candidate
    return None


def graph_days(weekdays: list[int]) -> list[str]:
    return [_GRAPH_DAYS[d] for d in weekdays]


def excluded_dates(weekdays: list[int], term: Term = DEFAULT_TERM) -> list[date]:
    """Non-instruction days that actually fall on this meeting's weekdays.

    Only these need cancelling; a Monday holiday is irrelevant to a
    Tuesday/Thursday lecture, and asking Graph to cancel a nonexistent
    occurrence is an error.
    """
    return [d for d in term.holidays if d.weekday() in weekdays]


def build_meetings(
    enrollments: list[tuple[str, str]],
    fetch_section,
    term: Term = DEFAULT_TERM,
) -> list[ClassMeeting]:
    """Turn (course_label, canvas_section_name) pairs into ClassMeetings.

    `fetch_section` takes a CRN and returns a parsed Section. Sections with no
    real meeting time -- online and ARRANGED ones such as FSHN 120 -- are
    dropped rather than becoming events with no time or place.
    """
    out: list[ClassMeeting] = []
    for course, section_name in enrollments:
        crn = extract_crn(section_name)
        if not crn:
            continue
        section = fetch_section(crn)
        if section is None:
            continue
        for meeting in section.meetings:
            if not meeting.weekdays() or parse_clock(meeting.start) is None:
                continue  # online / ARRANGED
            out.append(
                ClassMeeting(
                    crn=crn,
                    course=course,
                    section=section_name,
                    meeting=meeting,
                    start_date=section.start_date or term.start,
                    end_date=section.end_date or term.end,
                )
            )
    return out
