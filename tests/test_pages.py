"""Exam schedules on course pages and syllabus bodies.

MCB 354 publishes its four exams on a wiki page linked from the home page --
not in /assignments, not in modules. Read 2026-09-08 after Exam 1 (Sep 16)
had been missing from the calendar for two weeks."""

from datetime import datetime
from pathlib import Path

import httpx

from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.models import Source
from canvas_calendar.pages import extract_assessments
from canvas_calendar.timeutil import CHICAGO

FIX = Path(__file__).parent / "fixtures"


def _local(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=CHICAGO)


def test_mcb354_exam_page_yields_four_timed_exams():
    html = (FIX / "mcb354_exam_information.html").read_text()
    out = extract_assessments(
        html, course="MCB 354", course_id=70420, year=2026, where="page 'Exam Information'"
    )
    got = {a.name: (a.due_at, a.ends_at) for a in out}
    assert got == {
        "Exam 1": (_local(2026, 9, 16, 19), _local(2026, 9, 16, 21)),
        "Exam 2": (_local(2026, 10, 12, 19), _local(2026, 10, 12, 21)),
        "Exam 3": (_local(2026, 11, 5, 19), _local(2026, 11, 5, 21)),
        "Final Exam": (_local(2026, 12, 14, 8), _local(2026, 12, 14, 11)),
    }


def test_repeated_mention_on_the_same_page_is_one_event():
    """The room-assignment heading repeats 'Exam 1 Wednesday, September 16,
    7:00-9:00 pm' below the table."""
    html = (FIX / "mcb354_exam_information.html").read_text()
    names = [a.name for a in extract_assessments(html, "MCB 354", 70420, 2026, "p")]
    assert names.count("Exam 1") == 1


def test_uid_is_stable_and_namespaced():
    html = "<table><tr><td>Exam 1</td><td>September 16, 2026</td><td>7:00-9:00 PM</td></tr></table>"
    a = extract_assessments(html, "MCB 354", 70420, 2026, "p")[0]
    assert a.uid == "cc-pg-70420-exam-1"
    assert a.source is Source.EXTRACTED
    assert a.provenance.startswith("p: Exam 1")


def test_final_exam_in_syllabus_prose():
    """MCB 364: 'The final exam will take place in BH488 on December 15th,
    2026 from 8 to 11am.'"""
    html = "<p>The final exam will take place in BH488 on December 15th, 2026 from 8 to 11am.</p>"
    (a,) = extract_assessments(html, "MCB 364", 69135, 2026, "syllabus")
    assert a.name == "Final Exam"
    assert (a.due_at, a.ends_at) == (_local(2026, 12, 15, 8), _local(2026, 12, 15, 11))


def test_mcb244_final_row_with_slash_date_and_compact_range():
    html = "<p>Final Exam (Chps 14 -17) – Tuesday, 12/15 at 7-10pm</p>"
    (a,) = extract_assessments(html, "MCB 244", 71442, 2026, "syllabus")
    assert (a.due_at, a.ends_at) == (_local(2026, 12, 15, 19), _local(2026, 12, 15, 22))


def test_time_range_crossing_noon_is_read_correctly():
    html = "<p>Exam 2: October 12, 11-1pm</p>"
    (a,) = extract_assessments(html, "C", 1, 2026, "p")
    assert (a.due_at.hour, a.ends_at.hour) == (11, 13)


def test_dateless_exam_line_is_skipped():
    """MCB 244's grading table: 'Exam 1 (Chps 1- 4)' with no date."""
    assert extract_assessments("<p>Exam 1 (Chps 1- 4)</p>", "MCB 244", 1, 2026, "s") == []


def test_no_time_means_end_of_day():
    html = "<p>Midterm Exam: October 14</p>"
    (a,) = extract_assessments(html, "C", 1, 2026, "p")
    assert (a.due_at.hour, a.due_at.minute, a.ends_at) == (23, 59, None)


def test_review_and_practice_lines_are_not_exams():
    html = (
        "<p>Exam 1 review session September 14, 7-9 PM</p>"
        "<p>Practice Exam 1 posted September 10</p>"
        "<p>Exam 1 conflict request due September 9</p>"
        "<p>Exam 1 answer key September 20</p>"
    )
    assert extract_assessments(html, "C", 1, 2026, "p") == []


def test_prose_without_a_named_assessment_is_ignored():
    """'Each exam period will open on a Thursday at 1:00 am (9/10, 10/15 and
    11/12)' names no exam; the student's CBTF reservation is the real date."""
    html = "<p>Each exam period will open on a Thursday at 1:00 am (9/10, 10/15 and 11/12) and close on Sunday.</p>"
    assert extract_assessments(html, "MCB 244", 1, 2026, "s") == []


def test_conflicting_dates_for_one_name_keep_the_first_and_are_reported():
    html = "<p>Exam 1: September 16, 7-9 PM</p><p>Exam 1: September 17, 7-9 PM</p>"
    notes: list[str] = []
    (a,) = extract_assessments(html, "C", 1, 2026, "p", notes=notes)
    assert a.due_at.day == 16
    assert notes and "Exam 1" in notes[0] and "Sep 17" in notes[0]


def test_html_entities_and_nbsp_are_normalised():
    html = "<p>Exam&nbsp;1 &ndash; September&nbsp;16, 2026, 7:00&ndash;9:00&nbsp;PM</p>"
    (a,) = extract_assessments(html, "C", 1, 2026, "p")
    assert a.due_at == _local(2026, 9, 16, 19)


# --- client ----------------------------------------------------------------


def _client(handler):
    return CanvasClient(
        "https://canvas.test/api/v1", "t", http=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_pages_disabled_is_an_empty_list_not_a_crash():
    """Four of six courses 404 on /pages (tab disabled). That is 'nothing
    here', not a failure of the run."""
    c = _client(lambda r: httpx.Response(404, json={"errors": [{"message": "not found"}]}))
    assert c.list_pages(1) == []
    assert c.get_syllabus_body(1) == ""


def test_page_body_is_fetched_per_page():
    def handler(r):
        if r.url.path.endswith("/pages"):
            return httpx.Response(
                200, json=[{"url": "exam-information", "title": "Exam Information"}]
            )
        if r.url.path.endswith("/pages/exam-information"):
            return httpx.Response(
                200,
                json={"url": "exam-information", "title": "Exam Information", "body": "<p>x</p>"},
            )
        return httpx.Response(500)

    c = _client(handler)
    assert c.list_pages(1)[0]["title"] == "Exam Information"
    assert c.get_page(1, "exam-information")["body"] == "<p>x</p>"


def test_401_on_pages_still_raises_token_expired():
    import pytest

    from canvas_calendar.canvas.client import TokenExpired

    c = _client(lambda r: httpx.Response(401))
    with pytest.raises(TokenExpired):
        c.list_pages(1)
