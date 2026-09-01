"""Sync recurring class meetings into the assignments calendar.

Kept in one calendar deliberately: separate calendars mean more things to
toggle, and meetings and deadlines are read together anyway.
"""

from __future__ import annotations

import re

import httpx

from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.catalog.client import USER_AGENT
from canvas_calendar.catalog.parser import Section, parse_section
from canvas_calendar.config import load_canvas_credentials, load_sync_options, load_term
from canvas_calendar.meetings import ClassMeeting, build_meetings

_CX_ROOT = "https://courses.illinois.edu/cisapp/explorer/schedule"


def explorer_base(term) -> str:
    """Course Explorer path for a term. Was hardcoded to 2026/fall, which is
    correct for exactly one semester and silently wrong afterwards."""
    return f"{_CX_ROOT}/{term.year}/{term.season}"
_CODE = re.compile(r"([a-z]{2,4})_(\d{3})_", re.IGNORECASE)


def collect_meetings() -> list[ClassMeeting]:
    term = load_term()
    base, tok = load_canvas_credentials()
    client = CanvasClient(base, tok)
    courses = client.list_courses()
    names = {c["id"]: c.get("name", "") for c in courses}
    codes: dict[str, tuple[str, str]] = {}
    for c in courses:
        m = _CODE.match(c.get("course_code", "") or "")
        if m:
            codes[c.get("name", "")] = (m.group(1).upper(), m.group(2))

    pairs: list[tuple[str, str]] = []
    for e in client.list_enrollments():
        if e.get("type") != "StudentEnrollment":
            continue
        sec = client.get_section(e["course_section_id"])
        pairs.append((names.get(e["course_id"], "?"), sec.get("name", "")))

    def fetch(crn: str) -> Section | None:
        for course, sname in pairs:
            if crn in sname and course in codes:
                subj, num = codes[course]
                r = httpx.get(
                    f"{explorer_base(term)}/{subj}/{num}/{crn}.xml",
                    headers={"User-Agent": USER_AGENT}, timeout=45,
                )
                return parse_section(r.text) if r.status_code == 200 else None
        return None

    return build_meetings(pairs, fetch, term)


def sync_meetings(live: bool = False, calendar_name: str | None = None) -> int:
    meetings = collect_meetings()
    print(f"resolved {len(meetings)} class meeting series:\n")
    for m in meetings:
        print(
            f"  {m.uid:<14} {m.title:<30} {m.meeting.days:<4} "
            f"{m.meeting.start}-{m.meeting.end}  {m.location}"
        )
    if not live:
        print("\nDRY RUN (nothing written) -- pass --live to write")
        return 0

    from canvas_calendar.calendars.factory import make_adapter

    opts = load_sync_options()
    if calendar_name:
        opts = {**opts, "assignments_calendar": calendar_name}
    adapter, cal = make_adapter(opts)
    calendar_name = opts["assignments_calendar"]
    # Cache instructor names: the mail filter uses them to recognise a message
    # from someone who actually teaches you.
    import json as _json

    from canvas_calendar.config import GRAPH_CONFIG

    if GRAPH_CONFIG.exists():
        cfg = _json.loads(GRAPH_CONFIG.read_text())
        cfg["instructors"] = sorted({m.meeting.instructor for m in meetings if m.meeting.instructor})
        GRAPH_CONFIG.write_text(_json.dumps(cfg, indent=2))

    written = sum(1 for m in meetings if adapter.upsert_recurring(cal, m))
    print(f"\nAPPLIED: {written} series written into {calendar_name!r}")
    return 0
