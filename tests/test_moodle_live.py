"""Launches a real Moodle quiz page through Canvas. Run explicitly:
uv run pytest -m live

Read-only: the reader stops at view.php and can only submit the two LTI
handshake forms. Pinned to MCB 364 Assignment 3 (Fall 2026); update the ids
when that course ends.
"""

from datetime import datetime

import pytest

from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.config import load_canvas_credentials
from canvas_calendar.moodle import MoodleReader
from canvas_calendar.timeutil import CHICAGO

pytestmark = pytest.mark.live

COURSE, ASSIGNMENT, TOOL = 69135, 1605584, 7319


def test_reads_assignment_3_close_time():
    base, token = load_canvas_credentials()
    quiz = MoodleReader(CanvasClient(base, token)).read(COURSE, ASSIGNMENT, TOOL)
    assert quiz.closes == datetime(2026, 9, 30, 9, 0, tzinfo=CHICAGO)
