"""Touches a real calendar. Run explicitly:  uv run pytest -m live

Creates its own scratch calendar and removes it afterwards, so it never
writes into 'UIUC Assignments' or any calendar the user cares about.
"""

from datetime import datetime, timedelta

import pytest

from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import CHICAGO

pytestmark = pytest.mark.live

SCRATCH = "canvas-calendar test (safe to delete)"


@pytest.fixture
def adapter():
    from canvas_calendar.calendars.eventkit import EventKitAdapter

    a = EventKitAdapter()
    cal_id = a.ensure_calendar(SCRATCH)
    assert cal_id, "could not create or find the scratch calendar"
    yield a, cal_id
    cal = a._store.calendarWithIdentifier_(cal_id)
    if cal is not None:
        a._store.removeCalendar_commit_error_(cal, True, None)


def _item(name="Spike Homework", days=3, cid=999001):
    return Assignment(
        canvas_id=cid, name=name, points=10.0,
        due_at=datetime.now(CHICAGO) + timedelta(days=days),
        course="MCB 999", source=Source.CANVAS,
    )


def test_roundtrip_create_read_delete(adapter):
    a, cal_id = adapter
    item = _item()

    a.upsert(cal_id, item.uid, item)
    a._index_cache.clear()
    assert item.uid in a.list_uids(cal_id), "uid did not survive the round-trip"

    a.delete(cal_id, item.uid)
    a._index_cache.clear()
    assert item.uid not in a.list_uids(cal_id)


def test_upsert_is_idempotent(adapter):
    """A second upsert must update, not create a duplicate."""
    a, cal_id = adapter
    item = _item(name="First title")
    a.upsert(cal_id, item.uid, item)

    item.name = "Second title"
    a._index_cache.clear()
    a.upsert(cal_id, item.uid, item)

    a._index_cache.clear()
    assert list(a.list_uids(cal_id)).count(item.uid) == 1
    ev = a._index(cal_id)[item.uid]
    assert "Second title" in ev.title()
    a.delete(cal_id, item.uid)


def test_foreign_events_are_never_listed(adapter):
    """An event the user made must be invisible to us, or prune deletes it."""
    import EventKit as EK
    from Foundation import NSDate

    a, cal_id = adapter
    ev = EK.EKEvent.eventWithEventStore_(a._store)
    ev.setTitle_("the user's own event")
    ev.setCalendar_(a._store.calendarWithIdentifier_(cal_id))
    ev.setStartDate_(NSDate.dateWithTimeIntervalSinceNow_(86400))
    ev.setEndDate_(NSDate.dateWithTimeIntervalSinceNow_(90000))
    ok, err = a._store.saveEvent_span_commit_error_(ev, EK.EKSpanThisEvent, True, None)
    assert ok, err

    a._index_cache.clear()
    assert a.list_uids(cal_id) == set(), "a foreign event leaked into list_uids"


def test_all_day_item_is_marked_all_day(adapter):
    """An end-of-day due date must become an all-day banner, not a 30-min block."""
    a, cal_id = adapter
    item = Assignment(
        canvas_id=999002, name="All day thing", points=0.0,
        due_at=(datetime.now(CHICAGO) + timedelta(days=4)).replace(hour=23, minute=59),
        course="MCB 999", source=Source.EXTRACTED,
    )
    a.upsert(cal_id, item.uid, item)
    a._index_cache.clear()
    assert a._index(cal_id)[item.uid].isAllDay()
    a.delete(cal_id, item.uid)
