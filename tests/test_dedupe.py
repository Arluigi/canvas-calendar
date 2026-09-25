"""One piece of work, one event. Measured 2026-09-08: the hand-entered
'Ch5 Adaptive Quiz' (added 2026-08-31 when Canvas had no such assignment) sat
beside Canvas's later 'Ch5 Adaptive Quiz: Protein Function' for a week, and
stayed on the calendar after the Canvas one was cleared as completed."""

from datetime import datetime

from canvas_calendar.dedupe import dedupe, same_work
from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import CHICAGO

WHEN = datetime(2026, 9, 8, 9, 0, tzinfo=CHICAGO)


def _canvas(name, cid=1652221, due=WHEN, course="MCB 354", completed=False):
    return Assignment(
        canvas_id=cid,
        name=name,
        points=2.0,
        course=course,
        due_at=due,
        source=Source.CANVAS if due else Source.UNRESOLVED,
        completed=completed,
    )


def _manual(name, mid="mcb354-ch5", course="MCB 354"):
    return Assignment(
        canvas_id=mid,
        name=name,
        points=2.0,
        course=course,
        due_at=WHEN,
        source=Source.EXTRACTED,
        namespace="man-",
    )


def _page(name, course="MCB 354"):
    return Assignment(
        canvas_id=f"70420-{name.lower().replace(' ', '-')}",
        name=name,
        points=0.0,
        course=course,
        due_at=WHEN,
        source=Source.EXTRACTED,
        namespace="pg-",
    )


def _sub(name, course="MCB 320"):
    return Assignment(
        canvas_id=5440557,
        name=name,
        points=0.0,
        course=course,
        due_at=WHEN,
        source=Source.EXTRACTED,
        namespace="mi-",
    )


# --- name matching ---------------------------------------------------------


def test_subtitle_after_colon_is_the_same_work():
    assert same_work("Ch5 Adaptive Quiz", "Ch5 Adaptive Quiz: Protein Function")


def test_match_is_symmetric():
    assert same_work("Ch5 Adaptive Quiz: Protein Function", "Ch5 Adaptive Quiz")


def test_case_and_whitespace_do_not_matter():
    assert same_work("  ch5  adaptive quiz ", "Ch5 Adaptive Quiz")


def test_prefix_must_end_at_a_word_boundary():
    """'Exam 1' is not 'Exam 10'."""
    assert not same_work("Exam 1", "Exam 10")


def test_prefix_in_the_middle_is_not_a_match():
    """MCB 244's 'Practice Exam 1' is not the exam."""
    assert not same_work("Exam 1", "Practice Exam 1")
    assert not same_work("Exam 1", "September 14: Review for Exam 1")


# --- precedence --------------------------------------------------------------


def test_dated_canvas_assignment_supersedes_a_manual_addition():
    log: list[str] = []
    kept = dedupe(
        [_canvas("Ch5 Adaptive Quiz: Protein Function"), _manual("Ch5 Adaptive Quiz")], log
    )
    assert [a.uid for a in kept] == ["cc-1652221"]
    assert "overrides.json" in log[0] and "1652221" in log[0]


def test_superseded_addition_disappears_even_when_canvas_says_complete():
    """The whole point: completion lives on the Canvas item, so the manual
    twin must not outlive it."""
    kept = dedupe(
        [
            _canvas("Ch5 Adaptive Quiz: Protein Function", completed=True),
            _manual("Ch5 Adaptive Quiz"),
        ],
        [],
    )
    assert len(kept) == 1 and kept[0].completed


def test_undated_canvas_assignment_does_not_supersede():
    """If Canvas lists the work but gives it no date, the hand entry is the
    only thing putting it on a calendar. Keep it, say so."""
    log: list[str] = []
    kept = dedupe(
        [_canvas("Ch5 Adaptive Quiz: Protein Function", due=None), _manual("Ch5 Adaptive Quiz")],
        log,
    )
    assert {a.uid for a in kept} == {"cc-1652221", "cc-man-mcb354-ch5"}
    assert any("no due date" in line for line in log)


def test_manual_addition_beats_page_extraction():
    """A hand entry carries the room and the section; the page scan does not."""
    log: list[str] = []
    kept = dedupe([_page("Exam 1"), _manual("Exam 1", mid="mcb354-exam1")], log)
    assert [a.uid for a in kept] == ["cc-man-mcb354-exam1"]
    assert log and "Exam 1" in log[0]


def test_subheader_extraction_beats_page_extraction():
    kept = dedupe([_page("Exam 1", course="MCB 320"), _sub("Exam 1")], [])
    assert [a.uid for a in kept] == ["cc-mi-5440557"]


def test_same_tier_is_never_merged():
    """Two Canvas assignments are two assignments, whatever their names."""
    items = [
        _canvas("Week 3 discussion worksheet", cid=1),
        _canvas("Week 3 discussion worksheet: part 2", cid=2),
    ]
    assert len(dedupe(items, [])) == 2


def test_different_courses_never_merge():
    kept = dedupe([_canvas("Exam 1", course="MCB 244"), _manual("Exam 1", course="MCB 354")], [])
    assert len(kept) == 2


def test_order_of_input_does_not_matter():
    a = [_manual("Ch5 Adaptive Quiz"), _canvas("Ch5 Adaptive Quiz: Protein Function")]
    b = list(reversed(a))
    assert [x.uid for x in dedupe(a, [])] == [x.uid for x in dedupe(b, [])] == ["cc-1652221"]


def test_nothing_to_do_changes_nothing():
    items = [_canvas("A", cid=1), _manual("B")]
    log: list[str] = []
    assert dedupe(items, log) == items and log == []
