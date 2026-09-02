"""Windows and cases are the real ones measured on 2026-09-01: 43 of 50 timed
assignments landed exactly on a class start time."""

from datetime import datetime, time

import pytest

from canvas_calendar.models import Assignment, Source
from canvas_calendar.overlap import (
    MeetingWindow,
    apply_meeting_offsets,
    windows_from_config,
)
from canvas_calendar.timeutil import CHICAGO

MCB244 = MeetingWindow(1, time(14, 0), time(15, 20), "MCB 244 Lecture")   # Tue
MCB354 = MeetingWindow(0, time(9, 0), time(9, 50), "MCB 354 Lecture")     # Mon


def _a(when, name="Chapter 3", course="MCB 244", **kw):
    return Assignment(canvas_id=1, name=name, points=1.0, course=course,
                      due_at=when, source=Source.CANVAS, **kw)


def test_assignment_on_the_class_start_is_moved_into_the_gap_before_it():
    a = _a(datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO))          # Tue 2:00 PM
    notes = apply_meeting_offsets([a], [MCB244])

    assert a.display_start == datetime(2026, 9, 8, 13, 45, tzinfo=CHICAGO)
    assert a.display_end == datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO)
    assert len(notes) == 1 and "MCB 244 Lecture" in notes[0]


def test_the_real_due_time_is_never_altered():
    """The deadline is a fact. Only the rectangle moves."""
    due = datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO)
    a = _a(due)
    apply_meeting_offsets([a], [MCB244])
    assert a.due_at == due
    assert "Actually due 2:00 PM" in a.display_reason


def test_the_moved_block_ends_exactly_when_class_begins():
    """A 30-minute block starting 15 min early would still overlap by 15."""
    a = _a(datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO))
    apply_meeting_offsets([a], [MCB244])
    assert a.display_end == a.display_start.replace(hour=14, minute=0)
    assert (a.display_end - a.display_start).total_seconds() == 15 * 60


def test_an_assignment_mid_block_is_moved_too():
    """Due 2:30, inside the 2:00-3:20 lecture."""
    a = _a(datetime(2026, 9, 8, 14, 30, tzinfo=CHICAGO))
    apply_meeting_offsets([a], [MCB244])
    assert a.display_start == datetime(2026, 9, 8, 13, 45, tzinfo=CHICAGO)


def test_an_assignment_outside_every_block_is_left_alone():
    a = _a(datetime(2026, 9, 8, 23, 0, tzinfo=CHICAGO))
    assert apply_meeting_offsets([a], [MCB244]) == []
    assert a.display_start is None


def test_the_wrong_weekday_does_not_match():
    """Tue 2 PM lecture must not move a Wednesday 2 PM deadline."""
    a = _a(datetime(2026, 9, 9, 14, 0, tzinfo=CHICAGO))  # Wednesday
    assert apply_meeting_offsets([a], [MCB244]) == []
    assert a.display_start is None


def test_all_day_items_are_untouched():
    a = _a(datetime(2026, 9, 8, 23, 59, tzinfo=CHICAGO))
    assert apply_meeting_offsets([a], [MCB244]) == []
    assert a.display_start is None


def test_digest_only_items_are_untouched():
    a = _a(datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO), digest_only=True)
    assert apply_meeting_offsets([a], [MCB244]) == []


def test_undated_items_are_untouched():
    a = _a(None)
    assert apply_meeting_offsets([a], [MCB244]) == []


def test_no_windows_means_no_change():
    a = _a(datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO))
    assert apply_meeting_offsets([a], []) == []
    assert a.display_start is None


def test_zero_minutes_disables_it():
    a = _a(datetime(2026, 9, 8, 14, 0, tzinfo=CHICAGO))
    assert apply_meeting_offsets([a], [MCB244], minutes=0) == []
    assert a.display_start is None


def test_first_matching_window_wins_and_it_stops_looking():
    a = _a(datetime(2026, 9, 14, 9, 0, tzinfo=CHICAGO))  # Monday 9:00
    apply_meeting_offsets([a], [MCB244, MCB354])
    assert a.display_start == datetime(2026, 9, 14, 8, 45, tzinfo=CHICAGO)


def test_windows_from_config_round_trips():
    ws = windows_from_config([
        {"weekday": 1, "start": "14:00", "end": "15:20", "title": "MCB 244 Lecture"}
    ])
    assert ws == [MCB244]


def test_windows_from_config_tolerates_absence():
    assert windows_from_config(None) == []
    assert windows_from_config([]) == []


@pytest.mark.parametrize("hhmm,expected", [("09:00", time(9, 0)), ("14:00", time(14, 0))])
def test_config_times_parse(hhmm, expected):
    ws = windows_from_config([{"weekday": 0, "start": hhmm, "end": "23:59"}])
    assert ws[0].start == expected
