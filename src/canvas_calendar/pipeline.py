"""Wire the read path together: Canvas + module extraction -> Assignment list."""

from __future__ import annotations

from datetime import datetime, time

from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.config import load_canvas_credentials
from canvas_calendar.models import Assignment, CourseRef, Source
from canvas_calendar.modules import extract_dates
from canvas_calendar.rules import Disposition, classify
from canvas_calendar.timeutil import CHICAGO, parse_canvas_ts

# An extracted date carries no clock time; treat it as end of day, which the
# renderer then classifies as all-day.
_EXTRACTED_TIME = time(23, 59)

TERM_YEAR = 2026


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


def _module_title_by_assignment(client: CanvasClient, course_id: int) -> dict[int, str]:
    titles: dict[int, str] = {}
    for module in client.list_modules(course_id):
        for entry in client.list_module_items(course_id, module["id"]):
            if entry.get("type") == "Assignment" and entry.get("content_id"):
                titles[entry["content_id"]] = module.get("name", "")
    return titles


def collect() -> list[Assignment]:
    """Full read path against live Canvas. Used by `canvas-calendar preview`."""
    base_url, token = load_canvas_credentials()
    client = CanvasClient(base_url, token)
    results: list[Assignment] = []

    for course in term_courses(client.list_courses()):
        cid = course["id"]
        label = course.get("name", "") or course.get("course_code", "")
        items = build_assignments(client.list_assignments(cid), course=label)

        if any(a.source is Source.UNRESOLVED for a in items):
            items = resolve_undated(items, _module_title_by_assignment(client, cid), TERM_YEAR)

        results.extend(a for a in items if classify(a) is not Disposition.SKIP)
    return results
