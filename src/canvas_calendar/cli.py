"""Read-path CLI. Prints; never writes to a calendar."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import is_end_of_day, to_local

_FAR_FUTURE = datetime.max.replace(tzinfo=UTC)


def _when(a: Assignment) -> str:
    if a.due_at is None:
        return "unresolved"
    if is_end_of_day(a.due_at):
        return f"{to_local(a.due_at):%a %b %d} (all day)"
    return f"{to_local(a.due_at):%a %b %d, %-I:%M %p}"


def render_preview(assignments: list[Assignment]) -> str:
    if not assignments:
        return "No assignments found."
    lines: list[str] = []
    for a in sorted(assignments, key=lambda x: x.due_at or _FAR_FUTURE):
        tag = " [extracted]" if a.source is Source.EXTRACTED else ""
        lines.append(f"{_when(a):<28} {a.course:<10} {a.name[:56]}{tag}")
        if a.provenance:
            lines.append(f"{'':<28} └─ {a.provenance}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="canvas-calendar")
    parser.add_argument("command", choices=["preview"])
    parser.parse_args()

    from canvas_calendar.pipeline import collect

    print(render_preview(collect()))
    return 0
