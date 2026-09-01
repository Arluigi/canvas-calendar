from datetime import date

import pytest

from canvas_calendar.modules import extract_dates, parse_subheader_date


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "Week 1 - Intro to Cell culture and Aseptic Techniques     -   August 26th/28th",
            [date(2026, 8, 26), date(2026, 8, 28)],
        ),
        (
            "Week 6 -  Cell Viabilty and Cell Death - September 30th/October 2nd",
            [date(2026, 9, 30), date(2026, 10, 2)],
        ),
        ("Week 7 - Midterm -  October 7th/ 9th", [date(2026, 10, 7), date(2026, 10, 9)]),
        (
            "Week 9 - Neuronal Differentiation - October 21st/23rd",
            [date(2026, 10, 21), date(2026, 10, 23)],
        ),
        (
            "Week 14 - MiniSymposium - December 2nd/4th",
            [date(2026, 12, 2), date(2026, 12, 4)],
        ),
    ],
)
def test_extracts_dates_from_mcb364_module_titles(title, expected):
    assert extract_dates(title, year=2026) == expected


def test_handles_merged_week_module():
    """Weeks 12 & 13 share one module. Ordinal or label arithmetic both break
    here; reading the title does not."""
    title = "Weeks 12 &13 - Independent project  November 11th/13th & November 18th/20th"
    assert extract_dates(title, year=2026) == [
        date(2026, 11, 11),
        date(2026, 11, 13),
        date(2026, 11, 18),
        date(2026, 11, 20),
    ]


def test_cancelled_week_still_parses_its_date():
    assert extract_dates("Week 15 - December 9th -  NO CLASS", year=2026) == [date(2026, 12, 9)]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("September 16: EXAM 1 (Lectures 1-7 of PARTS 1-2)", date(2026, 9, 16)),
        ("October 12: EXAM 2 (Lectures 8-13 of PART 3)", date(2026, 10, 12)),
        ("November 6: EXAM 3 (Lectures 14-21 of PARTS 4-5)", date(2026, 11, 6)),
        ("December 9: EXAM 4 (Lectures 22-28 of PART 6)", date(2026, 12, 9)),
    ],
)
def test_extracts_mcb320_exam_dates_from_subheaders(text, expected):
    assert parse_subheader_date(text, year=2026) == expected


def test_subheader_with_range_takes_first_date():
    assert parse_subheader_date("August 24 - 26: Cell Death", year=2026) == date(2026, 8, 24)


def test_subheader_with_two_explicit_dates():
    """'November 20, November 30: Stroke' -- two separate sessions."""
    assert extract_dates("November 20, November 30: Stroke", year=2026) == [
        date(2026, 11, 20),
        date(2026, 11, 30),
    ]


def test_month_spanning_subheader():
    assert parse_subheader_date(
        "October 30 - November 2: Metabolic Syndrome", year=2026
    ) == date(2026, 10, 30)


def test_returns_empty_when_no_date_present():
    assert extract_dates("Appendices - Contains important information", year=2026) == []
    assert parse_subheader_date("Resourses", year=2026) is None


def test_lecture_numbers_are_not_mistaken_for_dates():
    """'EXAM 1 (Lectures 1-7 of PARTS 1-2)' contains many bare numbers.
    Only the leading named date may be extracted."""
    assert extract_dates("September 16: EXAM 1 (Lectures 1-7 of PARTS 1-2)", year=2026) == [
        date(2026, 9, 16)
    ]


@pytest.mark.parametrize(
    "title,expected",
    [
        ("8/24 - Lecture 1: Brenda Wilson", date(2026, 8, 24)),
        ("11/30 - Lecture 13: James Slauch", date(2026, 11, 30)),
        ("12/7 - Lecture 14: Brenda Wilson", date(2026, 12, 7)),
    ],
)
def test_mcb436_lecture_module_slash_format(title, expected):
    assert extract_dates(title, year=2026) == [expected]


def test_module_with_no_date_returns_empty():
    assert extract_dates("Extra Credit Opportunities", year=2026) == []
    assert extract_dates("MCB 364 - Orientation", year=2026) == []
