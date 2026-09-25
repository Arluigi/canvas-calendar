"""One piece of work, one event.

The pipeline now has four ways to learn about the same exam or quiz: a Canvas
assignment, a hand entry in overrides.json, a dated module SubHeader, and a
line on a course page or syllabus. They arrive with different UIDs, so the
diff cannot see that they are the same thing.

Measured 2026-09-08: 'Ch5 Adaptive Quiz' was hand-entered on 2026-08-31 when
Canvas had no such assignment. Canvas published 'Ch5 Adaptive Quiz: Protein
Function' later. For a week both were on the calendar and in the debrief; when
the Canvas one was cleared as completed, the hand entry stayed, because
nothing links a manual addition to the work it describes.

Precedence, highest first:

    Canvas assignment with a due date   -- has an id and completion tracking
    overrides.json addition             -- hand-authored; carries room/section
    module SubHeader                    -- instructor-authored schedule text
    course page / syllabus line         -- prose; the widest net, least exact

Two items of the SAME tier are never merged: two Canvas assignments are two
assignments whatever their names. An undated Canvas assignment supersedes
nothing -- if the hand entry is the only thing putting the work on a calendar,
it stays. Every retirement is logged so it is visible in the digest.
"""

from __future__ import annotations

import re

from canvas_calendar.models import Assignment

_WS = re.compile(r"\s+")
# What may follow a shorter name inside a longer one for them to be the same
# work: a subtitle separator or an aside. Not a digit -- 'Exam 1' is not
# 'Exam 10'.
_BOUNDARY = re.compile(r"[\s:;,\-–—(\[/]")

_TIER = {"": 3, "man-": 2, "mi-": 1, "pg-": 0}
_LABEL = {
    "man-": "your overrides.json entry",
    "mi-": "module SubHeader",
    "pg-": "course page",
}


def _norm(name: str) -> str:
    return _WS.sub(" ", name.strip().lower())


def same_work(a: str, b: str) -> bool:
    """Equal after normalisation, or one is the other's prefix at a boundary.

    'Ch5 Adaptive Quiz' vs 'Ch5 Adaptive Quiz: Protein Function' is the same
    work. 'Exam 1' vs 'Practice Exam 1' is not: a prefix has to start at the
    start, because the qualifier is the whole difference.
    """
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    short, long = (x, y) if len(x) < len(y) else (y, x)
    return long.startswith(short) and bool(_BOUNDARY.match(long[len(short)]))


def _tier(a: Assignment) -> int:
    if a.namespace == "" and a.due_at is None:
        return -1  # undated Canvas: participates in nothing
    return _TIER.get(a.namespace, -1)


def _describe(a: Assignment) -> str:
    if a.namespace == "":
        return f"Canvas assignment {a.canvas_id} '{a.name}'"
    return f"{_LABEL.get(a.namespace, a.namespace)} '{a.name}'"


def dedupe(items: list[Assignment], log: list[str]) -> list[Assignment]:
    """Drop lower-tier twins. Order is preserved; input is not mutated."""
    dropped: set[int] = set()
    noted: set[tuple[int, int]] = set()

    for i, a in enumerate(items):
        for j in range(i + 1, len(items)):
            b = items[j]
            if a.course != b.course or not same_work(a.name, b.name):
                continue
            ta, tb = _tier(a), _tier(b)
            if ta == tb:
                continue
            if -1 in (ta, tb):
                # Undated Canvas assignment beside a hand entry: keep the
                # entry, but say the two exist so the operator can decide.
                canvas, other = (a, b) if ta == -1 else (b, a)
                key = (id(canvas), id(other))
                if other.namespace == "man-" and key not in noted:
                    noted.add(key)
                    log.append(
                        f"{other.course} {other.name}: Canvas lists it as assignment "
                        f"{canvas.canvas_id} '{canvas.name}' with no due date; "
                        f"keeping your overrides.json entry"
                    )
                continue
            winner, loser = (a, b) if ta > tb else (b, a)
            if id(loser) in dropped:
                continue
            dropped.add(id(loser))
            if loser.namespace == "man-" and winner.namespace == "":
                log.append(
                    f"{loser.course} {loser.name}: superseded by {_describe(winner)} "
                    f"-- remove it from overrides.json"
                )
            else:
                log.append(
                    f"{loser.course} {loser.name}: {_describe(loser)} retired in favour "
                    f"of {_describe(winner)}"
                )

    return [a for a in items if id(a) not in dropped]
