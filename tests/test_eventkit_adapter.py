"""Unit tests for the EventKit adapter. No real calendar is touched.

The live round-trip lives in tests/test_eventkit_live.py behind `-m live`.
"""

import pytest

from canvas_calendar.calendars.base import ForeignEventError
from canvas_calendar.calendars.eventkit import UID_SCHEME, EventKitAdapter, ek_weekday


class FakeURL:
    def __init__(self, s):
        self._s = s

    def absoluteString(self):
        return self._s


class FakeEvent:
    def __init__(self, uid=None, ident="ek-1", raw_url=None):
        self._url = FakeURL(raw_url if raw_url else (UID_SCHEME + uid) if uid else None)
        self._ident = ident
        if raw_url is None and uid is None:
            self._url = None

    def URL(self):
        return self._url

    def eventIdentifier(self):
        return self._ident


class FakeAdapter(EventKitAdapter):
    """Bypasses __init__ so no EKEventStore is created and no TCC prompt fires.

    `_index` mirrors the real one: it is handed a flat list of calendar events
    and must derive the uid map itself via _uid_of. Handing it a ready-made
    map would make the filtering tests pass vacuously.
    """

    def __init__(self, events=()):
        self._reminder_timed = 15
        self._reminder_all_day = 1440
        self._store = None
        self._ek = None
        self._index_cache = {}
        self._events = list(events)

    def _index(self, calendar_id):
        out = {}
        for ev in self._events:
            uid = self._uid_of(ev)
            if uid:
                out[uid] = ev
        return out


def test_uid_is_read_back_from_the_event_url():
    a = FakeAdapter([FakeEvent("cc-1652210")])
    assert a.list_uids("cal") == {"cc-1652210"}


def test_event_with_no_url_is_invisible():
    """A user's own event must never appear in list_uids, or prune deletes it."""
    assert FakeAdapter()._uid_of(FakeEvent(None)) is None


def test_uid_of_ignores_a_foreign_url_scheme():
    ev = FakeEvent(raw_url="https://example.com/meeting")
    assert FakeAdapter()._uid_of(ev) is None


def test_uid_of_ignores_our_scheme_with_an_empty_uid():
    ev = FakeEvent(raw_url=UID_SCHEME)
    assert FakeAdapter()._uid_of(ev) is None


def test_foreign_events_never_reach_list_uids():
    a = FakeAdapter([
        FakeEvent("cc-1"),
        FakeEvent(raw_url="https://zoom.us/j/123"),  # a Zoom invite of theirs
        FakeEvent(None),                             # a plain event of theirs
        FakeEvent(raw_url=UID_SCHEME),               # our scheme, empty uid
    ])
    assert a.list_uids("cal") == {"cc-1"}


def test_delete_refuses_a_uid_that_is_not_ours():
    with pytest.raises(ForeignEventError):
        FakeAdapter().delete("cal", "not-ours-123")


def test_delete_refuses_when_the_event_url_disagrees():
    """State said cc-1; the event on the calendar says cc-2. Refuse.

    This is the guard that must not depend on the index being correct.
    """
    a = FakeAdapter([FakeEvent("cc-2")])
    a._index = lambda cal: {"cc-1": a._events[0]}  # a wrong index, on purpose
    with pytest.raises(ForeignEventError, match="does not carry"):
        a.delete("cal", "cc-1")


def test_delete_of_a_missing_event_is_a_no_op():
    FakeAdapter().delete("cal", "cc-404")  # must not raise


def test_upsert_refuses_a_uid_that_is_not_ours():
    with pytest.raises(ForeignEventError):
        FakeAdapter().upsert("cal", "nope", object())


def test_upsert_recurring_refuses_a_foreign_uid():
    class M:
        uid = "not-ours"

    with pytest.raises(ForeignEventError):
        FakeAdapter().upsert_recurring("cal", M())


@pytest.mark.parametrize(
    "python_weekday,expected,name",
    [(0, 2, "Monday"), (1, 3, "Tuesday"), (2, 4, "Wednesday"),
     (3, 5, "Thursday"), (4, 6, "Friday"), (5, 7, "Saturday"), (6, 1, "Sunday")],
)
def test_weekday_mapping_matches_eventkit_numbering(python_weekday, expected, name):
    """EKWeekday is 1-based from Sunday; Python weekday() is 0-based from
    Monday. Conflating them shifts every class by a day, which looks
    plausible on a calendar and is therefore easy to miss."""
    assert ek_weekday(python_weekday) == expected, name
