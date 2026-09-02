"""Microsoft Graph calendar adapter.

Graph specifics that matter here:

- There is no writable iCalUID on create, so our UID lives in a single-value
  extended property and events are located by filtering on it.
- Timed events take a local wall time plus a Windows timezone name. Sending a
  UTC instant would silently drift every event across the Nov 1 DST change.
- All-day events take date-only bounds with an exclusive end.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx

from canvas_calendar.calendars.base import assert_ours
from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import is_end_of_day, to_local

GRAPH = "https://graph.microsoft.com/v1.0"

# Graph speaks Windows timezone names, not IANA ones.
WINDOWS_TZ = "Central Standard Time"

# Stable property id for our UID. The GUID namespaces it so nothing else
# collides; changing it orphans every event already written.
UID_PROP = "String {b4a1e7c2-0d3f-4a58-9c6e-5f2d8a1b3c7e} Name canvasCalendarUid"


class OutlookAdapter:
    def __init__(
        self,
        auth,
        http: httpx.Client | None = None,
        reminder_timed: int = 15,
        reminder_all_day: int = 1440,
    ) -> None:
        self._auth = auth
        self._http = http or httpx.Client(timeout=30)
        # Graph exposes a single reminderMinutesBeforeStart per event -- there
        # is no array, so one alert per event is the hard ceiling. Timed
        # deadlines get a short nudge; all-day banners get a day's warning,
        # since 15 minutes before midnight is not a useful prompt.
        self._reminder_timed = reminder_timed
        self._reminder_all_day = reminder_all_day

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth.access_token()}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kw) -> httpx.Response:
        r = self._http.request(method, f"{GRAPH}{path}", headers=self._headers(), **kw)
        r.raise_for_status()
        return r

    # -- calendars ---------------------------------------------------------

    def ensure_calendar(self, name: str) -> str:
        existing = self._request("GET", "/me/calendars").json().get("value", [])
        for cal in existing:
            if cal.get("name") == name:
                return cal["id"]
        return self._request("POST", "/me/calendars", json={"name": name}).json()["id"]

    # -- events ------------------------------------------------------------

    def _find_event_id(self, calendar_id: str, uid: str) -> str | None:
        flt = (
            f"singleValueExtendedProperties/any(ep: ep/id eq '{UID_PROP}' "
            f"and ep/value eq '{uid}')"
        )
        r = self._request(
            "GET",
            f"/me/calendars/{calendar_id}/events",
            params={"$filter": flt, "$select": "id", "$top": "1"},
        )
        values = r.json().get("value", [])
        return values[0]["id"] if values else None

    def _payload(self, uid: str, a: Assignment) -> dict:
        local = to_local(a.due_at)
        subject = f"{a.course}: {a.name}"
        if a.source is Source.EXTRACTED:
            subject += " [extracted]"

        body_lines = [f"Course: {a.course}", f"Points: {a.points:g}"]
        if a.provenance:
            body_lines.append(f"Date derived from {a.provenance}")
        if a.display_reason:
            body_lines.append(a.display_reason)
        body_lines.append("Synced by canvas-calendar. Edits here will be overwritten.")

        payload: dict = {
            "subject": subject[:250],
            "body": {"contentType": "text", "content": "\n".join(body_lines)},
            "singleValueExtendedProperties": [{"id": UID_PROP, "value": uid}],
            "isReminderOn": True,
        }

        if is_end_of_day(a.due_at):
            day = local.date()
            payload.update(
                {
                    "isAllDay": True,
                    "reminderMinutesBeforeStart": self._reminder_all_day,
                    "start": {"dateTime": f"{day}T00:00:00", "timeZone": WINDOWS_TZ},
                    "end": {
                        "dateTime": f"{day + timedelta(days=1)}T00:00:00",
                        "timeZone": WINDOWS_TZ,
                    },
                }
            )
        else:
            # display_start is set when the real due time would sit on top of a
            # class meeting; due_at itself is untouched.
            start = a.display_start or local
            end = a.display_end or (local + timedelta(minutes=30))
            payload.update(
                {
                    "isAllDay": False,
                    "reminderMinutesBeforeStart": self._reminder_timed,
                    "start": {
                        "dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": WINDOWS_TZ,
                    },
                    "end": {
                        "dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timeZone": WINDOWS_TZ,
                    },
                }
            )
        return payload

    def upsert(self, calendar_id: str, uid: str, assignment: Assignment) -> None:
        assert_ours(uid)
        payload = self._payload(uid, assignment)
        existing = self._find_event_id(calendar_id, uid)
        if existing:
            self._request(
                "PATCH",
                f"/me/calendars/{calendar_id}/events/{existing}",
                content=json.dumps(payload),
            )
        else:
            self._request(
                "POST",
                f"/me/calendars/{calendar_id}/events",
                content=json.dumps(payload),
            )

    # -- recurring class meetings -----------------------------------------

    def upsert_recurring(self, calendar_id: str, meeting) -> str | None:
        """Create or refresh a weekly class-meeting series.

        Graph has no EXDATE on create: holiday occurrences must be deleted
        individually after the series exists. Doing it any other way puts a
        lecture on Thanksgiving.
        """
        from canvas_calendar.meetings import (
            excluded_dates,
            first_occurrence,
            graph_days,
            parse_clock,
        )

        assert_ours(meeting.uid)
        weekdays = meeting.meeting.weekdays()
        start_clock = parse_clock(meeting.meeting.start)
        end_clock = parse_clock(meeting.meeting.end)
        if not weekdays or start_clock is None:
            return None

        begins = first_occurrence(weekdays, meeting.start_date)
        if begins is None:
            return None

        payload = {
            "subject": meeting.title,
            "location": {"displayName": meeting.location},
            "body": {
                "contentType": "text",
                "content": (
                    f"{meeting.section}\n"
                    f"Instructor: {meeting.meeting.instructor}\n"
                    "Synced by canvas-calendar. Edits here will be overwritten."
                ),
            },
            "isAllDay": False,
            "isReminderOn": True,
            "reminderMinutesBeforeStart": 15,
            "showAs": "busy",
            "singleValueExtendedProperties": [{"id": UID_PROP, "value": meeting.uid}],
            "start": {
                "dateTime": f"{begins}T{start_clock.isoformat()}",
                "timeZone": WINDOWS_TZ,
            },
            "end": {
                "dateTime": f"{begins}T{(end_clock or start_clock).isoformat()}",
                "timeZone": WINDOWS_TZ,
            },
            "recurrence": {
                "pattern": {
                    "type": "weekly",
                    "interval": 1,
                    "daysOfWeek": graph_days(weekdays),
                },
                "range": {
                    "type": "endDate",
                    "startDate": str(begins),
                    "endDate": str(meeting.end_date),
                    "recurrenceTimeZone": WINDOWS_TZ,
                },
            },
        }

        existing = self._find_event_id(calendar_id, meeting.uid)
        if existing:
            self._request(
                "DELETE", f"/me/calendars/{calendar_id}/events/{existing}"
            )  # replace wholesale; patching a series is unreliable
        event_id = self._request(
            "POST", f"/me/calendars/{calendar_id}/events", content=json.dumps(payload)
        ).json()["id"]

        self._cancel_occurrences(event_id, excluded_dates(weekdays))
        return event_id

    def _cancel_occurrences(self, event_id: str, days: list) -> int:
        """Delete individual occurrences on non-instruction days."""
        cancelled = 0
        for day in days:
            r = self._http.get(
                f"{GRAPH}/me/events/{event_id}/instances",
                headers=self._headers(),
                params={
                    "startDateTime": f"{day}T00:00:00",
                    "endDateTime": f"{day}T23:59:59",
                    "$select": "id",
                },
            )
            if r.status_code != 200:
                continue
            for inst in r.json().get("value", []):
                d = self._http.delete(
                    f"{GRAPH}/me/events/{inst['id']}", headers=self._headers()
                )
                if d.status_code in (200, 204):
                    cancelled += 1
        return cancelled

    def delete(self, calendar_id: str, uid: str) -> None:
        # Guard before any network call: never look up, let alone remove, an
        # event that is not ours.
        assert_ours(uid)
        existing = self._find_event_id(calendar_id, uid)
        if existing is None:
            return  # already gone; nothing to do
        self._request("DELETE", f"/me/calendars/{calendar_id}/events/{existing}")

    def list_uids(self, calendar_id: str) -> set[str]:
        r = self._request(
            "GET",
            f"/me/calendars/{calendar_id}/events",
            params={
                "$select": "id",
                "$expand": f"singleValueExtendedProperties($filter=id eq '{UID_PROP}')",
                "$top": "999",
            },
        )
        uids: set[str] = set()
        for ev in r.json().get("value", []):
            for prop in ev.get("singleValueExtendedProperties", []):
                if prop.get("id") == UID_PROP and prop.get("value"):
                    uids.add(prop["value"])
        return uids
