"""Interactive first-run setup, also drivable by a coding agent.

The step order matters and is not cosmetic. Calendar permission is requested
while the user is present, BEFORE the LaunchAgents are installed: a background
agent has its own TCC identity, and its first run blocks on a permission
prompt. Scheduled at 07:15, that prompt appears to nobody and the job stalls
silently -- the exact failure this project exists to prevent. Measured on
2026-08-31: a first agent run blocked 37 seconds waiting for that dialog.

`write_config` reports every change, and names both sides of an overwrite.
Setup must never quietly replace a working setting.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path

from canvas_calendar.config import GRAPH_CONFIG
from canvas_calendar.timeutil import CHICAGO

CREDENTIALS = Path.home() / ".config" / "canvas-calendar" / "credentials.json"
DEFAULT_CALENDAR = "UIUC Assignments"


def _terms() -> list[dict]:
    return json.loads((Path(__file__).parent / "data" / "terms.json").read_text())


def choose_term(today: date | None = None) -> dict:
    """The term containing today, else the next one to start."""
    today = today or datetime.now(CHICAGO).date()
    terms = sorted(_terms(), key=lambda t: t["start"])
    for t in terms:
        if date.fromisoformat(t["start"]) <= today <= date.fromisoformat(t["end"]):
            return t
    for t in terms:
        if date.fromisoformat(t["start"]) > today:
            return t
    return terms[-1]


def write_config(updates: dict, *, path: Path | None = None) -> list[str]:
    """Merge `updates` into config.json, describing every change.

    Returns one human-readable line per change; an unchanged value produces
    no line. An overwrite is reported with both values.
    """
    p = Path(path or GRAPH_CONFIG)
    existing = json.loads(p.read_text()) if p.exists() else {}
    changes: list[str] = []
    for k, v in updates.items():
        if k not in existing:
            changes.append(f"{k}: {v!r}  (added)")
        elif existing[k] != v:
            changes.append(f"{k}: {existing[k]!r} -> {v!r}  (overwritten)")
        existing[k] = v
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2) + "\n")
    return changes


def save_canvas_token(token: str, base_url: str = "https://canvas.illinois.edu") -> Path:
    """Write the Canvas token at mode 0600, created restrictive from the outset
    rather than chmod-ed afterwards."""
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CREDENTIALS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"CANVAS_API_TOKEN": token, "CANVAS_API_URL": base_url}, fh)
    os.chmod(CREDENTIALS, 0o600)
    return CREDENTIALS


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    return input(f"{prompt}{suffix}: ").strip() or default


def _writable_calendars(adapter) -> list[str]:
    cals = adapter._store.calendarsForEntityType_(adapter._ek.EKEntityTypeEvent) or []
    return [c.title() for c in cals if c.allowsContentModifications()]


def main() -> int:
    print("canvas-calendar setup\n")

    # 1. Backend.
    print("Which calendar do you use?")
    print("  1) iCloud, Google or Outlook already set up in the macOS Calendar app")
    print("  2) Outlook via Microsoft sign-in (no Calendar app needed)")
    backend = "eventkit" if _ask("Choice", "1") == "1" else "outlook"

    # 2. Permission FIRST, while a human is here to answer the prompt.
    if backend == "eventkit":
        from canvas_calendar.calendars.eventkit import (
            CalendarAccessDenied,
            EventKitAdapter,
        )

        print("\nmacOS will ask permission to use your calendars. Please allow it.")
        try:
            adapter = EventKitAdapter()
        except CalendarAccessDenied as exc:
            print(f"\n{exc}")
            return 1
        writable = _writable_calendars(adapter)
        if not writable:
            print(
                "\nNo writable calendars found. Add your calendar account in\n"
                "System Settings > Internet Accounts, then run setup again.\n"
                "A Google account that only exists in a browser tab is not enough."
            )
            return 1
        print("\nWritable calendars: " + ", ".join(writable))
        cal_name = _ask("Calendar to use (created if absent)", DEFAULT_CALENDAR)
    else:
        from canvas_calendar.calendars.device_login import device_login

        if device_login() != 0:
            return 1
        cal_name = _ask("Calendar to use", DEFAULT_CALENDAR)

    # 3. Canvas token.
    print(
        "\nCanvas token: canvas.illinois.edu > Account > Settings > "
        "+ New Access Token"
    )
    print(
        "  No such button? Request one (takes a day or two) — see the README, "
        "then re-run setup."
    )
    token = _ask("Paste it here")
    if token:
        print(f"  saved to {save_canvas_token(token)} (mode 0600)")

    # 4. Debrief. Needs Graph Mail.Send, so it is offered only on outlook.
    debrief: dict = {}
    if backend == "outlook":
        to = _ask("Morning debrief email address (blank to skip)")
        if to:
            debrief = {"debrief_enabled": True, "debrief_to": to}
    else:
        print(
            "\nMorning debrief needs the Outlook backend; skipping. "
            "Use `canvas-calendar digest` instead."
        )

    # 5. Term.
    term = choose_term()
    print(f"\nTerm: {term['year']} {term['season']} "
          f"({term['start']} to {term['end']})")

    for line in write_config(
        {
            "calendar_backend": backend,
            "assignments_calendar": cal_name,
            "term": term,
            **debrief,
        }
    ):
        print(f"  config {line}")

    # 6. Verify before offering to schedule anything.
    #
    # The "Scheduled run" check is deliberately excluded from both the gate
    # AND the output. The agents genuinely are not installed yet -- that is
    # the next step setup tells the user to take -- so showing it here ends a
    # successful setup with a red cross and "1 problem(s) found", which reads
    # as failure to exactly the user this wizard exists for.
    from canvas_calendar.doctor import render, run_checks

    print()
    checks = [c for c in run_checks() if c.name != "Scheduled run"]
    print(render(checks))
    if not all(c.ok for c in checks):
        print("\nFix the above, then run: canvas-calendar setup")
        return 1

    print("\nNext:")
    print("  canvas-calendar sync            # dry run -- shows what would happen")
    print("  canvas-calendar sync --live     # writes it")
    print("  canvas-calendar install-agents  # run it twice a day")
    return 0
