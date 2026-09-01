import dataclasses
from datetime import date

import pytest

from canvas_calendar.terms import DEFAULT_TERM, term_from_config


def test_default_term_matches_the_shipped_fall_2026_dates():
    assert DEFAULT_TERM.start == date(2026, 8, 24)
    assert DEFAULT_TERM.end == date(2026, 12, 9)
    assert date(2026, 9, 7) in DEFAULT_TERM.holidays  # Labor Day
    assert date(2026, 11, 25) in DEFAULT_TERM.holidays  # Fall Break
    assert len(DEFAULT_TERM.holidays) == 10


def test_covers_reports_whether_a_day_is_in_term():
    assert DEFAULT_TERM.covers(date(2026, 9, 15)) is True
    assert DEFAULT_TERM.covers(date(2026, 8, 1)) is False  # before
    assert DEFAULT_TERM.covers(date(2027, 1, 5)) is False  # after


def test_term_from_config_parses_iso_dates():
    t = term_from_config({
        "year": 2027, "season": "spring",
        "start": "2027-01-19", "end": "2027-05-05",
        "holidays": ["2027-03-20"],
    })
    assert t.year == 2027
    assert t.season == "spring"
    assert t.start == date(2027, 1, 19)
    assert t.holidays == (date(2027, 3, 20),)  # tuple: Term is frozen


def test_term_from_config_rejects_a_backwards_range():
    """A start after the end produces zero meetings and no error. Loud is better."""
    with pytest.raises(ValueError, match="starts after"):
        term_from_config({"year": 2027, "season": "spring",
                          "start": "2027-05-05", "end": "2027-01-19", "holidays": []})


def test_term_from_config_falls_back_to_default_when_absent():
    assert term_from_config(None) is DEFAULT_TERM


def test_term_is_immutable():
    """Frozen so a caller cannot mutate the shared DEFAULT_TERM."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_TERM.start = date(2020, 1, 1)
