"""Keep deadline events from sitting on top of class meetings.

43 of 50 timed assignments landed exactly on a class start time, because
"due at the start of class" is how most of them are set. Rendered as a
30-minute block starting at 2:00 PM, each one overlapped its own lecture and
both events collapsed to half width -- the calendar became unreadable on the
days with the most work.

Shifting the *start* 15 minutes earlier is not enough: a 30-minute block from
1:45 still runs to 2:15, fifteen minutes into the lecture. The event has to
occupy the gap immediately BEFORE the meeting, ending exactly as it begins.

`due_at` is never touched. The deadline is a fact reported by Canvas; only the
rectangle drawn on a calendar moves, and the event body says so.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta

from canvas_calendar.models import Assignment
from canvas_calendar.timeutil import is_end_of_day, to_local

DEFAULT_OFFSET_MINUTES = 15


@dataclass(frozen=True)
class MeetingWindow:
    """One weekly class block: weekday (Mon=0), local start and end."""

    weekday: int
    start: time
    end: time
    title: str

    def contains(self, moment: datetime) -> bool:
        return moment.weekday() == self.weekday and self.start <= moment.time() <= self.end


def windows_from_config(rows: list[dict] | None) -> list[MeetingWindow]:
    out: list[MeetingWindow] = []
    for r in rows or []:
        out.append(
            MeetingWindow(
                weekday=int(r["weekday"]),
                start=time.fromisoformat(r["start"]),
                end=time.fromisoformat(r["end"]),
                title=str(r.get("title", "class")),
            )
        )
    return out


def apply_meeting_offsets(
    items: list[Assignment],
    windows: list[MeetingWindow],
    *,
    minutes: int = DEFAULT_OFFSET_MINUTES,
) -> list[str]:
    """Give colliding assignments a display window before their meeting.

    Returns a human-readable line per adjustment, so the run can report what
    it moved rather than quietly relocating events.
    """
    notes: list[str] = []
    if not windows or minutes <= 0:
        return notes

    for a in items:
        if a.due_at is None or is_end_of_day(a.due_at) or a.digest_only:
            continue
        local = to_local(a.due_at)
        for w in windows:
            if not w.contains(local):
                continue
            block_start = local.replace(
                hour=w.start.hour, minute=w.start.minute, second=0, microsecond=0
            )
            a.display_start = block_start - timedelta(minutes=minutes)
            a.display_end = block_start
            a.display_reason = (
                f"Moved to {a.display_start:%-I:%M %p} so it does not sit on top of "
                f"{w.title}. Actually due {local:%-I:%M %p}."
            )
            notes.append(
                f"{a.course} {a.name[:34]}: shown {a.display_start:%-I:%M %p}"
                f"-{block_start:%-I:%M %p} (clears {w.title})"
            )
            break
    return notes
