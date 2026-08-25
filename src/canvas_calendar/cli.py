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


def render_plan(plan) -> str:
    """Human-readable diff plan. Shown before anything is written."""
    from canvas_calendar.diff import Action

    order = [Action.CREATE, Action.UPDATE, Action.DELETE, Action.NOOP, Action.SKIP]
    lines = []
    for action in order:
        rows = [p for p in plan if p.action is action]
        if not rows:
            continue
        lines.append(f"\n{action.value.upper()} ({len(rows)})")
        for p in rows[:200]:
            label = p.assignment.name[:60] if p.assignment else "(stale state row)"
            course = p.assignment.course if p.assignment else ""
            lines.append(f"   {p.uid:<16} {course:<10} {label}")
    return "\n".join(lines) or "nothing to do"


def _sync(live: bool) -> int:
    from canvas_calendar.apply import apply_plan
    from canvas_calendar.calendars.graph_auth import GraphAuth
    from canvas_calendar.calendars.outlook import OutlookAdapter
    from canvas_calendar.config import load_graph_client_id
    from canvas_calendar.diff import diff
    from canvas_calendar.pipeline import collect
    from canvas_calendar.state import DEFAULT_STATE_PATH, StateStore

    assignments = collect()
    store = StateStore(DEFAULT_STATE_PATH)
    plan = diff(assignments, store)
    print(render_plan(plan))

    auth = GraphAuth(client_id=load_graph_client_id())
    adapter = OutlookAdapter(auth=auth)
    calendar_id = adapter.ensure_calendar("UIUC Assignments")

    errors: list[str] = []
    counts = apply_plan(plan, adapter, calendar_id, store, dry_run=not live, errors=errors)

    print(f"\n{'APPLIED' if live else 'DRY RUN (nothing written)'}: {dict(counts)}")
    for e in errors:
        print(f"   error: {e}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="canvas-calendar")
    parser.add_argument("command", choices=["preview", "sync"])
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually write to the calendar (default is a dry run)",
    )
    args = parser.parse_args()

    if args.command == "preview":
        from canvas_calendar.pipeline import collect

        print(render_preview(collect()))
        return 0
    return _sync(live=args.live)
