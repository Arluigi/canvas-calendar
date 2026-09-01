from datetime import datetime

from canvas_calendar.cli import render_preview
from canvas_calendar.models import Assignment, Source


def _a(name, ts, points=10.0, cid=1, course="MCB 244"):
    return Assignment(
        canvas_id=cid,
        name=name,
        points=points,
        course=course,
        due_at=datetime.fromisoformat(ts),
    )


def test_renders_timed_and_all_day_separately():
    out = render_preview(
        [
            _a("Chapter 1", "2026-08-25T19:00:00Z", cid=1),  # 14:00 local
            _a("Reflective", "2026-09-01T04:59:59Z", cid=2),  # 23:59 local Aug 31
        ]
    )
    assert "2:00 PM" in out
    assert "all day" in out


def test_all_day_lands_on_local_date_not_utc_date():
    """04:59:59Z Sep 1 is Aug 31 locally -- the banner must say Aug 31."""
    out = render_preview([_a("Reflective", "2026-09-01T04:59:59Z", cid=2)])
    assert "Aug 31" in out


def test_november_deadline_renders_at_local_2359():
    """The DST regression. A fixed -5 offset would render this at 10:59 PM."""
    out = render_preview([_a("Ch17 Quiz", "2026-11-07T05:59:00Z", cid=3)])
    assert "Nov 06" in out or "Nov 6" in out
    assert "all day" in out


def test_flags_extracted_dates_in_output():
    a = _a("Image submission -Wk1", "2026-08-28T04:59:00Z", points=2.0, cid=3)
    a.source = Source.EXTRACTED
    a.provenance = "module: Week 1 ... August 26th/28th"
    out = render_preview([a])
    assert "[extracted]" in out
    assert "Week 1" in out


def test_unresolved_items_are_shown_not_dropped():
    a = Assignment(
        canvas_id=9, name="Class 16 - Poll", points=5.0, due_at=None,
        course="MCB 436", source=Source.UNRESOLVED,
    )
    out = render_preview([a])
    assert "unresolved" in out
    assert "Class 16" in out


def test_empty_input_does_not_crash():
    assert "no assignments" in render_preview([]).lower()


def test_sorts_by_due_date_with_unresolved_last():
    items = [
        Assignment(canvas_id=1, name="Later", points=1.0, due_at=None,
                   course="X", source=Source.UNRESOLVED),
        _a("Earlier", "2026-08-25T19:00:00Z", cid=2),
    ]
    lines = render_preview(items).splitlines()
    assert "Earlier" in lines[0]
    assert "Later" in lines[-1]
