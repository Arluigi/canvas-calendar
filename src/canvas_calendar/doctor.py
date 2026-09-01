"""Diagnose an install without guessing.

Every failure mode this project has is silent: a token that expired, a term
that ended, a calendar that stopped being writable, a LaunchAgent that never
loaded. Each one presents days later as "my calendar stopped updating". This
command turns all of them into one line of text plus the command that fixes
it, so an agent helping a non-technical user has something concrete to read.

No check may raise. A crashing diagnostic is worse than none, so every check
catches broadly and reports the failure as data.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date, datetime

from canvas_calendar.timeutil import CHICAGO


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str


def render(checks: list[Check]) -> str:
    lines: list[str] = []
    for c in checks:
        lines.append(f"{'✓' if c.ok else '✗'} {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"    fix: {c.fix}")
    failed = [c for c in checks if not c.ok]
    lines.append("")
    lines.append(
        "All checks passed." if not failed else f"{len(failed)} problem(s) found."
    )
    return "\n".join(lines)


def check_term(term, today: date | None = None) -> Check:
    today = today or datetime.now(CHICAGO).date()
    if term.covers(today):
        left = (term.end - today).days
        return Check("Term", True, f"{term.year} {term.season}, {left} days left", "")
    return Check(
        "Term",
        False,
        f"today is outside {term.year} {term.season} ({term.start}..{term.end})",
        "canvas-calendar setup   # pick the current term",
    )


def check_canvas() -> Check:
    from canvas_calendar.canvas.client import CanvasClient, TokenExpired
    from canvas_calendar.config import load_canvas_credentials

    try:
        base, tok = load_canvas_credentials()
        client = CanvasClient(base, tok)
        courses = client.list_courses()
    except TokenExpired:
        return Check(
            "Canvas token", False, "expired or rejected (401)",
            "canvas-calendar token   # prints the renewal steps",
        )
    except Exception as exc:  # noqa: BLE001 -- report, never crash the doctor
        return Check("Canvas token", False, f"{type(exc).__name__}: {exc}",
                     "canvas-calendar setup")

    detail = f"valid, {len(courses)} active courses"
    try:
        from canvas_calendar.daily import token_expiry_status

        days, note = token_expiry_status(client.list_tokens(), datetime.now(CHICAGO))
        if note:
            detail += f"; {note}"
        if days is not None and days <= 7:
            return Check("Canvas token", False, detail,
                         "canvas-calendar token   # expires very soon")
    except Exception as exc:  # noqa: BLE001 -- expiry is a bonus, not the check
        detail += f"; expiry unknown ({type(exc).__name__})"
    return Check("Canvas token", True, detail, "")


def check_calendar() -> Check:
    from canvas_calendar.calendars.factory import UnknownBackend, make_adapter
    from canvas_calendar.config import load_sync_options

    try:
        opts = load_sync_options()
    except Exception as exc:  # noqa: BLE001
        return Check("Calendar", False, f"config unreadable: {exc}",
                     "canvas-calendar setup")
    try:
        _, cal_id = make_adapter(opts)
    except UnknownBackend as exc:
        return Check("Calendar", False, str(exc), "canvas-calendar setup")
    except Exception as exc:  # noqa: BLE001
        return Check("Calendar", False, f"{type(exc).__name__}: {exc}",
                     "canvas-calendar setup")
    return Check(
        "Calendar", True,
        f"{opts['calendar_backend']} -> {opts['assignments_calendar']!r} "
        f"({str(cal_id)[:12]}…)",
        "",
    )


def check_agents() -> Check:
    """Is the scheduled job loaded, and did it last exit cleanly?"""
    try:
        out = subprocess.run(
            ["launchctl", "list"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except Exception as exc:  # noqa: BLE001
        return Check("Scheduled run", False, f"launchctl unavailable: {exc}", "")

    rows = [ln for ln in out.splitlines() if "canvas" in ln.lower()]
    if not rows:
        return Check(
            "Scheduled run", False, "no canvas-calendar agent loaded",
            "canvas-calendar install-agents",
        )
    bad = []
    for r in rows:
        parts = r.split("\t")
        if len(parts) >= 2 and parts[1] not in ("0", "-"):
            bad.append(r.strip())
    if bad:
        return Check(
            "Scheduled run", False, f"last exit non-zero: {bad[0]}",
            "check ~/.config/canvas-calendar/launchd.err.log",
        )
    return Check("Scheduled run", True, f"{len(rows)} agent(s) loaded, last exit 0", "")


def run_checks() -> list[Check]:
    from canvas_calendar.config import load_term

    try:
        term = load_term()
        term_check = check_term(term)
    except Exception as exc:  # noqa: BLE001
        term_check = Check("Term", False, f"unreadable: {exc}", "canvas-calendar setup")
    return [check_canvas(), term_check, check_calendar(), check_agents()]


def main() -> int:
    checks = run_checks()
    print(render(checks))
    return 0 if all(c.ok for c in checks) else 1
