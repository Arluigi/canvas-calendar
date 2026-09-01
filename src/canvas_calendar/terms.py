"""Academic term bounds. Pure: no IO, no config reads.

These were compiled into meetings.py as Fall 2026 constants. That is correct
for exactly one semester, and wrong silently -- a spring install would emit a
fall meeting schedule, or none at all, without complaining. Making the term a
value the caller passes lets config override it while keeping meetings.py
free of IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Term:
    year: int
    season: str
    start: date
    end: date
    holidays: tuple[date, ...]

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


# Fall 2026, University of Illinois Urbana-Champaign registrar calendar.
# Cross-checked against MCB 320's module titles, which jump November 20 -> 30.
DEFAULT_TERM = Term(
    year=2026,
    season="fall",
    start=date(2026, 8, 24),
    end=date(2026, 12, 9),
    holidays=(
        date(2026, 9, 7),  # Labor Day
        *[date(2026, 11, d) for d in range(21, 30)],  # Fall Break, Nov 21-29
    ),
)


def term_from_config(block: dict | None) -> Term:
    """Build a Term from the config `term` block, or return the default."""
    if not block:
        return DEFAULT_TERM
    start = date.fromisoformat(block["start"])
    end = date.fromisoformat(block["end"])
    if start > end:
        raise ValueError(f"term starts after it ends: {start} > {end}")
    return Term(
        year=int(block["year"]),
        season=str(block["season"]),
        start=start,
        end=end,
        holidays=tuple(date.fromisoformat(d) for d in block.get("holidays", [])),
    )
