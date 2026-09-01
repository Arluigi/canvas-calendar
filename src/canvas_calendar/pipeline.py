"""Wire the read path together: Canvas + module extraction -> Assignment list."""

from __future__ import annotations

import re
from datetime import datetime, time

from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.completion import is_complete
from canvas_calendar.config import load_canvas_credentials, load_sync_options
from canvas_calendar.models import Assignment, CourseRef, Source
from canvas_calendar.modules import extract_dates, parse_subheader_date
from canvas_calendar.overrides import apply_overrides, load_overrides
from canvas_calendar.rules import Disposition, classify
from canvas_calendar.timeutil import CHICAGO, parse_canvas_ts

# An extracted date carries no clock time; treat it as end of day, which the
# renderer then classifies as all-day.
_EXTRACTED_TIME = time(23, 59)

TERM_YEAR = 2026

# SubHeaders worth calendaring. A lecture topic is not a deadline.
_ASSESSMENT = re.compile(r"exam|midterm|quiz|review for|final", re.IGNORECASE)


def term_courses(courses: list[dict]) -> list[dict]:
    """Keep only real term courses. Open and teacher-dev courses have course
    codes that carry no subject/number/term triple and must be dropped rather
    than crashing the run."""
    out = []
    for c in courses:
        try:
            CourseRef.from_canvas_code(c.get("course_code", "") or "")
        except ValueError:
            continue
        out.append(c)
    return out


def build_assignments(raw: list[dict], course: str) -> list[Assignment]:
    out = []
    for a in raw:
        due = a.get("due_at")
        out.append(
            Assignment(
                canvas_id=a["id"],
                name=a.get("name", ""),
                points=a.get("points_possible") or 0.0,
                due_at=parse_canvas_ts(due) if due else None,
                course=course,
                source=Source.CANVAS if due else Source.UNRESOLVED,
                completed=is_complete(a.get("submission")),
            )
        )
    return out


def resolve_undated(
    items: list[Assignment], module_titles: dict[int, str], year: int
) -> list[Assignment]:
    """Give undated assignments a date from their containing module's title.

    Uses the LAST date stated in the title: for a lab week "August 26th/28th"
    the work is due by the end of that week's sessions, not the first one.
    Anything with no parseable date stays UNRESOLVED and goes to the digest --
    never a guess.
    """
    for a in items:
        if a.source is not Source.UNRESOLVED:
            continue
        title = module_titles.get(a.canvas_id, "")
        found = extract_dates(title, year=year)
        if not found:
            continue
        a.due_at = datetime.combine(found[-1], _EXTRACTED_TIME, tzinfo=CHICAGO)
        a.source = Source.EXTRACTED
        a.module = title.strip()
        a.provenance = f"module: {title.strip()}"
    return items


def subheader_events(items: list[dict], course: str, year: int) -> list[Assignment]:
    """Build assessment events from dated SubHeader text.

    Some courses expose no Canvas assignments at all. MCB 320 is the case that
    forced this: zero assignments, no syllabus body, but its entire schedule --
    including four exams -- is published as dated SubHeader text inside modules.
    Reading only /assignments makes such a course silently contribute nothing.

    Only assessments are calendared; a plain lecture topic is not a deadline.
    """
    out: list[Assignment] = []
    for item in items:
        if item.get("type") != "SubHeader":
            continue
        title = (item.get("title") or "").strip()
        if not _ASSESSMENT.search(title):
            continue
        when = parse_subheader_date(title, year=year)
        if when is None:
            continue
        out.append(
            Assignment(
                canvas_id=item["id"],
                name=title,
                points=0.0,
                due_at=datetime.combine(when, _EXTRACTED_TIME, tzinfo=CHICAGO),
                course=course,
                source=Source.EXTRACTED,
                provenance=f"module subheader: {title}",
                namespace="mi-",
            )
        )
    return out


def _walk_modules(
    client: CanvasClient, course_id: int, course: str
) -> tuple[dict[int, str], list[Assignment]]:
    """One pass over a course's modules, serving both extraction paths.

    Returns (assignment_id -> containing module title, subheader events).
    Walked unconditionally: a course with zero assignments still has a schedule
    worth reading, which is exactly how MCB 320 was being missed.
    """
    titles: dict[int, str] = {}
    events: list[Assignment] = []
    for module in client.list_modules(course_id):
        entries = client.list_module_items(course_id, module["id"])
        for entry in entries:
            if entry.get("type") == "Assignment" and entry.get("content_id"):
                titles[entry["content_id"]] = module.get("name", "")
        events.extend(subheader_events(entries, course=course, year=TERM_YEAR))
    return titles, events


def collect(applied: list[str] | None = None) -> list[Assignment]:
    """Full read path against live Canvas. Used by `canvas-calendar preview`."""
    base_url, token = load_canvas_credentials()
    client = CanvasClient(base_url, token)
    exclude = set(load_sync_options()["exclude_assignment_ids"])
    results: list[Assignment] = []

    for course in term_courses(client.list_courses()):
        cid = course["id"]
        label = course.get("name", "") or course.get("course_code", "")
        items = build_assignments(client.list_assignments(cid), course=label)

        titles, events = _walk_modules(client, cid, label)
        items = resolve_undated(items, titles, TERM_YEAR) + events

        for a in items:
            verdict = classify(a, exclude=exclude)
            if verdict is Disposition.SKIP:
                continue
            a.digest_only = verdict is Disposition.DIGEST
            results.append(a)

    # Canvas is not always authoritative. Applied last so a corrected date
    # wins over whatever Canvas or module extraction produced.
    return apply_overrides(results, load_overrides(), applied)
