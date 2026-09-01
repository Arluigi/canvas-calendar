from datetime import datetime

from canvas_calendar.models import Assignment, Source
from canvas_calendar.overrides import apply_overrides
from canvas_calendar.timeutil import CHICAGO


def _a(cid, name="Ch10 Adaptive Quiz", when="2026-09-29T02:00:00Z"):
    return Assignment(
        canvas_id=cid, name=name, points=2.0, course="MCB 354",
        due_at=datetime.fromisoformat(when),
    )


def test_date_override_corrects_an_existing_assignment():
    """Canvas says Sep 28 9pm; the instructor's posted schedule says 9am."""
    items = [_a(1652210)]
    log: list[str] = []
    apply_overrides(items, {"date_overrides": {"1652210": {"due_local": "2026-09-28T09:00"}}}, log)
    assert items[0].due_at == datetime(2026, 9, 28, 9, 0, tzinfo=CHICAGO)
    assert "Ch10" in log[0]


def test_override_is_local_wall_time_not_utc():
    """A posted schedule says '9 am', which is a wall time, not an instant."""
    items = [_a(1)]
    apply_overrides(items, {"date_overrides": {"1": {"due_local": "2026-11-02T09:00"}}})
    assert items[0].due_at.hour == 9
    assert items[0].due_at.tzinfo is CHICAGO


def test_stale_override_is_reported_not_silently_dropped():
    log: list[str] = []
    apply_overrides([_a(1)], {"date_overrides": {"9999": {"due_local": "2026-01-01T09:00"}}}, log)
    assert "no matching assignment" in log[0]


def test_addition_gets_manual_namespace():
    """Ch3 and Ch5 have no Canvas assignment; their UIDs must not be able to
    collide with a Canvas assignment id."""
    items: list[Assignment] = []
    apply_overrides(items, {"additions": [{
        "id": "mcb354-ch3", "course": "MCB 354", "name": "Ch3 Adaptive Quiz",
        "due_local": "2026-08-31T09:00", "points": 2.0}]})
    assert items[0].uid == "cc-man-mcb354-ch3"
    assert items[0].source is Source.EXTRACTED


def test_additions_carry_provenance():
    items: list[Assignment] = []
    apply_overrides(items, {"additions": [{
        "id": "x", "course": "C", "name": "N", "due_local": "2026-08-31T09:00",
        "note": "instructor schedule FA26 post1"}]})
    assert "post1" in items[0].provenance


def test_every_change_is_logged():
    """A silent correction is indistinguishable from a bug."""
    log: list[str] = []
    apply_overrides(
        [_a(1)],
        {"date_overrides": {"1": {"due_local": "2026-09-28T09:00"}},
         "additions": [{"id": "y", "course": "C", "name": "N", "due_local": "2026-09-08T09:00"}]},
        log,
    )
    assert len(log) == 2


def test_no_overrides_changes_nothing():
    items = [_a(1)]
    before = items[0].due_at
    apply_overrides(items, {"additions": [], "date_overrides": {}})
    assert items[0].due_at == before and len(items) == 1
