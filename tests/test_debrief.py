from datetime import datetime, timedelta

from canvas_calendar.debrief import strip_html
from canvas_calendar.debrief_render import next_week, render, subject_line
from canvas_calendar.models import Assignment
from canvas_calendar.timeutil import CHICAGO

NOW = datetime(2026, 8, 26, 7, 0, tzinfo=CHICAGO)


def _a(name, course="MCB 244", days=0, hour=14, minute=0, digest_only=False):
    return Assignment(
        canvas_id=1, name=name, points=1.0, course=course, digest_only=digest_only,
        due_at=(NOW + timedelta(days=days)).replace(hour=hour, minute=minute),
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


def test_subject_counts_all_calendar_items_together():
    """The meeting/class distinction was dropped: the event name already says
    which it is, so a separate badge and count were noise."""
    s = subject_line(_data(events=[
        {"all_day": False, "kind": "course"},
        {"all_day": False, "kind": "personal"},
    ]))
    assert "2 on your calendar" in s
    assert "meeting" not in s


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


def test_personal_events_appear_without_a_badge():
    out = render(_data(events=[{
        "time": "10:00", "subject": "Advising appointment", "all_day": False,
        "location": "", "organizer": "Dr. Patel", "kind": "personal"}]))
    assert "Advising appointment" in out
    assert 'class="meet"' not in out


def test_class_is_not_marked_as_a_meeting():
    out = render(_data(events=[{
        "time": "14:00", "subject": "MCB 244 Lecture", "all_day": False,
        "location": "Foellinger", "organizer": "", "kind": "course"}]))
    assert 'class="meet"' not in out


def test_due_today_is_marked_urgent():
    out = render(_data(due=[_a("Chapter 2", days=0)]))
    assert "Today" in out
    assert "urgent" in out


def test_due_tomorrow_is_distinguished():
    assert "Tomorrow" in render(_data(due=[_a("Chapter 3", days=1)]))


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


# --- mail triage ---------------------------------------------------------

from canvas_calendar.mail_triage import score, triage


def _m(frm="Dr. Garcia", addr="garcia@illinois.edu", subj="About your quiz",
       to=("aryanss2@illinois.edu",), importance="normal"):
    return {"from": frm, "from_address": addr, "subject": subj,
            "to": list(to), "importance": importance, "received": "2026-08-26T06:00:00Z",
            "preview": "x"}


ME = "aryanss2@illinois.edu"


def test_bookstore_receipt_is_filtered():
    """The exact noise the user called out."""
    pts, why = score(_m(frm="IU Bookstore", addr="receipts@bookstore.com",
                        subj="Your Receipt: RC5-00387132-214"), ME)
    assert pts < 0 and "transactional" in why or "automated" in why


def test_massmail_is_filtered():
    pts, _ = score(_m(frm="Gioconda Guerra Perez",
                      subj="MASSMAIL - Information on accommodating religious observances",
                      to=["all-students@illinois.edu"]), ME)
    assert pts < 0


def test_instructor_mail_is_highlighted():
    pts, why = score(_m(), ME, instructors=["Garcia, M"])
    assert pts > 0
    assert "instructor" in why


def test_direct_mail_asking_something_is_highlighted():
    pts, _ = score(_m(frm="Advising", addr="advising@illinois.edu",
                      subj="Please confirm your appointment"), ME)
    assert pts > 0


def test_high_importance_is_highlighted():
    pts, why = score(_m(subj="schedule", importance="high"), ME)
    assert pts > 0 and "flagged high" in why


def test_no_reply_sender_is_filtered():
    pts, _ = score(_m(frm="Canvas", addr="no-reply@instructure.com", subj="Assignment graded"), ME)
    assert pts < 0


def test_bulk_recipient_list_is_penalised():
    pts, _ = score(_m(subj="FYI", to=[f"u{i}@illinois.edu" for i in range(30)]), ME)
    assert pts <= 0


def test_triage_reports_what_it_set_aside():
    """Filtered mail must be counted, never silently dropped -- otherwise a bad
    filter is indistinguishable from a quiet inbox."""
    msgs = [_m(), _m(frm="Store", addr="receipts@x.com", subj="Your Receipt: 123")]
    keep, filtered = triage(msgs, ME, instructors=["Garcia, M"])
    assert len(keep) == 1
    assert len(filtered) == 1
    assert "Store" in filtered[0]


def test_triage_ranks_most_important_first():
    low = _m(frm="Someone", addr="s@illinois.edu", subj="hello")
    high = _m(subj="Please respond: exam conflict", importance="high")
    keep, _ = triage([low, high], ME, instructors=["Garcia, M"])
    assert "exam conflict" in keep[0]["subject"]


# --- formatting ----------------------------------------------------------


def test_end_of_day_replaces_1159pm_noise():
    """11:59 PM on every row was most of the visual clutter."""
    out = render(_data(due=[_a("Reflective", days=2, hour=23, minute=59)]))
    assert "end of day" in out
    assert "11:59" not in out


def test_real_deadline_times_are_kept():
    out = render(_data(due=[_a("Chapter 1", days=2, hour=14)]))
    assert "2:00 PM" in out


def test_due_items_are_grouped_by_day():
    out = render(_data(due=[_a("A", days=0, hour=14), _a("B", days=0, hour=23, minute=59),
                            _a("C", days=3, hour=9)]))
    assert out.count("<table>") >= 2
    assert "Today" in out


def test_today_events_use_12_hour_clock():
    out = render(_data(events=[{"time": "14:00", "subject": "MCB 244 Lecture",
                                "all_day": False, "location": "Foellinger", "kind": "course"}]))
    assert "2:00 PM" in out
    assert ">14:00<" not in out


def test_canvas_inbox_section_is_gone():
    out = render(_data(conversations=[{"from": "X", "subject": "Y", "preview": "Z"}]))
    assert "Canvas inbox" not in out


def test_no_meeting_badge():
    out = render(_data(events=[{"time": "10:00", "subject": "Quick Connect", "all_day": False,
                                "location": "", "kind": "personal"}]))
    assert 'class="meet"' not in out
    assert "Quick Connect" in out


def test_the_debrief_does_not_highlight_itself():
    """It mails from the same account it reads, so without an explicit guard
    every morning's debrief flags yesterday's as important."""
    pts, why = score(_m(frm="Sachdev, Aryan", addr=ME,
                        subj="Tue Aug 25 — 1 due today"), ME)
    assert pts < 0
    assert "sent by you" in why


def test_meeting_urls_are_shortened_to_a_platform_name():
    from canvas_calendar.debrief import _tidy_location

    assert _tidy_location("https://zoom.us/j/91438820346?pwd=As6UsfeHgwK7") == "Zoom"
    assert _tidy_location("https://teams.microsoft.com/l/meetup-join/x") == "Teams"


def test_real_room_locations_are_preserved():
    from canvas_calendar.debrief import _tidy_location

    assert _tidy_location("Foellinger Auditorium AUD") == "Foellinger Auditorium AUD"


def test_debrief_is_sent_once_per_day(monkeypatch, tmp_path):
    """A pmset scheduled wake and launchd's missed-job catch-up can both fire
    on the same morning; without a guard that sends two debriefs."""
    import canvas_calendar.run_debrief as rd

    monkeypatch.setattr(rd, "load_last_run", lambda: NOW)
    assert rd.already_sent_today(NOW) is True
    assert rd.already_sent_today(NOW + timedelta(days=1)) is False
