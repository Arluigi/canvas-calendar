from datetime import date

from canvas_calendar.models import Assignment, Source
from canvas_calendar.pipeline import subheader_events


def _items(*titles):
    return [
        {"id": 5440590 + i, "type": "SubHeader", "title": t} for i, t in enumerate(titles)
    ]


def test_extracts_mcb320_exams_from_subheaders():
    items = _items(
        " October 23 - 26: Introduction to Metabolism",
        " November 4: Review for Exam 3",
        "November 6: EXAM 3 (Lectures 14-21 of PARTS 4-5)",
    )
    out = subheader_events(items, course="MCB 320", year=2026)
    names = [a.name for a in out]
    assert any("EXAM 3" in n for n in names)
    assert any("Review for Exam 3" in n for n in names)
    # A plain lecture topic is not an assessment and must not be calendared.
    assert not any("Introduction to Metabolism" in n for n in names)


def test_exam_lands_on_stated_date():
    out = subheader_events(
        _items("November 6: EXAM 3 (Lectures 14-21 of PARTS 4-5)"), course="MCB 320", year=2026
    )
    assert out[0].due_at.date() == date(2026, 11, 6)


def test_subheader_events_are_marked_extracted_with_provenance():
    out = subheader_events(
        _items("September 16: EXAM 1 (Lectures 1-7 of PARTS 1-2)"), course="MCB 320", year=2026
    )
    assert out[0].source is Source.EXTRACTED
    assert "EXAM 1" in out[0].provenance


def test_uid_namespace_prevents_collision_with_assignment_ids():
    """SubHeader ids and assignment ids are different Canvas ID spaces. Without
    a namespace, module item 5440597 and assignment 5440597 would share a UID
    and one would overwrite the other on the calendar."""
    sub = subheader_events(_items("November 6: EXAM 3"), course="MCB 320", year=2026)[0]
    asg = Assignment(canvas_id=sub.canvas_id, name="X", points=1.0, due_at=None, course="Y")
    assert sub.uid != asg.uid
    assert sub.uid.startswith("cc-mi-")
    assert asg.uid.startswith("cc-")


def test_undated_subheader_is_ignored():
    assert subheader_events(_items("Resourses"), course="MCB 320", year=2026) == []


def test_non_subheader_items_are_ignored():
    items = [{"id": 1, "type": "File", "title": "November 6: EXAM 3 handout.pdf"}]
    assert subheader_events(items, course="MCB 320", year=2026) == []


def test_matches_midterm_keyword():
    out = subheader_events(_items("October 7: Midterm"), course="MCB 364", year=2026)
    assert len(out) == 1
