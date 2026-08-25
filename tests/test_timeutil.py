from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from canvas_calendar.timeutil import CHICAGO, is_end_of_day, parse_canvas_ts, to_local


def test_parses_canvas_utc_timestamp():
    dt = parse_canvas_ts("2026-08-25T19:00:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.hour == 19


def test_converts_to_central_during_cdt():
    # Real MCB 244 reading deadline: 19:00Z in August is 14:00 CDT (UTC-5)
    local = to_local(parse_canvas_ts("2026-08-25T19:00:00Z"))
    assert (local.hour, local.minute) == (14, 0)
    assert local.utcoffset().total_seconds() == -5 * 3600


def test_converts_to_central_during_cst():
    # Real MCB 354 deadline: 05:59Z in November is 23:59 CST (UTC-6)
    local = to_local(parse_canvas_ts("2026-11-07T05:59:00Z"))
    assert (local.month, local.day) == (11, 6)
    assert (local.hour, local.minute) == (23, 59)
    assert local.utcoffset().total_seconds() == -6 * 3600


def test_dst_boundary_keeps_both_sides_at_local_2359():
    """The bug this guards: 04:59Z and 05:59Z are the SAME local wall time,
    on opposite sides of the Nov 1 transition. A fixed -5 offset breaks one."""
    before = to_local(parse_canvas_ts("2026-10-29T04:59:00Z"))  # CDT
    after = to_local(parse_canvas_ts("2026-11-07T05:59:00Z"))  # CST
    assert before.hour == after.hour == 23
    assert before.minute == after.minute == 59


@pytest.mark.parametrize(
    "ts,expected",
    [
        ("2026-10-29T04:59:00Z", True),  # 23:59 CDT -> end of day
        ("2026-11-07T05:59:00Z", True),  # 23:59 CST -> end of day
        ("2026-09-01T04:59:59Z", True),  # 23:59:59 CDT
        ("2026-08-25T19:00:00Z", False),  # 14:00 -> real timed deadline
        ("2026-08-31T14:00:00Z", False),  # 09:00 -> real timed deadline
    ],
)
def test_end_of_day_classification(ts, expected):
    assert is_end_of_day(parse_canvas_ts(ts)) is expected


def test_end_of_day_uses_local_date_not_utc_date():
    """04:59:59Z on Sep 1 is Aug 31 locally. The all-day event must land Aug 31."""
    local = to_local(parse_canvas_ts("2026-09-01T04:59:59Z"))
    assert (local.month, local.day) == (8, 31)


def test_chicago_is_a_real_tz_not_an_offset():
    assert CHICAGO == ZoneInfo("America/Chicago")
    jan = datetime(2026, 1, 15, 12, tzinfo=CHICAGO)
    jul = datetime(2026, 7, 15, 12, tzinfo=CHICAGO)
    assert jan.utcoffset() != jul.utcoffset()


def test_rejects_naive_datetime():
    with pytest.raises(ValueError, match="naive"):
        to_local(datetime(2026, 8, 25, 12))
