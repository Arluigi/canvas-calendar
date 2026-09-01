"""The daily unattended run.

Everything here is shaped by one rule: a scheduled job that fails quietly is
worse than no job at all, because it manufactures false confidence. So every
exit path is either a visible success or a visible failure.
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from canvas_calendar.apply import apply_plan
from canvas_calendar.calendars.graph_auth import AuthError, GraphAuth
from canvas_calendar.calendars.outlook import OutlookAdapter
from canvas_calendar.canvas.client import TokenExpired
from canvas_calendar.config import load_graph_client_id, load_sync_options
from canvas_calendar.diff import Action, diff
from canvas_calendar.models import Source
from canvas_calendar.pipeline import collect
from canvas_calendar.state import DEFAULT_STATE_PATH, StateStore
from canvas_calendar.timeutil import CHICAGO, to_local

LOG_DIR = Path.home() / ".config" / "canvas-calendar"
LOG_PATH = LOG_DIR / "daily.log"
DIGEST_PATH = LOG_DIR / "digest.md"

# Illinois caps Canvas token lifetime near 30 days, so expiry is a scheduled
# certainty rather than an edge case. Warn while there is still time to act.
TOKEN_WARN_DAYS = 5


def notify(title: str, message: str, *, urgent: bool = False) -> None:
    """macOS notification. Best-effort: never let the notifier break the run."""
    import subprocess

    body = message.replace('"', "'")[:200]
    sound = "Basso" if urgent else "Glass"
    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{body}" with title "{title}" sound name "{sound}"',
            ],
            check=False,
            timeout=10,
        )
    except Exception:  # noqa: BLE001,S110 -- a failed toast must not fail the sync
        pass


def token_expiry_status(tokens: list[dict], now: datetime) -> tuple[int | None, str]:
    """Days until the soonest-expiring active token, and a message.

    Canvas OAuth2 refresh tokens would remove this chore entirely, but Canvas
    developer keys are issued only by institution admins, so a student cannot
    self-provision one. Illinois additionally blocks token create and
    regenerate through the API. Renewal therefore stays manual -- which makes
    advance warning the entire mitigation.
    """
    from canvas_calendar.timeutil import parse_canvas_ts, to_local

    soonest, when = None, None
    for t in tokens:
        if t.get("workflow_state") not in (None, "active"):
            continue
        raw = t.get("expires_at")
        if not raw:
            return None, "token does not expire"
        exp = parse_canvas_ts(raw)
        days = (exp - now).days
        if soonest is None or days < soonest:
            soonest, when = days, exp
    if soonest is None:
        return None, "no active tokens found"
    return soonest, f"Canvas token expires in {soonest} days ({to_local(when):%b %d})"


def _log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(CHICAGO).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a") as fh:
        fh.write(f"{stamp}  {line}\n")


def run(dry_run: bool = False) -> int:
    """One daily pass. Returns a process exit code."""
    try:
        return _run(dry_run)
    except TokenExpired as exc:
        _log(f"FAIL canvas token: {exc}")
        notify(
            "Canvas Calendar — token expired",
            "Regenerate at canvas.illinois.edu → Settings, then run: canvas-calendar login",
            urgent=True,
        )
        return 2
    except AuthError as exc:
        _log(f"FAIL outlook auth: {exc}")
        notify("Canvas Calendar — Outlook auth failed", str(exc), urgent=True)
        return 3
    except Exception as exc:  # noqa: BLE001 -- the whole point is not failing silently
        _log(f"FAIL unexpected: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        notify("Canvas Calendar — sync failed", f"{type(exc).__name__}: {exc}", urgent=True)
        return 1


def _run(dry_run: bool) -> int:
    overrides_applied: list[str] = []
    assignments = collect(overrides_applied)

    store = StateStore(DEFAULT_STATE_PATH)
    plan = diff(assignments, store)

    opts = load_sync_options()
    auth = GraphAuth(client_id=load_graph_client_id())
    adapter = OutlookAdapter(
        auth=auth,
        reminder_timed=opts["reminder_minutes_timed"],
        reminder_all_day=opts["reminder_minutes_all_day"],
    )
    calendar_id = adapter.ensure_calendar("UIUC Assignments")

    errors: list[str] = []
    counts = apply_plan(plan, adapter, calendar_id, store, dry_run=dry_run, errors=errors)

    # Expiry is a scheduled certainty here, not an edge case. Check it every
    # run so the failure is announced days early rather than discovered as a
    # calendar that quietly stopped updating.
    token_note = ""
    try:
        from canvas_calendar.canvas.client import CanvasClient
        from canvas_calendar.config import load_canvas_credentials

        base_url, tok = load_canvas_credentials()
        days, token_note = token_expiry_status(
            CanvasClient(base_url, tok).list_tokens(), datetime.now(CHICAGO)
        )
        if days is not None and days <= TOKEN_WARN_DAYS:
            _log(f"WARN {token_note}")
            notify("Canvas Calendar — token expiring", token_note, urgent=True)
    except Exception as exc:  # noqa: BLE001 -- expiry check must never break the sync
        token_note = f"could not check token expiry: {exc}"

    write_digest(plan, counts, errors, overrides_applied, token_note)

    changed = counts["create"] + counts["update"] + counts["delete"]
    _log(f"ok {dict(counts)}" + (f" errors={len(errors)}" if errors else ""))

    if errors:
        notify("Canvas Calendar — finished with errors", f"{len(errors)} failed", urgent=True)
        return 1
    if changed:
        notify(
            "Canvas Calendar updated",
            f"{counts['create']} new, {counts['update']} changed, {counts['delete']} removed",
        )
    return 0


def write_digest(plan, counts, errors, overrides_applied, token_note: str = "") -> Path:
    """The digest is where everything the calendar cannot show goes.

    A calendar answers 'what is due'. It cannot answer 'what changed since
    yesterday' or 'what does this system know about but refuse to guess at'.
    Those are exactly the questions that let work slip, so they get their own
    document, rewritten every run.
    """
    now = datetime.now(CHICAGO)
    soon = now + timedelta(days=7)
    lines = [f"# Canvas Calendar — {now:%A, %B %d %Y at %I:%M %p}", ""]

    # 1. What changed. The only genuinely new information most days.
    changed = [p for p in plan if p.action in (Action.CREATE, Action.UPDATE, Action.DELETE)]
    lines.append("## Changed since last run")
    if changed:
        for p in changed:
            name = p.assignment.name[:60] if p.assignment else "(removed)"
            course = p.assignment.course if p.assignment else ""
            when = (
                f"{to_local(p.assignment.due_at):%a %b %d}"
                if p.assignment and p.assignment.due_at
                else ""
            )
            lines.append(f"- **{p.action.value}** {course} — {name} {when}")
    else:
        lines.append("- nothing changed")
    lines.append("")

    # 2. Corrections we made to Canvas. Never silent.
    if overrides_applied:
        lines += ["## Manual corrections applied", ""]
        lines += [f"- {line}" for line in overrides_applied] + [""]

    # 3. Due in the next 7 days, so the digest stands alone.
    upcoming = [
        a
        for a in (p.assignment for p in plan if p.assignment)
        if a.due_at and now <= a.due_at <= soon and not a.digest_only and not a.completed
    ]
    lines += [f"## Due in the next 7 days ({len(upcoming)})", ""]
    for a in sorted(upcoming, key=lambda x: x.due_at):
        lines.append(f"- {to_local(a.due_at):%a %b %d %I:%M %p} — **{a.course}** {a.name[:64]}")
    if not upcoming:
        lines.append("- nothing due")
    lines.append("")

    # 4. What we removed because Canvas says it is done. Named, never merely
    #    counted: this is the only place a false-positive completion becomes
    #    visible before the work is missed.
    cleared = [
        p.assignment
        for p in plan
        if p.action is Action.DELETE and p.assignment and p.assignment.completed
    ]
    if cleared:
        lines += [f"## Cleared as completed ({len(cleared)})", ""]
        lines += [
            f"- {a.course} — {a.name[:60]}"
            + (f" (was due {to_local(a.due_at):%a %b %d})" if a.due_at else "")
            for a in cleared
        ]
        lines += [
            "",
            (
                "> Removed from the calendar because Canvas reports them submitted, "
                "graded or excused. If something here is not actually done, it was "
                "graded early or marked in error — check Canvas."
            ),
            "",
        ]

    # 5. Extra credit, which is real work the calendar deliberately excludes.
    ec = [
        a
        for a in (p.assignment for p in plan if p.assignment)
        if a.digest_only and a.due_at and now <= a.due_at <= soon
    ]
    if ec:
        lines += ["## Extra credit open this week", ""]
        lines += [
            f"- {to_local(a.due_at):%a %b %d} — {a.course} {a.name[:60]}"
            for a in sorted(ec, key=lambda x: x.due_at)
        ] + [""]

    # 6. The blind spots. The single most important section: work this system
    #    knows exists but cannot place on a calendar. Repeated every run until
    #    it is resolved, because a gap you stop being told about is a gap you
    #    forget.
    unresolved = [
        p.assignment
        for p in plan
        if p.action is Action.SKIP
        and p.assignment
        and p.assignment.source is Source.UNRESOLVED
    ]
    lines += [f"## Not on your calendar — no date available ({len(unresolved)})", ""]
    if unresolved:
        by_course: dict[str, list[str]] = {}
        for a in unresolved:
            by_course.setdefault(a.course, []).append(a.name[:56])
        for course, names in sorted(by_course.items()):
            lines.append(f"- **{course}** ({len(names)}): {', '.join(names[:6])}")
            if len(names) > 6:
                lines.append(f"  …and {len(names) - 6} more")
        lines.append("")
        lines.append(
            "> These are real assignments with no due date in Canvas and no date in "
            "their module. They are listed here every run precisely so they do not "
            "become invisible."
        )
    else:
        lines.append("- none")
    lines.append("")

    if errors:
        lines += ["## Errors", ""] + [f"- {e}" for e in errors] + [""]

    if token_note:
        lines += ["## Credentials", "", f"- {token_note}", ""]

    lines += ["---", f"`{dict(counts)}`"]

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DIGEST_PATH.write_text("\n".join(lines))
    return DIGEST_PATH


def main() -> int:
    return run(dry_run="--dry-run" in sys.argv)
