import pytest

from canvas_calendar.models import Assignment
from canvas_calendar.rules import Disposition, classify


def make(name, points=10.0, canvas_id=1):
    return Assignment(canvas_id=canvas_id, name=name, points=points, due_at=None, course="X")


@pytest.mark.parametrize(
    "name",
    [
        "PILLAR A VIDEO QUIZ - extra credit points will update within 2 weeks",
        "PILLAR C - EXTRA CREDIT QUIZ - extra credit points will be awarded",
        "Extra Credit - Submit Here - Summary #1 (classic)",
        "PILLAR A - Data for Improvement (DI) extra credit quiz",
    ],
)
def test_extra_credit_goes_to_digest(name):
    assert classify(make(name, points=0.0)) is Disposition.DIGEST


@pytest.mark.parametrize(
    "name",
    [
        "PILLAR A - REFLECTIVE ASSIGNMENT (discussion)",
        "PILLAR B REFLECTIVE ASSIGNMENT (discussion) ",
        "PILLAR C REFLECTIVE ASSIGNMENT (discussion) ",
        "PILLAR D REFLECTIVE ASSIGNMENT (discussion) -",
        "PILLAR B - Data for Improvement (DI) quiz",
        "PILLAR C - Data for Improvement (DI) quiz",
        "PILLAR D - Data for Improvement (DI) quiz",
    ],
)
def test_zero_point_non_extra_credit_is_calendared(name):
    """The seven real FSHN 120 items a points>0 filter would have silently
    dropped. Being wrong here loses coursework, so 0-point defaults to
    calendar."""
    assert classify(make(name, points=0.0)) is Disposition.CALENDAR


def test_excluded_ids_are_skipped():
    a = make("DISABILITY RESOURCES AND EDUCATIONAL SERVICES - DROP YOUR LOA HERE",
             points=0.0, canvas_id=1697990)
    assert classify(a, exclude={1697990}) is Disposition.SKIP


def test_graded_work_is_calendared():
    assert classify(make("Chapter 1; Sections 1.1, 1.4-1.7", 10.0)) is Disposition.CALENDAR


def test_mcb364_lab_items_are_calendared():
    """2-3 point undated lab work -- a points>0 filter never touched these,
    but confirm they survive the corrected rule too."""
    assert classify(make("Image submission -Wk1", 2.0)) is Disposition.CALENDAR
    assert classify(make("Pre-Lab Quiz -Week 1", 2.0)) is Disposition.CALENDAR
    assert classify(make("Wk7", 3.0)) is Disposition.CALENDAR


def test_extra_credit_match_is_case_and_space_insensitive():
    assert classify(make("EXTRA  CREDIT bonus", 5.0)) is Disposition.DIGEST
    assert classify(make("extracredit bonus", 5.0)) is Disposition.DIGEST


def test_exclude_wins_over_extra_credit():
    a = make("Extra Credit thing", points=0.0, canvas_id=42)
    assert classify(a, exclude={42}) is Disposition.SKIP
