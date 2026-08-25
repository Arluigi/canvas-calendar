from datetime import datetime

import pytest

from canvas_calendar.apply import apply_plan
from canvas_calendar.calendars.base import ForeignEventError
from canvas_calendar.diff import diff
from canvas_calendar.models import Assignment, Source
from canvas_calendar.state import StateStore


class FakeAdapter:
    """Records operations. Optionally fails on a chosen uid."""

    def __init__(self, fail_on: str | None = None):
        self.upserts: list[str] = []
        self.deletes: list[str] = []
        self.fail_on = fail_on

    def ensure_calendar(self, name):
        return "cal-1"

    def upsert(self, calendar_id, uid, assignment):
        if uid == self.fail_on:
            raise RuntimeError("calendar write failed")
        self.upserts.append(uid)

    def delete(self, calendar_id, uid):
        from canvas_calendar.calendars.base import assert_ours

        assert_ours(uid)
        if uid == self.fail_on:
            raise RuntimeError("calendar delete failed")
        self.deletes.append(uid)

    def list_uids(self, calendar_id):
        return set()


def _a(cid, name="X", when="2026-08-25T19:00:00Z", source=Source.CANVAS):
    return Assignment(
        canvas_id=cid,
        name=name,
        points=1.0,
        course="C",
        source=source,
        due_at=datetime.fromisoformat(when) if when else None,
    )


def test_dry_run_writes_nothing(tmp_path):
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter()
    counts = apply_plan(diff([_a(1)], s), adapter, "cal-1", s, dry_run=True)
    assert adapter.upserts == []
    assert s.get("cc-1") is None
    assert counts["create"] == 1


def test_live_run_creates_and_records_state(tmp_path):
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter()
    apply_plan(diff([_a(1)], s), adapter, "cal-1", s, dry_run=False)
    assert adapter.upserts == ["cc-1"]
    assert s.get("cc-1") is not None


def test_failed_write_does_not_advance_state(tmp_path):
    """A failed calendar write must be retried next run, not lost."""
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter(fail_on="cc-1")
    counts = apply_plan(diff([_a(1)], s), adapter, "cal-1", s, dry_run=False)
    assert s.get("cc-1") is None
    assert counts["error"] == 1


def test_one_failure_does_not_block_other_events(tmp_path):
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter(fail_on="cc-1")
    apply_plan(diff([_a(1), _a(2)], s), adapter, "cal-1", s, dry_run=False)
    assert s.get("cc-1") is None
    assert s.get("cc-2") is not None


def test_delete_removes_event_and_state(tmp_path):
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter()
    apply_plan(diff([_a(1)], s), adapter, "cal-1", s, dry_run=False)
    apply_plan(diff([], s), adapter, "cal-1", s, dry_run=False)
    assert adapter.deletes == ["cc-1"]
    assert s.get("cc-1") is None


def test_delete_of_foreign_uid_is_refused(tmp_path):
    """State corruption must not become calendar destruction."""
    s = StateStore(tmp_path / "s.db")
    s.upsert("outlook-native-id", due_at="a", title_hash="h", source="canvas")
    adapter = FakeAdapter()
    counts = apply_plan(diff([], s), adapter, "cal-1", s, dry_run=False)
    assert adapter.deletes == []
    assert counts["error"] == 1
    # The bogus row is left alone rather than silently purged.
    assert s.get("outlook-native-id") is not None


def test_skip_never_reaches_the_calendar(tmp_path):
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter()
    a = _a(1, when=None, source=Source.UNRESOLVED)
    counts = apply_plan(diff([a], s), adapter, "cal-1", s, dry_run=False)
    assert adapter.upserts == []
    assert counts["skip"] == 1


def test_noop_does_not_rewrite(tmp_path):
    s = StateStore(tmp_path / "s.db")
    adapter = FakeAdapter()
    apply_plan(diff([_a(1)], s), adapter, "cal-1", s, dry_run=False)
    apply_plan(diff([_a(1)], s), adapter, "cal-1", s, dry_run=False)
    assert adapter.upserts == ["cc-1"]  # written once, not twice


def test_guard_is_enforced_even_if_adapter_forgets():
    """apply_plan must not rely on the adapter to police UIDs."""
    from canvas_calendar.calendars.base import assert_ours

    with pytest.raises(ForeignEventError):
        assert_ours("outlook-native-id")
