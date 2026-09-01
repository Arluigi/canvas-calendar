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


RENEWAL_STEPS = """
Renew (about a minute):
  1. canvas.illinois.edu -> Account -> Settings -> + New Access Token
  2. Purpose: canvas-calendar. Copy the value now; Canvas shows it once.
  3. In a terminal:
       cd ~/code/canvas-mcp
       NEW_CANVAS_TOKEN='paste-here' node scripts/rotate-canvas-token.mjs

Canvas OAuth2 refresh tokens would remove this chore entirely, but Canvas
developer keys are issued only by institution admins, and Illinois blocks
both token creation and regeneration through the API (verified: HTTP 403 on
each). Renewal is therefore manual by policy, not by design.
"""


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


def _sync(live: bool, course: str | None = None, force: bool = False) -> int:
    from canvas_calendar.apply import apply_plan
    from canvas_calendar.calendars.factory import make_adapter
    from canvas_calendar.config import load_sync_options
    from canvas_calendar.diff import diff
    from canvas_calendar.pipeline import collect
    from canvas_calendar.state import DEFAULT_STATE_PATH, StateStore

    applied: list[str] = []
    assignments = collect(applied)
    if applied:
        print("MANUAL OVERRIDES APPLIED:")
        for line in applied:
            print(f"   {line}")
        print()
    if course:
        # Narrowing the write set is how a first live run stays reversible:
        # verify formatting on one course before committing the whole term.
        assignments = [a for a in assignments if course.lower() in a.course.lower()]
        print(f"filtered to course matching {course!r}: {len(assignments)} items")
    store = StateStore(DEFAULT_STATE_PATH)
    # A filtered run must never prune: the courses we did not fetch are not
    # gone, and pruning on a subset would delete all of their events.
    plan = diff(assignments, store, prune=course is None, force=force)
    print(render_plan(plan))

    opts = load_sync_options()
    adapter, calendar_id = make_adapter(opts)

    errors: list[str] = []
    counts = apply_plan(plan, adapter, calendar_id, store, dry_run=not live, errors=errors)

    print(f"\n{'APPLIED' if live else 'DRY RUN (nothing written)'}: {dict(counts)}")
    for e in errors:
        print(f"   error: {e}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="canvas-calendar")
    parser.add_argument("command", choices=["preview", "sync", "meetings", "daily", "digest", "token", "debrief", "login"])
    parser.add_argument(
        "--live",
        action="store_true",
        help="actually write to the calendar (default is a dry run)",
    )
    parser.add_argument(
        "--course",
        default=None,
        help="limit the sync to courses whose name contains this string",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="rewrite every event even if unchanged (use after changing formatting or reminders)",
    )
    args = parser.parse_args()

    if args.command == "login":
        from canvas_calendar.calendars.device_login import device_login

        return device_login()
    if args.command == "debrief":
        from canvas_calendar.run_debrief import run as run_debrief

        return run_debrief(send=args.live, force=args.force)
    if args.command == "token":
        from datetime import datetime

        from canvas_calendar.canvas.client import CanvasClient
        from canvas_calendar.config import load_canvas_credentials
        from canvas_calendar.daily import token_expiry_status
        from canvas_calendar.timeutil import CHICAGO

        b, t = load_canvas_credentials()
        days, msg = token_expiry_status(CanvasClient(b, t).list_tokens(), datetime.now(CHICAGO))
        print(msg)
        if days is not None and days <= 14:
            print(RENEWAL_STEPS)
        return 0
    if args.command == "daily":
        from canvas_calendar.daily import run

        return run(dry_run=not args.live)
    if args.command == "digest":
        from canvas_calendar.daily import DIGEST_PATH

        print(DIGEST_PATH.read_text() if DIGEST_PATH.exists() else "no digest yet")
        return 0
    if args.command == "meetings":
        from canvas_calendar.sync_meetings import sync_meetings

        return sync_meetings(live=args.live)
    if args.command == "preview":
        from canvas_calendar.pipeline import collect

        print(render_preview(collect()))
        return 0
    return _sync(live=args.live, course=args.course, force=args.force)
