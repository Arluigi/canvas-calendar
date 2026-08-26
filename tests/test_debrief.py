from datetime import datetime, timedelta

from canvas_calendar.debrief import strip_html
from canvas_calendar.debrief_render import next_week, render, subject_line
from canvas_calendar.models import Assignment
from canvas_calendar.timeutil import CHICAGO

NOW = datetime(2026, 8, 26, 7, 0, tzinfo=CHICAGO)


def _a(name, course="MCB 244", days=0, hour=14, digest_only=False):
    return Assignment(
        canvas_id=1, name=name, points=1.0, course=course, digest_only=digest_only,
        due_at=(NOW + timedelta(days=days)).replace(hour=hour),
    )


def _data(**kw):
    base = {"now": NOW, "events": [], "due": [], "announcements": [],
            "conversations": [], "mail": [], "unresolved": {}, "errors": []}
    base.update(kw)
    return base


def test_strips_html_from_announcement_bodies():
    assert strip_html("<p>Hello <b>there</b>&nbsp;world</p>") == "Hello there world"


def test_strip_html_truncates_with_ellipsis():
    assert strip_html("x" * 400, limit=50).endswith("…")


def test_subject_leads_with_what_is_due_today():
    s = subject_line(_data(due=[_a("Ch1")], events=[{"all_day": False, "kind": "course"}]))
    assert "1 due today" in s
    assert "1 on your calendar" in s


def test_subject_counts_personal_meetings_separately():
    """A meeting is the thing most likely to collide with a class, so it gets
    its own number in the subject line."""
    s = subject_line(_data(events=[
        {"all_day": False, "kind": "course"},
        {"all_day": False, "kind": "personal"},
    ]))
    assert "2 on your calendar" in s
    assert "1 meeting" in s


def test_subject_when_nothing_due():
    assert "nothing due" in subject_line(_data())


def test_today_section_lists_classes_with_location():
    out = render(_data(events=[
        {"time": "14:00", "subject": "MCB 244 Lecture", "all_day": False,
         "location": "Foellinger Auditorium AUD", "organizer": "", "kind": "course"}]))
    assert "MCB 244 Lecture" in out
    assert "Foellinger" in out


def test_empty_day_is_stated_not_omitted():
    assert "Nothing scheduled today" in render(_data())


def test_personal_meeting_is_visually_marked():
    out = render(_data(events=[{
        "time": "10:00", "subject": "Advising appointment", "all_day": False,
        "location": "", "organizer": "Dr. Patel", "kind": "personal"}]))
    assert "meeting" in out
    assert "Advising appointment" in out
    assert "Dr. Patel" in out


def test_class_is_not_marked_as_a_meeting():
    out = render(_data(events=[{
        "time": "14:00", "subject": "MCB 244 Lecture", "all_day": False,
        "location": "Foellinger", "organizer": "", "kind": "course"}]))
    assert 'class="meet"' not in out


def test_due_today_is_marked_urgent():
    out = render(_data(due=[_a("Chapter 2", days=0)]))
    assert "TODAY" in out
    assert "urgent" in out


def test_due_tomorrow_is_distinguished():
    assert "tomorrow" in render(_data(due=[_a("Chapter 3", days=1)]))


def test_unread_mail_is_rendered():
    out = render(_data(mail=[{"subject": "Advising", "from": "Dr. Smith",
                              "received": "2026-08-26T06:00:00Z", "preview": "Please read"}]))
    assert "Dr. Smith" in out and "Advising" in out


def test_mail_failure_is_reported_not_hidden():
    """A silently missing section is worse than one that says it broke."""
    out = render(_data(mail=None))
    assert "Could not read your inbox" in out


def test_blind_spots_are_always_included():
    out = render(_data(unresolved={"MCB 436": ["Class 3 - Poll", "Class 4 - Poll"]}))
    assert "Not on your calendar (2)" in out
    assert "cannot quietly disappear" in out


def test_html_is_escaped():
    out = render(_data(announcements=[
        {"title": "<script>alert(1)</script>", "posted": None, "body": "x", "course": "", "url": ""}]))
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_next_week_excludes_digest_only_and_far_future():
    items = [_a("keep", days=2), _a("ec", days=2, digest_only=True), _a("far", days=30)]
    kept = [a.name for a in next_week(items, NOW)]
    assert kept == ["keep"]


def test_next_week_keeps_todays_earlier_deadline():
    """A 9am deadline must still show in a 7am debrief."""
    assert len(next_week([_a("Quiz", days=0, hour=9)], NOW)) == 1
