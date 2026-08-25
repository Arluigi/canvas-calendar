from datetime import datetime

import httpx
import pytest

from canvas_calendar.calendars.base import ForeignEventError
from canvas_calendar.calendars.outlook import UID_PROP, OutlookAdapter
from canvas_calendar.models import Assignment, Source


class FakeAuth:
    def access_token(self):
        return "at-test"


def _adapter(handler):
    return OutlookAdapter(
        auth=FakeAuth(), http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def _a(name="Chapter 1", when="2026-08-25T19:00:00Z", cid=1, source=Source.CANVAS):
    return Assignment(
        canvas_id=cid,
        name=name,
        points=10.0,
        course="MCB 244",
        source=source,
        due_at=datetime.fromisoformat(when),
    )


def test_ensure_calendar_reuses_existing():
    calls = {"post": 0}

    def handler(request):
        if request.method == "POST":
            calls["post"] += 1
        return httpx.Response(200, json={"value": [{"id": "cal-9", "name": "UIUC Assignments"}]})

    assert _adapter(handler).ensure_calendar("UIUC Assignments") == "cal-9"
    assert calls["post"] == 0  # must not create a duplicate calendar


def test_ensure_calendar_creates_when_absent():
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(201, json={"id": "cal-new", "name": "UIUC Assignments"})

    assert _adapter(handler).ensure_calendar("UIUC Assignments") == "cal-new"


def test_timed_event_uses_local_time_and_windows_zone():
    """Graph wants a Windows zone name, not the IANA one. Sending a UTC instant
    instead would drift the event across the Nov 1 DST change."""
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "ev-1"})

    _adapter(handler).upsert("cal-1", "cc-1", _a())
    assert '"timeZone": "Central Standard Time"' in seen["body"]
    assert "2026-08-25T14:00:00" in seen["body"]  # 19:00Z -> 14:00 local
    assert '"isAllDay": false' in seen["body"]


def test_all_day_event_uses_date_only_with_exclusive_end():
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "ev-1"})

    # 04:59:59Z on Sep 1 is 23:59:59 local on Aug 31 -> all-day Aug 31
    _adapter(handler).upsert("cal-1", "cc-2", _a(when="2026-09-01T04:59:59Z", cid=2))
    body = seen["body"]
    assert '"isAllDay": true' in body
    assert "2026-08-31" in body
    assert "2026-09-01" in body  # end is exclusive


def test_uid_is_written_as_an_extended_property():
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "ev-1"})

    _adapter(handler).upsert("cal-1", "cc-1682585", _a())
    assert UID_PROP in seen["body"]
    assert "cc-1682585" in seen["body"]


def test_existing_event_is_patched_not_duplicated():
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "ev-existing"}]})
        return httpx.Response(200, json={"id": "ev-existing"})

    _adapter(handler).upsert("cal-1", "cc-1", _a())
    assert "PATCH" in methods
    assert "POST" not in methods


def test_extracted_event_is_labelled_in_the_subject():
    seen = {}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={"id": "ev-1"})

    a = _a(source=Source.EXTRACTED)
    a.provenance = "module: Week 1 ... August 26th/28th"
    _adapter(handler).upsert("cal-1", "cc-1", a)
    assert "[extracted]" in seen["body"]
    assert "Week 1" in seen["body"]


def test_delete_refuses_foreign_uid():
    def handler(request):
        raise AssertionError("must not reach the network for a foreign uid")

    with pytest.raises(ForeignEventError):
        _adapter(handler).delete("cal-1", "AAMkAD-outlook-native-id")


def test_delete_removes_matching_event():
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "ev-1"}]})
        return httpx.Response(204)

    _adapter(handler).delete("cal-1", "cc-1")
    assert "DELETE" in methods


def test_delete_of_absent_event_is_not_an_error():
    def handler(request):
        return httpx.Response(200, json={"value": []})

    _adapter(handler).delete("cal-1", "cc-1")  # must not raise


def test_401_surfaces_clearly():
    def handler(request):
        return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})

    with pytest.raises(httpx.HTTPStatusError):
        _adapter(handler).ensure_calendar("X")
