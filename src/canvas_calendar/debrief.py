"""Morning debrief: one email that answers "what do I need to know today?"

Sources, in the order they earn attention:

1. Today's classes and where they are.
2. What is due today, then this week.
3. Canvas announcements since the last debrief -- the highest-signal source,
   and the one that hid MCB 354's real quiz schedule for two weeks.
4. Canvas inbox messages.
5. Unread Outlook mail.
6. The standing blind-spot list.

Everything degrades: a source that fails is reported as failed, never dropped.
A debrief that silently omits a section is worse than one that says a section
broke, because only the second tells you to go look yourself.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from canvas_calendar.calendars.outlook import GRAPH
from canvas_calendar.timeutil import CHICAGO, parse_canvas_ts

STATE_PATH = Path.home() / ".config" / "canvas-calendar" / "debrief_state.json"

_TAGS = re.compile(r"<[^>]+>")


def strip_html(raw: str, limit: int = 240) -> str:
    """Announcement and mail bodies are HTML; the email wants readable text."""
    text = html.unescape(_TAGS.sub(" ", raw or ""))
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def load_last_run() -> datetime:
    """When the previous debrief ran. Announcements are shown since then, so
    nothing is either missed or repeated forever."""
    if STATE_PATH.exists():
        try:
            return datetime.fromisoformat(json.loads(STATE_PATH.read_text())["last_run"])
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            pass
    return datetime.now(CHICAGO) - timedelta(days=1)


def save_last_run(when: datetime) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps({"last_run": when.isoformat()}))


# --- Canvas sources -------------------------------------------------------


def canvas_announcements(client, course_ids: list[int], since: datetime) -> list[dict]:
    codes = [f"course_{c}" for c in course_ids]
    raw = client._get_all(
        "/announcements",
        **{
            "context_codes[]": codes,
            "start_date": since.date().isoformat(),
            "end_date": (datetime.now(CHICAGO) + timedelta(days=1)).date().isoformat(),
        },
    )
    out = []
    for a in raw:
        posted = a.get("posted_at")
        if posted and parse_canvas_ts(posted) < since:
            continue
        out.append(
            {
                "title": a.get("title", ""),
                "course": a.get("context_code", ""),
                "posted": posted,
                "body": strip_html(a.get("message", "")),
                "url": a.get("html_url", ""),
            }
        )
    return out


def canvas_conversations(client, limit: int = 8) -> list[dict]:
    raw = client._get_all("/conversations", scope="unread")
    return [
        {
            "subject": c.get("subject") or "(no subject)",
            "from": (c.get("participants") or [{}])[0].get("name", "?"),
            "preview": strip_html(c.get("last_message", ""), 160),
        }
        for c in raw[:limit]
    ]


# --- Outlook sources ------------------------------------------------------


def outlook_unread(auth, hours: int = 48, limit: int = 40) -> list[dict]:
    """Unread inbox mail from the last `hours`."""
    since = (datetime.now(CHICAGO) - timedelta(hours=hours)).astimezone().isoformat()
    r = httpx.get(
        f"{GRAPH}/me/mailFolders/inbox/messages",
        headers={"Authorization": f"Bearer {auth.access_token()}"},
        params={
            "$filter": f"isRead eq false and receivedDateTime ge {since}",
            "$select": "subject,from,toRecipients,receivedDateTime,bodyPreview,importance",
            "$orderby": "receivedDateTime desc",
            "$top": str(limit),
        },
        timeout=60,
    )
    r.raise_for_status()
    return [
        {
            "subject": m.get("subject") or "(no subject)",
            "from": ((m.get("from") or {}).get("emailAddress") or {}).get("name", "?"),
            "from_address": ((m.get("from") or {}).get("emailAddress") or {}).get("address", ""),
            "to": [
                (t.get("emailAddress") or {}).get("address", "")
                for t in (m.get("toRecipients") or [])
            ],
            "importance": m.get("importance", "normal"),
            "received": m.get("receivedDateTime", ""),
            "preview": " ".join((m.get("bodyPreview") or "").split())[:160],
        }
        for m in r.json().get("value", [])
    ]


def _tidy_location(raw: str) -> str:
    """Meeting locations are often a full join URL, which swamps the row.
    Name the platform instead; the calendar entry still holds the link."""
    low = (raw or "").lower()
    for host, label in (("zoom.us", "Zoom"), ("teams.microsoft", "Teams"),
                        ("meet.google", "Google Meet"), ("webex", "Webex")):
        if host in low:
            return label
    raw = " ".join((raw or "").split())
    return raw[:44] + ("…" if len(raw) > 44 else "")


def _calendar_view(auth, path: str, start: datetime, end: datetime) -> list[dict]:
    r = httpx.get(
        f"{GRAPH}{path}",
        headers={
            "Authorization": f"Bearer {auth.access_token()}",
            "Prefer": 'outlook.timezone="Central Standard Time"',
        },
        params={
            "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
            "$select": "subject,start,isAllDay,location,organizer,isCancelled",
            "$orderby": "start/dateTime",
            "$top": "80",
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json().get("value", [])


def todays_events(auth, course_calendar_id: str | None = None) -> list[dict]:
    """Everything on today, from the default calendar AND the course calendar.

    The default calendar is where real life lands -- meetings, appointments,
    anything invited. A debrief that showed only synced coursework would tell
    you about a 2pm lecture and stay silent about the 2pm meeting that
    conflicts with it, which is worse than useless.
    """
    now = datetime.now(CHICAGO)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    seen: set[str] = set()
    out: list[dict] = []
    sources = [("personal", "/me/calendarView")]
    if course_calendar_id:
        sources.append(("course", f"/me/calendars/{course_calendar_id}/calendarView"))

    for kind, path in sources:
        for e in _calendar_view(auth, path, start, end):
            if e.get("isCancelled"):
                continue
            key = f"{e.get('subject')}|{e['start']['dateTime']}"
            if key in seen:  # the default calendar can echo the course one
                continue
            seen.add(key)
            out.append(
                {
                    "subject": e.get("subject", ""),
                    "time": "all day" if e.get("isAllDay") else e["start"]["dateTime"][11:16],
                    "all_day": bool(e.get("isAllDay")),
                    "location": _tidy_location((e.get("location") or {}).get("displayName", "")),
                    "organizer": ((e.get("organizer") or {}).get("emailAddress") or {}).get(
                        "name", ""
                    ),
                    "kind": kind,
                }
            )
    out.sort(key=lambda x: (x["all_day"], x["time"]))
    return out


def send_mail(auth, to: str, subject: str, html_body: str) -> None:
    r = httpx.post(
        f"{GRAPH}/me/sendMail",
        headers={
            "Authorization": f"Bearer {auth.access_token()}",
            "Content-Type": "application/json",
        },
        content=json.dumps(
            {
                "message": {
                    "subject": subject,
                    "body": {"contentType": "HTML", "content": html_body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
                "saveToSentItems": False,
            }
        ),
        timeout=60,
    )
    r.raise_for_status()
