"""Dates for Learn@Illinois Moodle quizzes embedded in Canvas.

MCB 364 runs its Assignments and Pre-Lab Quizzes as Moodle quizzes launched
over LTI 1.3. Canvas holds no date for any of them -- due_at, unlock_at and
lock_at are all null -- and the course does not appear when the student logs
in to learn.illinois.edu directly, because the launch provisions a separate
account. The deadline exists only on the Moodle quiz page.

So the page is read the way the browser reaches it: Canvas's sessionless
launch URL, then the LTI handshake (two form POSTs), then /mod/quiz/view.php.
The Canvas token is the only credential. Verified live 2026-09-28; see
docs/superpowers/specs/2026-09-28-moodle-lti-dates-design.md.

The quiz page carries the "Attempt quiz" form. Only the two handshake
endpoints may ever be submitted, so nothing here can start an attempt.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx

from canvas_calendar.canvas.client import CanvasClient, TokenExpired
from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import CHICAGO

MOODLE_HOST = "lti.learn.illinois.edu"
CACHE_PATH = Path.home() / ".config" / "canvas-calendar" / "moodle_cache.json"
PROVENANCE = "Moodle quiz page (Learn@Illinois)"

# The handshake's two form targets. Anything else -- above all
# /mod/quiz/startattempt.php, which is on the quiz page -- is never submitted.
_ALLOWED_POSTS = {"/enrol/lti/login.php", "/enrol/lti/launch.php"}
_DESTINATION = re.compile(r"^/mod/\w+/view\.php$")
_MAX_HOPS = 6

# Without a browser User-Agent and Accept, Canvas answers the launch URL 400.
_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml",
}

# "Opened:" is deliberately absent: it is when the quiz became available.
_DEADLINE = re.compile(
    r"\b(?:Closes|Closed|Due):\s*"
    r"([A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, \d{4}, \d{1,2}:\d{2} [AP]M)"
)
_FINISHED = re.compile(r"\bStatus Finished\b")
_NON_TEXT = re.compile(r"<(script|style)\b.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class MoodleUnavailable(RuntimeError):
    """The page could not be reached or did not look as expected. Callers fall
    back to the last good read; this is never a reason to drop an event."""


@dataclass(frozen=True)
class MoodleQuiz:
    closes: datetime | None
    finished: bool


def is_moodle(raw: dict) -> bool:
    """Matched on the launch link rather than the course, so any course using
    the same Moodle service is covered."""
    tag = raw.get("external_tool_tag_attributes") or {}
    return urlsplit(tag.get("url") or "").netloc == MOODLE_HOST


def _text(page: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub(" ", _NON_TEXT.sub(" ", page))))


def parse_quiz_page(page: str) -> MoodleQuiz:
    """Read the deadline and completion state from a view.php page.

    No date line means closes=None -- never a guessed date. Moodle renders
    times in the account's zone, which for these LTI accounts is the site
    default, America/Chicago.
    """
    text = _text(page)
    m = _DEADLINE.search(text)
    closes = None
    if m:
        closes = datetime.strptime(m.group(1), "%A, %B %d, %Y, %I:%M %p").replace(tzinfo=CHICAGO)
    return MoodleQuiz(closes=closes, finished=bool(_FINISHED.search(text)))


class _Forms(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[tuple[str, str, dict[str, str]]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if tag == "form":
            self._current = {}
            self.forms.append((a.get("action", ""), a.get("method", "get").lower(), self._current))
        elif tag == "input" and self._current is not None and a.get("name"):
            self._current[a["name"]] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "form":
            self._current = None


def _forms(page: str) -> list[tuple[str, str, dict[str, str]]]:
    parser = _Forms()
    parser.feed(page)
    return parser.forms


class MoodleReader:
    def __init__(
        self, client: CanvasClient, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = client
        self._transport = transport  # tests inject a MockTransport here
        self.down: str | None = None

    def read(self, course_id: int, assignment_id: int, tool_id: int) -> MoodleQuiz:
        if self.down:
            raise MoodleUnavailable(f"unreachable earlier this run ({self.down})")
        try:
            launch = self._client.sessionless_launch(course_id, assignment_id, tool_id)
        except TokenExpired:
            raise
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            raise MoodleUnavailable(f"Canvas launch: {exc}") from exc

        # A client per launch: its cookie jar is that LTI session, carried
        # across every redirect in the handshake and discarded afterwards.
        try:
            with httpx.Client(
                transport=self._transport, headers=_BROWSER, follow_redirects=True, timeout=20
            ) as http:
                return self._walk(http, launch)
        except httpx.TransportError as exc:
            self.down = type(exc).__name__
            raise MoodleUnavailable(self.down) from exc
        except httpx.HTTPError as exc:
            raise MoodleUnavailable(str(exc)) from exc

    def _walk(self, http: httpx.Client, url: str) -> MoodleQuiz:
        resp = http.get(url)
        for _ in range(_MAX_HOPS):
            if resp.status_code != 200:
                raise MoodleUnavailable(f"HTTP {resp.status_code} at {resp.url.path}")
            if resp.url.host == MOODLE_HOST and _DESTINATION.match(resp.url.path):
                return parse_quiz_page(resp.text)
            posts = [
                (urljoin(str(resp.url), html.unescape(action)), fields)
                for action, method, fields in _forms(resp.text)
                if method == "post"
            ]
            step = next(
                ((target, fields) for target, fields in posts
                 if urlsplit(target).path in _ALLOWED_POSTS),
                None,
            )
            if step is None:
                raise MoodleUnavailable(f"no handshake form at {resp.url.host}{resp.url.path}")
            target, fields = step
            resp = http.post(target, data=fields)
        raise MoodleUnavailable("launch did not reach a Moodle activity page")


class MoodleCache:
    """Last good read per assignment. What keeps an event on the calendar when
    Moodle is down: an item that reverted to undated would be pruned."""

    def __init__(self, path: Path = CACHE_PATH) -> None:
        self._path = Path(path)
        try:
            self._data: dict[str, dict] = json.loads(self._path.read_text())
        except (OSError, ValueError):
            self._data = {}

    def get(self, assignment_id) -> tuple[MoodleQuiz, datetime] | None:
        entry = self._data.get(str(assignment_id))
        if not entry:
            return None
        try:
            closes = datetime.fromisoformat(entry["closes"]) if entry.get("closes") else None
            return (
                MoodleQuiz(closes=closes, finished=bool(entry.get("finished"))),
                datetime.fromisoformat(entry["read_at"]),
            )
        except (KeyError, ValueError):
            return None

    def put(self, assignment_id, quiz: MoodleQuiz, read_at: datetime) -> None:
        self._data[str(assignment_id)] = {
            "closes": quiz.closes.isoformat() if quiz.closes else None,
            "finished": quiz.finished,
            "read_at": read_at.isoformat(),
        }

    def save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, indent=2) + "\n")


def resolve_moodle(
    items: list[Assignment],
    raw: list[dict],
    course_id: int,
    reader,
    cache: MoodleCache,
    notes: list[str],
    now: datetime | None = None,
) -> list[Assignment]:
    """Date undated Moodle items from their quiz page, in place.

    Runs before module-title extraction so an exact Moodle time outranks a
    date guessed from a heading. A Canvas due date, or work Canvas already
    reports complete, is left alone and costs no launch.
    """
    now = now or datetime.now(CHICAGO)
    links = {r["id"]: r for r in raw if is_moodle(r)}
    for a in items:
        r = links.get(a.canvas_id)
        if r is None or a.source is not Source.UNRESOLVED or a.completed:
            continue
        tool_id = (r.get("external_tool_tag_attributes") or {}).get("content_id")
        try:
            if not tool_id:
                raise MoodleUnavailable("the Canvas link names no tool")
            quiz = reader.read(course_id, a.canvas_id, tool_id)
            if quiz.closes is None and not quiz.finished:
                raise MoodleUnavailable("no Closes/Due line on the page")
            cache.put(a.canvas_id, quiz, now)
        except MoodleUnavailable as exc:
            cached = cache.get(a.canvas_id)
            if cached is None:
                notes.append(f"Moodle unavailable for {a.course} {a.name} ({exc}); left undated")
                continue
            quiz, read_at = cached
            notes.append(
                f"Moodle unavailable for {a.course} {a.name} ({exc}); "
                f"using what it said on {read_at.astimezone(CHICAGO):%b %d %H:%M}"
            )
        if quiz.closes is not None:
            a.due_at = quiz.closes
            a.source = Source.MOODLE
            a.provenance = PROVENANCE
        a.completed = a.completed or quiz.finished
    return items
