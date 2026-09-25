"""Assessments published on course pages and in syllabus bodies.

MCB 354 keeps its four exams on a wiki page called 'Exam Information', linked
from the home page. Not in /assignments, not in modules. Exam 1 (Sep 16) was
absent from the calendar for two weeks before this was read (2026-09-08).
MCB 244 and MCB 364 state their final exams only in syllabus prose.

Extraction, not inference: a line has to NAME an assessment ('Exam 1',
'Final Exam', 'Midterm', 'Quiz 3') and STATE a date. Time ranges are taken
when present. Anything without both is ignored, and lines about reviews,
practice exams, answer keys and conflict requests are never exams.

Every hit is reported each run, so a wrong one is visible before it matters.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date, datetime, time

from canvas_calendar.models import Assignment, Source
from canvas_calendar.modules import find_dates
from canvas_calendar.timeutil import CHICAGO

_EOD = time(23, 59)

_NAME = re.compile(r"\b(final exam|midterm(?: exam)?(?: \d+)?|exam \d+|quiz \d+)\b", re.IGNORECASE)
# Between the name and its date, these words mean the line is ABOUT the exam,
# not the exam itself.
_NOT_THE_EXAM = re.compile(
    r"review|practice|answer key|conflict|make-?up|regrade|sign-?up|solutions", re.IGNORECASE
)
_MERIDIEM = r"(a\.?m\.?|p\.?m\.?)"
_RANGE = re.compile(
    rf"\b(\d{{1,2}})(?::(\d{{2}}))?\s*{_MERIDIEM}?\s*(?:-|–|—|to|until)\s*"
    rf"(\d{{1,2}})(?::(\d{{2}}))?\s*{_MERIDIEM}\b",
    re.IGNORECASE,
)
_SINGLE = re.compile(rf"\b(\d{{1,2}})(?::(\d{{2}}))?\s*{_MERIDIEM}\b", re.IGNORECASE)

_ROW = re.compile(r"(?is)<tr\b.*?</tr>")
_BLOCK_END = re.compile(r"(?i)<br\s*/?>|</(?:p|div|li|h[1-6]|table|ul|ol|tr)>")
_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"(?is)<(script|style)\b.*?</\1>")
_LOOKBEHIND = 24  # chars before a name to check for 'Practice', 'Review' etc.


def _flatten_row(m: re.Match) -> str:
    row = re.sub(r"(?i)</t[dh]>", " | ", m.group(0))
    # Canvas writes a newline between cells; the row must stay one unit.
    return "\n" + " ".join(_TAG.sub(" ", row).split()) + "\n"


def _units(raw: str) -> list[str]:
    """Table rows and block elements, one line each, tags gone."""
    s = _SCRIPT.sub(" ", raw or "")
    s = _ROW.sub(_flatten_row, s)
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = _html.unescape(s).replace("\xa0", " ")
    return [" ".join(line.split()) for line in s.splitlines() if line.strip()]


def _hour(h: str, minute: str | None, meridiem: str | None) -> time | None:
    hh, mm = int(h), int(minute or 0)
    if not (1 <= hh <= 12 and 0 <= mm < 60):
        return None
    if meridiem and meridiem.lower().startswith("p") and hh != 12:
        hh += 12
    if meridiem and meridiem.lower().startswith("a") and hh == 12:
        hh = 0
    return time(hh, mm)


def _times(text: str) -> tuple[time | None, time | None]:
    """(start, end) from '7:00-9:00 PM', '8 to 11am', '11-1pm', '6:15pm'."""
    m = _RANGE.search(text)
    if m:
        h1, m1, mer1, h2, m2, mer2 = m.groups()
        end = _hour(h2, m2, mer2)
        start = _hour(h1, m1, mer1 or mer2)
        if start is None or end is None:
            return None, None
        if mer1 is None and start >= end:
            # '11-1pm': the start meridiem is the other one.
            flipped = "am" if mer2.lower().startswith("p") else "pm"
            start = _hour(h1, m1, flipped)
        return start, end
    m = _SINGLE.search(text)
    if m:
        return _hour(*m.groups()), None
    return None, None


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _canonical(raw: str) -> str:
    return " ".join(raw.split()).title()


def extract_assessments(
    raw_html: str,
    course: str,
    course_id: int,
    year: int,
    where: str,
    notes: list[str] | None = None,
) -> list[Assignment]:
    """Named, dated assessments in one page body.

    `where` names the source ("page 'Exam Information'", "syllabus") and is
    carried in provenance and in every note. `notes` receives one line per
    conflicting restatement (same name, different date) so the digest shows
    it; the first statement wins.
    """
    if notes is None:
        notes = []
    found: dict[str, Assignment] = {}

    for unit in _units(raw_html):
        names = list(_NAME.finditer(unit))
        if not names:
            continue
        dates = find_dates(unit, year)
        if not dates:
            continue
        for k, nm in enumerate(names):
            region_end = names[k + 1].start() if k + 1 < len(names) else len(unit)
            after = [d for d in dates if nm.end() <= d[0] < region_end]
            if not after:
                continue
            _, d_end, when = after[0]
            window = unit[max(0, nm.start() - _LOOKBEHIND) : d_end]
            if _NOT_THE_EXAM.search(window):
                continue
            tail = unit[d_end:region_end]
            start, end = _times(tail)
            if start is None:
                start, end = _times(unit[nm.end() : region_end])
            _record(
                found,
                notes,
                course,
                course_id,
                where,
                _canonical(nm.group(1)),
                when,
                start,
                end,
                snippet=unit[nm.start() : min(len(unit), d_end + 40)],
            )
    return list(found.values())


def _record(found, notes, course, course_id, where, name, when: date, start, end, *, snippet):
    due = datetime.combine(when, start or _EOD, tzinfo=CHICAGO)
    ends = datetime.combine(when, end, tzinfo=CHICAGO) if (start and end and end > start) else None
    prior = found.get(name)
    if prior is not None:
        if prior.due_at.date() != when:
            notes.append(
                f"{course} {name}: also stated as {when:%b %d} on {where}; "
                f"kept {prior.due_at:%b %d}"
            )
        return
    found[name] = Assignment(
        canvas_id=f"{course_id}-{_slug(name)}",
        name=name,
        points=0.0,
        due_at=due,
        course=course,
        source=Source.EXTRACTED,
        provenance=f"{where}: {snippet}",
        namespace="pg-",
        ends_at=ends,
    )


def course_page_events(
    client, course_id: int, course: str, year: int, notes: list[str]
) -> list[Assignment]:
    """Every page the student can see, plus the syllabus body.

    Reads all of them rather than guessing which title holds the schedule:
    MCB 354's is 'Exam Information', but the next course's will not be. A
    course with the Pages tab disabled contributes nothing and costs one 404.
    Repeats across pages of one course collapse to the first statement.
    """
    out: dict[str, Assignment] = {}
    sources = [
        (
            f"page '{p.get('title', p.get('url', ''))}'",
            client.get_page(course_id, p["url"]).get("body") or "",
        )
        for p in client.list_pages(course_id)
        if p.get("url")
    ]
    syllabus = client.get_syllabus_body(course_id)
    if syllabus:
        sources.append(("syllabus", syllabus))

    for where, body in sources:
        for a in extract_assessments(body, course, course_id, year, where, notes=notes):
            prior = out.get(a.uid)
            if prior is None:
                out[a.uid] = a
                notes.append(
                    f"found {course} {a.name} {a.due_at:%a %b %d %-I:%M %p}"
                    + (f"-{a.ends_at:%-I:%M %p}" if a.ends_at else "")
                    + f" on {where}"
                )
            elif prior.due_at != a.due_at:
                notes.append(
                    f"{course} {a.name}: {where} says {a.due_at:%b %d %-I:%M %p}; "
                    f"kept {prior.due_at:%b %d %-I:%M %p} from {prior.provenance.split(':')[0]}"
                )
    return list(out.values())
