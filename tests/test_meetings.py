from datetime import date, time

import pytest

from canvas_calendar.catalog.parser import Section
from canvas_calendar.meetings import (
    NON_INSTRUCTION,
    build_meetings,
    excluded_dates,
    extract_crn,
    first_occurrence,
    graph_days,
    parse_clock,
)
from canvas_calendar.models import Meeting


@pytest.mark.parametrize(
    "name,expected",
    [
        ("MCB 354 ADI Fall 2026 CRN40604", "40604"),
        ("MCB 244 A Fall 2026 CRN56301", "56301"),
        ("FSHN 120 ONL Fall 2026 CRN54527", "54527"),
        ("MCB 364 A Fall 2026 CRN71748", "71748"),
    ],
)
def test_extracts_crn_from_canvas_section_name(name, expected):
    """Canvas embeds the Course Explorer section id, so section resolution
    needs no input from the user."""
    assert extract_crn(name) == expected


@pytest.mark.parametrize("name", ["3rd Hour", "OPEN LEARNING - Nonfiction Writing", "", None])
def test_non_term_section_names_have_no_crn(name):
    assert extract_crn(name) is None


@pytest.mark.parametrize(
    "raw,expected",
    [("02:00PM", time(14, 0)), ("09:00AM", time(9, 0)), ("10:00AM", time(10, 0))],
)
def test_parses_clock(raw, expected):
    assert parse_clock(raw) == expected


@pytest.mark.parametrize("raw", ["ARRANGED", "", "n.a.", None])
def test_arranged_sections_have_no_clock(raw):
    assert parse_clock(raw) is None


def test_first_occurrence_matches_the_pattern():
    """Term starts Monday Aug 24; a TR lecture first meets Tuesday Aug 25.
    Graph shifts the whole series if the start is not a real occurrence."""
    assert first_occurrence([1, 3], date(2026, 8, 24)) == date(2026, 8, 25)


def test_first_occurrence_when_term_start_already_matches():
    assert first_occurrence([0, 2, 4], date(2026, 8, 24)) == date(2026, 8, 24)


def test_first_occurrence_wraps_to_next_week():
    # Term starts Monday; a Friday-only lab first meets Aug 28.
    assert first_occurrence([4], date(2026, 8, 24)) == date(2026, 8, 28)


def test_graph_day_names():
    assert graph_days([1, 3]) == ["tuesday", "thursday"]
    assert graph_days([0, 2, 4]) == ["monday", "wednesday", "friday"]


def test_labor_day_and_fall_break_are_non_instruction():
    assert date(2026, 9, 7) in NON_INSTRUCTION  # Labor Day
    assert date(2026, 11, 26) in NON_INSTRUCTION  # Thanksgiving
    assert date(2026, 11, 21) in NON_INSTRUCTION
    assert date(2026, 11, 29) in NON_INSTRUCTION
    assert date(2026, 11, 30) not in NON_INSTRUCTION  # instruction resumes


def test_excluded_dates_only_covers_this_meetings_weekdays():
    """A Monday holiday is irrelevant to a Tue/Thu lecture, and cancelling a
    nonexistent occurrence is an error."""
    tr = excluded_dates([1, 3])
    assert date(2026, 9, 7) not in tr  # Labor Day is a Monday
    assert date(2026, 11, 24) in tr  # Tuesday of fall break
    assert date(2026, 11, 26) in tr  # Thursday of fall break

    mwf = excluded_dates([0, 2, 4])
    assert date(2026, 9, 7) in mwf


def _section(days="TR", start="02:00PM", kind="Lecture"):
    return Section(
        meetings=[
            Meeting(
                days=days,
                start=start,
                end="03:20PM",
                building="Foellinger Auditorium",
                room="AUD",
                kind=kind,
                instructor="Garcia, M",
            )
        ],
        start_date=date(2026, 8, 24),
        end_date=date(2026, 12, 9),
    )


def test_build_meetings_resolves_a_real_enrollment():
    out = build_meetings(
        [("MCB 244", "MCB 244 A Fall 2026 CRN56301")], lambda crn: _section()
    )
    assert len(out) == 1
    m = out[0]
    assert m.crn == "56301"
    assert m.uid == "cc-mtg-56301"
    assert m.title == "MCB 244 Lecture"
    assert m.location == "Foellinger Auditorium AUD"


def test_online_sections_are_dropped():
    """FSHN 120 is online with ARRANGED times -- an event with no time or place
    is worse than no event."""
    online = Section(
        meetings=[
            Meeting(days="n.a.", start="ARRANGED", end="", building="", room="",
                    kind="Online", instructor="")
        ],
        start_date=date(2026, 8, 24),
        end_date=date(2026, 12, 9),
    )
    assert build_meetings([("FSHN 120", "FSHN 120 ONL Fall 2026 CRN54527")], lambda c: online) == []


def test_non_term_enrollments_are_skipped():
    assert build_meetings([("US History", "3rd Hour")], lambda c: _section()) == []


def test_meeting_uid_namespace_cannot_collide_with_assignments():
    out = build_meetings([("MCB 244", "MCB 244 A Fall 2026 CRN56301")], lambda c: _section())
    assert out[0].uid.startswith("cc-mtg-")


def test_location_omits_placeholder_values():
    sec = Section(
        meetings=[
            Meeting(days="R", start="03:00PM", end="03:50PM", building="Location Pending",
                    room="", kind="Discussion", instructor="")
        ],
        start_date=date(2026, 8, 24),
        end_date=date(2026, 12, 9),
    )
    out = build_meetings([("MCB 354", "MCB 354 ADM Fall 2026 CRN40610")], lambda c: sec)
    assert out[0].location == "Location Pending"


def test_course_explorer_url_follows_the_configured_term():
    """Hardcoded /2026/fall is right for one semester and silently wrong after."""
    from datetime import date

    from canvas_calendar.sync_meetings import explorer_base
    from canvas_calendar.terms import Term

    t = Term(year=2027, season="spring", start=date(2027, 1, 19),
             end=date(2027, 5, 5), holidays=())
    assert explorer_base(t).endswith("/2027/spring")


def test_excluded_dates_follows_the_configured_term():
    from datetime import date

    from canvas_calendar.meetings import excluded_dates
    from canvas_calendar.terms import Term

    spring = Term(year=2027, season="spring", start=date(2027, 1, 19),
                  end=date(2027, 5, 5),
                  holidays=(date(2027, 3, 15), date(2027, 3, 16)))  # Mon, Tue
    assert excluded_dates([0], spring) == [date(2027, 3, 15)]   # Monday only
    assert excluded_dates([1], spring) == [date(2027, 3, 16)]   # Tuesday only
    assert excluded_dates([2], spring) == []                    # no Wednesday
