"""Sync recurring class meetings into the assignments calendar.

Kept in one calendar deliberately: separate calendars mean more things to
toggle, and meetings and deadlines are read together anyway.
"""

from __future__ import annotations

import re

import httpx

from canvas_calendar.calendars.graph_auth import GraphAuth
from canvas_calendar.calendars.outlook import OutlookAdapter
from canvas_calendar.catalog.client import USER_AGENT
from canvas_calendar.catalog.parser import Section, parse_section
from canvas_calendar.config import load_canvas_credentials, load_graph_client_id
from canvas_calendar.meetings import ClassMeeting, build_meetings

CX = "https://courses.illinois.edu/cisapp/explorer/schedule/2026/fall"
_CODE = re.compile(r"([a-z]{2,4})_(\d{3})_", re.IGNORECASE)


def _canvas():
    base, tok = load_canvas_credentials()
    return base, {"Authorization": f"Bearer {tok}"}


def collect_meetings() -> list[ClassMeeting]:
    base, h = _canvas()
    courses = httpx.get(
        f"{base}/courses", headers=h,
        params={"per_page": 100, "enrollment_state": "active"}, timeout=60
    ).json()
    names = {c["id"]: c.get("name", "") for c in courses}
    codes: dict[str, tuple[str, str]] = {}
    for c in courses:
        m = _CODE.match(c.get("course_code", "") or "")
        if m:
            codes[c.get("name", "")] = (m.group(1).upper(), m.group(2))

    pairs: list[tuple[str, str]] = []
    for e in httpx.get(
        f"{base}/users/self/enrollments", headers=h,
        params={"per_page": 100, "state[]": "active"}, timeout=60
    ).json():
        if e.get("type") != "StudentEnrollment":
            continue
        sec = httpx.get(f"{base}/sections/{e['course_section_id']}", headers=h, timeout=30).json()
        pairs.append((names.get(e["course_id"], "?"), sec.get("name", "")))

    def fetch(crn: str) -> Section | None:
        for course, sname in pairs:
            if crn in sname and course in codes:
                subj, num = codes[course]
                r = httpx.get(
                    f"{CX}/{subj}/{num}/{crn}.xml",
                    headers={"User-Agent": USER_AGENT}, timeout=45,
                )
                return parse_section(r.text) if r.status_code == 200 else None
        return None

    return build_meetings(pairs, fetch)


def sync_meetings(live: bool = False, calendar_name: str = "UIUC Assignments") -> int:
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

    auth = GraphAuth(client_id=load_graph_client_id())
    adapter = OutlookAdapter(auth=auth)
    cal = adapter.ensure_calendar(calendar_name)
    written = sum(1 for m in meetings if adapter.upsert_recurring(cal, m))
    print(f"\nAPPLIED: {written} series written into {calendar_name!r}")
    return 0
