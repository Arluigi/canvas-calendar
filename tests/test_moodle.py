from datetime import datetime
from pathlib import Path

import httpx
import pytest

from canvas_calendar.canvas.client import CanvasClient, TokenExpired
from canvas_calendar.models import Assignment, Source
from canvas_calendar.moodle import (
    MoodleCache,
    MoodleQuiz,
    MoodleReader,
    MoodleUnavailable,
    is_moodle,
    parse_quiz_page,
    resolve_moodle,
)
from canvas_calendar.timeutil import CHICAGO

FIXTURES = Path(__file__).parent / "fixtures"
LTI = "https://lti.learn.illinois.edu"
CANVAS = "https://canvas.test"


def _raw(aid=5, url=f"{LTI}/enrol/lti/launch.php", tool=7319, due=None):
    return {
        "id": aid,
        "due_at": due,
        "external_tool_tag_attributes": {"url": url, "content_id": tool},
    }


# -- is_moodle ---------------------------------------------------------------


def test_is_moodle_matches_on_the_launch_host():
    assert is_moodle(_raw())


def test_is_moodle_rejects_other_tools_and_plain_assignments():
    assert not is_moodle(_raw(url="https://mcgraw.example/lti"))
    assert not is_moodle({"id": 1, "external_tool_tag_attributes": None})
    assert not is_moodle({"id": 1})


# -- parse_quiz_page ---------------------------------------------------------


def test_open_quiz_reads_closes_and_is_not_finished():
    q = parse_quiz_page((FIXTURES / "moodle_quiz_open.html").read_text())
    assert q.closes == datetime(2026, 9, 30, 9, 0, tzinfo=CHICAGO)
    assert q.finished is False


def test_finished_quiz_reads_closed_and_is_finished():
    q = parse_quiz_page((FIXTURES / "moodle_quiz_finished.html").read_text())
    assert q.closes == datetime(2026, 9, 2, 9, 0, tzinfo=CHICAGO)
    assert q.finished is True


def test_opened_line_is_not_mistaken_for_the_deadline():
    html = "<strong>Opened:</strong> Wednesday, September 23, 2026, 2:00 PM"
    assert parse_quiz_page(html).closes is None


def test_due_line_is_accepted():
    html = "<div><strong>Due:</strong> Friday, October 2, 2026, 11:59 PM</div>"
    assert parse_quiz_page(html).closes == datetime(2026, 10, 2, 23, 59, tzinfo=CHICAGO)


def test_page_without_a_date_line_returns_none_not_a_guess():
    assert parse_quiz_page("<p>Attempt quiz</p>") == MoodleQuiz(closes=None, finished=False)


def test_in_progress_attempt_is_not_finished():
    html = '<th scope="row">Status</th><td class="cell">In progress</td>'
    assert parse_quiz_page(html).finished is False


def test_script_text_cannot_supply_a_date():
    html = "<script>var s='Closes: Monday, January 4, 2027, 9:00 AM';</script>"
    assert parse_quiz_page(html).closes is None


# -- MoodleReader ------------------------------------------------------------


def _form(action, **fields):
    inputs = "".join(f'<input type="hidden" name="{k}" value="{v}">' for k, v in fields.items())
    return f'<html><body><form method="post" action="{action}">{inputs}</form></body></html>'


def _launch_handler(final_html, seen, final_path="/mod/quiz/view.php"):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.host, request.url.path))
        path = request.url.path
        if path.endswith("/external_tools/sessionless_launch"):
            return httpx.Response(200, json={"url": f"{CANVAS}/courses/1/assignments/5?st=x"})
        if request.url.host == "canvas.test" and path == "/courses/1/assignments/5":
            return httpx.Response(200, html=_form(f"{LTI}/enrol/lti/login.php", iss="c"))
        if path == "/enrol/lti/login.php":
            return httpx.Response(302, headers={"Location": f"{CANVAS}/api/lti/authorize?x=1"})
        if path == "/api/lti/authorize":
            return httpx.Response(200, html=_form(f"{LTI}/enrol/lti/launch.php", id_token="t"))
        if path == "/enrol/lti/launch.php":
            return httpx.Response(303, headers={"Location": f"{LTI}{final_path}?id=1"})
        if path == final_path:
            return httpx.Response(200, html=final_html)
        return httpx.Response(599, text=f"unexpected {request.method} {path}")

    return handler


def _reader(handler):
    transport = httpx.MockTransport(handler)
    client = CanvasClient(f"{CANVAS}/api/v1", "tok", http=httpx.Client(transport=transport))
    return MoodleReader(client, transport=transport)


def test_reader_follows_the_lti_launch_to_the_quiz_page():
    seen: list = []
    page = (FIXTURES / "moodle_quiz_open.html").read_text()
    q = _reader(_launch_handler(page, seen)).read(1, 5, 7319)
    assert q.closes == datetime(2026, 9, 30, 9, 0, tzinfo=CHICAGO)
    posts = [p for m, _, p in seen if m == "POST"]
    assert posts == ["/enrol/lti/login.php", "/enrol/lti/launch.php"]


def test_reader_never_submits_the_start_attempt_form():
    """The quiz page carries startattempt.php. Landing anywhere but view.php
    with only that form on the page must raise, not click it."""
    seen: list = []
    page = _form(f"{LTI}/mod/quiz/startattempt.php", cmid="1", sesskey="s")
    reader = _reader(_launch_handler(page, seen, final_path="/mod/quiz/summary.php"))
    with pytest.raises(MoodleUnavailable):
        reader.read(1, 5, 7319)
    assert not any(p.endswith("startattempt.php") for _, _, p in seen)


def test_reader_raises_token_expired_on_canvas_401():
    def handler(request):
        return httpx.Response(401, json={"errors": [{"message": "Invalid access token."}]})

    with pytest.raises(TokenExpired):
        _reader(handler).read(1, 5, 7319)


def test_reader_goes_down_after_a_transport_failure():
    """Wednesday maintenance: one timeout, then fail fast for the rest."""
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("sessionless_launch"):
            return httpx.Response(200, json={"url": f"{CANVAS}/courses/1/assignments/5"})
        if request.url.host == "canvas.test":
            return httpx.Response(200, html=_form(f"{LTI}/enrol/lti/login.php", a="b"))
        raise httpx.ConnectTimeout("maintenance", request=request)

    reader = _reader(handler)
    with pytest.raises(MoodleUnavailable):
        reader.read(1, 5, 7319)
    before = len(calls)
    with pytest.raises(MoodleUnavailable):
        reader.read(1, 6, 7319)
    assert len(calls) == before


def test_reader_rejects_a_canvas_error_body():
    def handler(request):
        return httpx.Response(200, json={"errors": {"external_tool": "no match"}})

    with pytest.raises(MoodleUnavailable):
        _reader(handler).read(1, 5, 7319)


# -- resolve_moodle ----------------------------------------------------------


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=CHICAGO)
CLOSES = datetime(2026, 9, 30, 9, 0, tzinfo=CHICAGO)


class FakeReader:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    def read(self, course_id, assignment_id, tool_id):
        self.calls.append(assignment_id)
        if self.error:
            raise self.error
        return self.result


def _a(aid=5, due=None, completed=False):
    return Assignment(
        canvas_id=aid, name="Assignment 3", points=50.0, due_at=due, course="MCB 364",
        source=Source.CANVAS if due else Source.UNRESOLVED, completed=completed,
    )


def test_resolve_fills_an_undated_moodle_item(tmp_path):
    cache = MoodleCache(tmp_path / "c.json")
    items = resolve_moodle(
        [_a()], [_raw()], 1, FakeReader(MoodleQuiz(CLOSES, False)), cache, [], now=NOW
    )
    a = items[0]
    assert a.due_at == CLOSES
    assert a.source is Source.MOODLE
    assert "Moodle" in a.provenance
    assert a.completed is False


def test_resolve_marks_finished_quiz_complete(tmp_path):
    cache = MoodleCache(tmp_path / "c.json")
    a = resolve_moodle(
        [_a()], [_raw()], 1, FakeReader(MoodleQuiz(CLOSES, True)), cache, [], now=NOW
    )[0]
    assert a.completed is True


def test_resolve_leaves_canvas_dated_and_completed_items_unread(tmp_path):
    reader = FakeReader(MoodleQuiz(CLOSES, False))
    canvas_due = datetime(2026, 10, 1, 23, 59, tzinfo=CHICAGO)
    items = [_a(aid=5, due=canvas_due), _a(aid=6, completed=True)]
    resolve_moodle(items, [_raw(5), _raw(6)], 1, reader, MoodleCache(tmp_path / "c.json"), [],
                   now=NOW)
    assert reader.calls == []
    assert items[0].due_at == canvas_due and items[0].source is Source.CANVAS


def test_resolve_ignores_non_moodle_items(tmp_path):
    reader = FakeReader(MoodleQuiz(CLOSES, False))
    raw = [{"id": 5, "external_tool_tag_attributes": None}]
    a = resolve_moodle([_a()], raw, 1, reader, MoodleCache(tmp_path / "c.json"), [], now=NOW)[0]
    assert reader.calls == [] and a.source is Source.UNRESOLVED


def test_resolve_falls_back_to_the_cache_when_moodle_is_down(tmp_path):
    """Reverting to UNRESOLVED would let the prune pass delete the event."""
    path = tmp_path / "c.json"
    ok = MoodleCache(path)
    resolve_moodle([_a()], [_raw()], 1, FakeReader(MoodleQuiz(CLOSES, False)), ok, [], now=NOW)
    ok.save()

    notes: list[str] = []
    a = resolve_moodle(
        [_a()], [_raw()], 1, FakeReader(error=MoodleUnavailable("timeout")),
        MoodleCache(path), notes, now=NOW,
    )[0]
    assert a.due_at == CLOSES and a.source is Source.MOODLE
    assert any("Moodle unavailable" in n and "Sep 28" in n for n in notes)


def test_resolve_leaves_item_undated_with_no_cache(tmp_path):
    notes: list[str] = []
    a = resolve_moodle(
        [_a()], [_raw()], 1, FakeReader(error=MoodleUnavailable("timeout")),
        MoodleCache(tmp_path / "c.json"), notes, now=NOW,
    )[0]
    assert a.source is Source.UNRESOLVED and a.due_at is None
    assert any("left undated" in n for n in notes)


def test_page_without_a_date_uses_the_cache_rather_than_clearing(tmp_path):
    path = tmp_path / "c.json"
    first = MoodleCache(path)
    resolve_moodle([_a()], [_raw()], 1, FakeReader(MoodleQuiz(CLOSES, False)), first, [], now=NOW)
    first.save()
    a = resolve_moodle(
        [_a()], [_raw()], 1, FakeReader(MoodleQuiz(None, False)), MoodleCache(path), [],
        now=NOW,
    )[0]
    assert a.due_at == CLOSES


def test_missing_tool_id_is_reported_not_crashed(tmp_path):
    notes: list[str] = []
    raw = [_raw(tool=None)]
    a = resolve_moodle([_a()], raw, 1, FakeReader(MoodleQuiz(CLOSES, False)),
                       MoodleCache(tmp_path / "c.json"), notes, now=NOW)[0]
    assert a.source is Source.UNRESOLVED and notes


def test_cache_survives_a_corrupt_file(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json")
    assert MoodleCache(path).get(5) is None
