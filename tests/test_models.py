import pytest

from canvas_calendar.calendars.base import UID_PREFIX, assert_ours
from canvas_calendar.models import Assignment, CourseRef, Meeting, Source


def test_course_ref_parses_canvas_course_code():
    ref = CourseRef.from_canvas_code("mcb_244_120268_262964")
    assert ref.subject == "MCB"
    assert ref.number == "244"
    assert ref.term_id == "120268"


def test_course_ref_rejects_malformed_code():
    with pytest.raises(ValueError, match="unrecognized"):
        CourseRef.from_canvas_code("not-a-course-code")


def test_course_ref_handles_open_course_codes():
    """Non-departmental courses (provost_unihigh_open_219093) are not term
    courses and must be rejected rather than silently mis-parsed."""
    with pytest.raises(ValueError):
        CourseRef.from_canvas_code("provost_unihigh_open_219093")


def test_course_ref_rejects_teacher_dev_course():
    """dev_kindrtnk_227020 (Illinois Chat) has no subject/number pair."""
    with pytest.raises(ValueError):
        CourseRef.from_canvas_code("dev_kindrtnk_227020")


def test_meeting_days_expand_to_weekdays():
    m = Meeting(
        days="TR",
        start="02:00PM",
        end="03:20PM",
        building="Foellinger",
        room="AUD",
        kind="Lecture",
        instructor="Garcia, M",
    )
    assert m.weekdays() == [1, 3]  # Tue, Thu


def test_meeting_handles_mwf():
    m = Meeting(
        days="MWF",
        start="09:00AM",
        end="09:50AM",
        building="Burrill",
        room="124",
        kind="Lecture",
        instructor="X",
    )
    assert m.weekdays() == [0, 2, 4]


def test_assignment_defaults_to_canvas_source():
    a = Assignment(canvas_id=1, name="X", points=10.0, due_at=None, course="MCB 244")
    assert a.source is Source.CANVAS
    assert a.uid == "cc-1"


def test_uid_prefix_is_stable():
    """The cc- prefix is what the never-delete-foreign-events check tests
    against. Changing it would orphan every existing calendar event."""
    assert Assignment(
        canvas_id=1605622, name="Wk1", points=3.0, due_at=None, course="MCB 364"
    ).uid.startswith("cc-")


@pytest.mark.parametrize(
    "canvas_id,namespace,expected",
    [
        (1652210, "", "cc-1652210"),  # Canvas assignment
        (5440557, "mi-", "cc-mi-5440557"),  # module item / SubHeader event
        ("mcb320-quiz1", "man-", "cc-man-mcb320-quiz1"),  # manual addition
    ],
)
def test_uid_format_is_frozen(canvas_id, namespace, expected):
    """Live calendar events and every state.db row key off this exact shape.
    Changing it orphans all of them silently, which is the one failure mode
    this project exists to prevent."""
    a = Assignment(
        canvas_id=canvas_id,
        name="x",
        points=0.0,
        due_at=None,
        course="MCB 320",
        namespace=namespace,
    )
    assert a.uid == expected
    assert a.uid.startswith(UID_PREFIX)
    assert_ours(a.uid)
