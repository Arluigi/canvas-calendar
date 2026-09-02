"""macOS EventKit calendar adapter.

Writes into Calendar.app, which already holds whichever account the user added
in System Settings -- iCloud, Google, or Exchange. One adapter therefore serves
all three backends without any OAuth of its own, which is the entire reason
this exists: distributing a Google Calendar integration would otherwise mean an
unverified OAuth app whose refresh tokens expire every seven days.

Facts established by spike on 2026-08-31 (macOS 26.5.2), each of which shapes
this file:

- `EKEvent.URL` survives a save/refetch on CalDAV/iCloud, Exchange, AND Google,
  so it is where our UID lives. It must be a valid URI -- NSURL rejects a bare
  "cc-..." string -- hence the x-canvas-calendar scheme.
- A completion block returning anything other than None aborts the process with
  "did not return None, expecting void return value".
- EKSource and EKCalendar handles do not survive EKEventStore.reset(). Never
  cache them across one.
- Google's CalDAV refuses calendar creation (EKErrorDomain Code=17). iCloud and
  Exchange allow it. So creation failure is an expected path, not a bug, and
  the message has to tell the user what to do about it.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from canvas_calendar.calendars.base import ForeignEventError, assert_ours
from canvas_calendar.models import Assignment
from canvas_calendar.timeutil import CHICAGO, is_end_of_day, to_local

UID_SCHEME = "x-canvas-calendar:"
_ACCESS_TIMEOUT = 60

# EventKit predicates require explicit bounds, and an event outside them is
# invisible -- which reads as "absent" and would be re-created as a duplicate.
# Generous either side of a term.
_WINDOW_DAYS = 400


class CalendarAccessDenied(RuntimeError):
    """macOS refused Calendar access, or nobody answered the prompt."""


class CalendarNotWritable(RuntimeError):
    """The named calendar refuses writes, or cannot be created here."""


def ek_weekday(python_weekday: int) -> int:
    """Python weekday (Mon=0) -> EKWeekday (Sunday=1).

    Two conventions for the same concept. Conflating them shifts every class
    meeting by one day, which looks entirely plausible on a calendar and is
    therefore easy to miss.
    """
    return (python_weekday + 1) % 7 + 1


def _request_access(store) -> None:
    """Block until macOS answers.

    The first call from a given process context shows a prompt; later ones
    return immediately from the TCC database. A LaunchAgent has its own TCC
    identity, so its first run would block here on a dialog nobody sees --
    which is why `setup` grants access interactively before installing it.
    """
    done = threading.Event()
    result: dict = {}

    def completion(granted, error):
        # MUST return None. A block returning a value aborts the process.
        result["granted"] = bool(granted)
        result["error"] = error
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(completion)
    else:  # macOS < 14
        import EventKit as EK

        store.requestAccessToEntityType_completion_(EK.EKEntityTypeEvent, completion)

    if not done.wait(_ACCESS_TIMEOUT):
        raise CalendarAccessDenied(
            "timed out waiting for Calendar permission. If this ran from a "
            "LaunchAgent, grant access once interactively: canvas-calendar setup"
        )
    if not result.get("granted"):
        raise CalendarAccessDenied(
            f"Calendar access denied ({result.get('error')}). Grant it in "
            "System Settings > Privacy & Security > Calendars."
        )


def _ns(dt: datetime):
    """Aware datetime -> NSDate."""
    from Foundation import NSDate

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHICAGO)
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())


class EventKitAdapter:
    def __init__(self, reminder_timed: int = 15, reminder_all_day: int = 1440) -> None:
        import EventKit as EK

        self._ek = EK
        self._reminder_timed = reminder_timed
        self._reminder_all_day = reminder_all_day
        self._store = EK.EKEventStore.alloc().init()
        _request_access(self._store)
        self._index_cache: dict[str, dict] = {}

    # -- calendars ---------------------------------------------------------

    def ensure_calendar(self, name: str) -> str:
        """Return the identifier of a writable calendar titled `name`."""
        for cal in self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or []:
            if cal.title() == name:
                if not cal.allowsContentModifications():
                    raise CalendarNotWritable(
                        f"calendar {name!r} is read-only (subscribed or shared). "
                        "Pick a different one: canvas-calendar setup"
                    )
                return cal.calendarIdentifier()
        return self._create_calendar(name)

    def _create_calendar(self, name: str) -> str:
        source = self._writable_source()
        cal = self._ek.EKCalendar.calendarForEntityType_eventStore_(
            self._ek.EKEntityTypeEvent, self._store
        )
        cal.setTitle_(name)
        cal.setSource_(source)
        ok, err = self._store.saveCalendar_commit_error_(cal, True, None)
        if not ok:
            # Expected on Google, which refuses calendar creation over CalDAV.
            raise CalendarNotWritable(
                f"could not create calendar {name!r} on {source.title()!r}: {err}\n"
                f"Some accounts (Google) do not allow it. Create a calendar named "
                f"{name!r} in that account, then run this again."
            )
        return cal.calendarIdentifier()

    def _writable_source(self):
        for src in self._store.sources():
            cals = src.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or []
            if any(c.allowsContentModifications() for c in cals):
                return src
        raise CalendarNotWritable(
            "no writable calendar account found. Add your calendar account in "
            "System Settings > Internet Accounts first."
        )

    def _calendar(self, calendar_id: str):
        cal = self._store.calendarWithIdentifier_(calendar_id)
        if cal is None:
            raise CalendarNotWritable(f"calendar {calendar_id!r} no longer exists")
        return cal

    # -- uid indexing ------------------------------------------------------

    def _uid_of(self, event) -> str | None:
        """Our UID, or None for anything we did not create.

        Deliberately strict. Anything that is not exactly our scheme followed
        by a non-empty uid is somebody else's event, and must stay invisible:
        an event that leaked into list_uids would be deleted by prune.
        """
        url = event.URL()
        if url is None:
            return None
        s = url.absoluteString() or ""
        if not s.startswith(UID_SCHEME):
            return None
        return s[len(UID_SCHEME) :] or None

    def _index(self, calendar_id: str) -> dict:
        """uid -> EKEvent for the whole term window, built once per run.

        One predicate query rather than a lookup per event, and it removes any
        need for a state.db schema change to remember backend ids.
        """
        if calendar_id in self._index_cache:
            return self._index_cache[calendar_id]

        cal = self._calendar(calendar_id)
        now = datetime.now(CHICAGO)
        pred = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            _ns(now - timedelta(days=_WINDOW_DAYS)),
            _ns(now + timedelta(days=_WINDOW_DAYS)),
            [cal],
        )
        index: dict = {}
        for ev in self._store.eventsMatchingPredicate_(pred) or []:
            uid = self._uid_of(ev)
            if uid:
                index[uid] = ev
        self._index_cache[calendar_id] = index
        return index

    def list_uids(self, calendar_id: str) -> set[str]:
        return set(self._index(calendar_id))

    # -- writes ------------------------------------------------------------

    def upsert(self, calendar_id: str, uid: str, assignment: Assignment) -> None:
        from Foundation import NSURL

        assert_ours(uid)
        existing = self._index(calendar_id).get(uid)
        ev = existing or self._ek.EKEvent.eventWithEventStore_(self._store)
        ev.setCalendar_(self._calendar(calendar_id))
        ev.setURL_(NSURL.URLWithString_(UID_SCHEME + uid))
        self._apply_fields(ev, assignment)

        ok, err = self._store.saveEvent_span_commit_error_(
            ev, self._ek.EKSpanThisEvent, True, None
        )
        if not ok:
            raise RuntimeError(f"saving {uid}: {err}")
        self._index_cache.setdefault(calendar_id, {})[uid] = ev

    def _apply_fields(self, ev, a: Assignment) -> None:
        from Foundation import NSTimeZone

        subject = f"{a.course}: {a.name}"
        ev.setTitle_(subject[:250])
        body = [f"Course: {a.course}", f"Points: {a.points:g}"]
        if a.provenance:
            body.append(f"Date derived from {a.provenance}")
        if a.display_reason:
            body.append(a.display_reason)
        body.append("Synced by canvas-calendar. Edits here will be overwritten.")
        ev.setNotes_("\n".join(body))
        ev.setTimeZone_(NSTimeZone.timeZoneWithName_("America/Chicago"))

        for alarm in list(ev.alarms() or []):
            ev.removeAlarm_(alarm)

        local = to_local(a.due_at)
        if is_end_of_day(a.due_at):
            day = local.replace(hour=0, minute=0, second=0, microsecond=0)
            ev.setAllDay_(True)
            ev.setStartDate_(_ns(day))
            ev.setEndDate_(_ns(day + timedelta(days=1)))
            offset = -self._reminder_all_day * 60
        else:
            # display_start is set when the real due time would sit on top of a
            # class meeting; due_at itself is untouched.
            start = a.display_start or local
            end = a.display_end or (local + timedelta(minutes=30))
            ev.setAllDay_(False)
            ev.setStartDate_(_ns(start))
            ev.setEndDate_(_ns(end))
            offset = -self._reminder_timed * 60
        ev.addAlarm_(self._ek.EKAlarm.alarmWithRelativeOffset_(offset))

    def delete(self, calendar_id: str, uid: str) -> None:
        assert_ours(uid)
        ev = self._index(calendar_id).get(uid)
        if ev is None:
            return  # already gone
        # Independent of any index or state row: confirm the event itself still
        # claims this uid before removing it. The lookup may be wrong; the
        # guard may not.
        if self._uid_of(ev) != uid:
            raise ForeignEventError(
                f"event for {uid} does not carry our uid; refusing to delete"
            )
        ok, err = self._store.removeEvent_span_error_(ev, self._ek.EKSpanThisEvent, None)
        if not ok:
            raise RuntimeError(f"deleting {uid}: {err}")
        self._index_cache.get(calendar_id, {}).pop(uid, None)

    # -- recurring class meetings -----------------------------------------

    def upsert_recurring(self, calendar_id: str, meeting) -> str | None:
        from Foundation import NSURL, NSTimeZone

        from canvas_calendar.meetings import excluded_dates, first_occurrence, parse_clock

        assert_ours(meeting.uid)
        weekdays = meeting.meeting.weekdays()
        start_clock = parse_clock(meeting.meeting.start)
        end_clock = parse_clock(meeting.meeting.end)
        if not weekdays or start_clock is None:
            return None
        begins = first_occurrence(weekdays, meeting.start_date)
        if begins is None:
            return None

        # Replace wholesale rather than patch: editing a live series in place
        # is unreliable across backends, exactly as it is on Graph.
        existing = self._index(calendar_id).get(meeting.uid)
        if existing is not None:
            self._store.removeEvent_span_error_(
                existing, self._ek.EKSpanFutureEvents, None
            )
            self._index_cache.get(calendar_id, {}).pop(meeting.uid, None)

        ev = self._ek.EKEvent.eventWithEventStore_(self._store)
        ev.setCalendar_(self._calendar(calendar_id))
        ev.setURL_(NSURL.URLWithString_(UID_SCHEME + meeting.uid))
        ev.setTitle_(meeting.title)
        ev.setLocation_(meeting.location)
        ev.setNotes_(
            f"{meeting.section}\nInstructor: {meeting.meeting.instructor}\n"
            "Synced by canvas-calendar. Edits here will be overwritten."
        )
        ev.setTimeZone_(NSTimeZone.timeZoneWithName_("America/Chicago"))
        ev.setAllDay_(False)
        ev.setStartDate_(_ns(datetime.combine(begins, start_clock, tzinfo=CHICAGO)))
        ev.setEndDate_(
            _ns(datetime.combine(begins, end_clock or start_clock, tzinfo=CHICAGO))
        )
        ev.addAlarm_(self._ek.EKAlarm.alarmWithRelativeOffset_(-15 * 60))
        ev.setRecurrenceRules_([self._weekly_rule(weekdays, meeting, start_clock)])

        ok, err = self._store.saveEvent_span_commit_error_(
            ev, self._ek.EKSpanFutureEvents, True, None
        )
        if not ok:
            raise RuntimeError(f"saving series {meeting.uid}: {err}")

        self._cancel_occurrences(calendar_id, meeting.uid, excluded_dates(weekdays))
        return ev.eventIdentifier()

    def _weekly_rule(self, weekdays, meeting, start_clock):
        days = [
            self._ek.EKRecurrenceDayOfWeek.dayOfWeek_(ek_weekday(w)) for w in weekdays
        ]
        end = self._ek.EKRecurrenceEnd.recurrenceEndWithEndDate_(
            _ns(datetime.combine(meeting.end_date, start_clock, tzinfo=CHICAGO))
        )
        return self._ek.EKRecurrenceRule.alloc().initRecurrenceWithFrequency_interval_daysOfTheWeek_daysOfTheMonth_monthsOfTheYear_weeksOfTheYear_daysOfTheYear_setPositions_end_(
            self._ek.EKRecurrenceFrequencyWeekly,
            1,
            days,
            None,
            None,
            None,
            None,
            None,
            end,
        )

    def _cancel_occurrences(self, calendar_id: str, uid: str, days: list) -> int:
        """Delete individual occurrences on non-instruction days.

        EventKit has no EXDATE at creation time, the same limitation Graph has.
        Without this a lecture lands on Thanksgiving.
        """
        if not days:
            return 0
        removed = 0
        cal = self._calendar(calendar_id)
        for day in days:
            start = datetime.combine(day, datetime.min.time(), tzinfo=CHICAGO)
            pred = self._store.predicateForEventsWithStartDate_endDate_calendars_(
                _ns(start), _ns(start + timedelta(days=1)), [cal]
            )
            for occ in self._store.eventsMatchingPredicate_(pred) or []:
                if self._uid_of(occ) != uid:
                    continue
                ok, _ = self._store.removeEvent_span_error_(
                    occ, self._ek.EKSpanThisEvent, None
                )
                removed += int(bool(ok))
        return removed
