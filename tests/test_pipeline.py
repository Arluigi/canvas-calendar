from datetime import date

from canvas_calendar.models import Source
from canvas_calendar.pipeline import (
    apply_completion_policy,
    build_assignments,
    resolve_undated,
    term_courses,
)


def test_skips_non_term_courses():
    """Open and teacher-dev courses have no term code and must be dropped,
    not crash the run."""
    courses = [
        {"id": 1, "course_code": "mcb_244_120268_262964", "name": "MCB 244"},
        {"id": 2, "course_code": "provost_unihigh_open_219093", "name": "Nonfiction"},
        {"id": 3, "course_code": "dev_kindrtnk_227020", "name": "Illinois Chat"},
        {"id": 4, "course_code": None, "name": "Broken"},
    ]
    assert [c["id"] for c in term_courses(courses)] == [1]


def test_dated_assignment_keeps_canvas_source():
    raw = [
        {"id": 9, "name": "Chapter 1", "points_possible": 10.0, "due_at": "2026-08-25T19:00:00Z"}
    ]
    out = build_assignments(raw, course="MCB 244")
    assert out[0].source is Source.CANVAS
    assert out[0].due_at is not None


def test_undated_assignment_starts_unresolved():
    raw = [{"id": 9, "name": "Image submission -Wk1", "points_possible": 2.0, "due_at": None}]
    out = build_assignments(raw, course="MCB 364")
    assert out[0].source is Source.UNRESOLVED
    assert out[0].due_at is None


def test_null_points_becomes_zero_not_crash():
    raw = [{"id": 9, "name": "X", "points_possible": None, "due_at": None}]
    assert build_assignments(raw, course="X")[0].points == 0.0


def test_resolve_undated_uses_containing_module_date():
    """The core extraction path: assignment inherits its module's stated date."""
    raw = [{"id": 9, "name": "Image submission -Wk1", "points_possible": 2.0, "due_at": None}]
    items = build_assignments(raw, course="MCB 364")
    modules = {9: "Week 1 - Intro to Cell culture and Aseptic Techniques - August 26th/28th"}
    resolved = resolve_undated(items, modules, year=2026)
    assert resolved[0].source is Source.EXTRACTED
    assert resolved[0].due_at.date() == date(2026, 8, 28)  # last stated date
    assert "Week 1" in resolved[0].provenance


def test_resolve_undated_leaves_undatable_items_unresolved():
    """MCB 436 polls: the module gives no usable date, so they stay unresolved
    and surface in the digest rather than getting a guess."""
    raw = [{"id": 9, "name": "Class 16 - Poll", "points_possible": 5.0, "due_at": None}]
    items = build_assignments(raw, course="MCB 436")
    resolved = resolve_undated(items, {9: "Extra Credit Opportunities"}, year=2026)
    assert resolved[0].source is Source.UNRESOLVED
    assert resolved[0].due_at is None


def test_resolve_does_not_touch_canvas_dated_items():
    """A real Canvas due date must never be overwritten by extraction."""
    raw = [{"id": 9, "name": "X", "points_possible": 1.0, "due_at": "2026-09-10T04:59:59Z"}]
    items = build_assignments(raw, course="MCB 364")
    original = items[0].due_at
    resolved = resolve_undated(items, {9: "Week 1 - August 26th/28th"}, year=2026)
    assert resolved[0].due_at == original
    assert resolved[0].source is Source.CANVAS


def test_extracted_date_is_end_of_day_local():
    raw = [{"id": 9, "name": "Wk1", "points_possible": 3.0, "due_at": None}]
    items = resolve_undated(
        build_assignments(raw, course="MCB 364"),
        {9: "Week 1 - August 26th/28th"},
        year=2026,
    )
    assert (items[0].due_at.hour, items[0].due_at.minute) == (23, 59)


def test_build_assignments_flags_completed():
    raw = [
        {"id": 1, "name": "done", "points_possible": 10,
         "due_at": "2026-09-01T04:59:00Z",
         "submission": {"workflow_state": "graded", "score": 10.0}},
        {"id": 2, "name": "not done", "points_possible": 10,
         "due_at": "2026-09-01T04:59:00Z",
         "submission": {"workflow_state": "unsubmitted"}},
        {"id": 3, "name": "no submission key", "points_possible": 10,
         "due_at": "2026-09-01T04:59:00Z"},
    ]
    out = {a.canvas_id: a.completed for a in build_assignments(raw, course="MCB 244")}
    assert out == {1: True, 2: False, 3: False}


def test_completed_assignments_keep_their_due_date():
    """Completion must not blank the date -- diff still needs it to report."""
    raw = [{"id": 1, "name": "done", "points_possible": 1,
            "due_at": "2026-09-01T04:59:00Z",
            "submission": {"workflow_state": "submitted",
                           "submitted_at": "2026-08-30T10:00:00Z"}}]
    a = build_assignments(raw, course="MCB 244")[0]
    assert a.completed is True
    assert a.due_at is not None


def test_clear_completed_false_keeps_events():
    """The toggle must clear the flag, not filter the item out."""
    raw = [{"id": 1, "name": "done", "points_possible": 1,
            "due_at": "2026-09-01T04:59:00Z",
            "submission": {"workflow_state": "graded", "score": 5.0}}]
    items = build_assignments(raw, course="MCB 244")
    assert items[0].completed is True

    kept = apply_completion_policy(items, clear_completed=False)
    assert kept[0].completed is False
    assert len(kept) == 1, "the item stays in the fetch; only the flag clears"
