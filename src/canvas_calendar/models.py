"""Core dataclasses. No I/O, no side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# e.g. mcb_244_120268_262964 -> subject mcb, number 244, term 120268
_COURSE_CODE = re.compile(r"^([a-z]{2,4})_(\d{3})_(\d{6})_\d+$", re.IGNORECASE)

_DAY_CODES = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5, "U": 6}


class Source(str, Enum):
    CANVAS = "canvas"  # instructor-set due date
    EXTRACTED = "extracted"  # parsed from module title / SubHeader text
    UNRESOLVED = "unresolved"  # no date available; digest only


@dataclass(frozen=True)
class CourseRef:
    subject: str
    number: str
    term_id: str

    @classmethod
    def from_canvas_code(cls, code: str) -> CourseRef:
        m = _COURSE_CODE.match(code)
        if not m:
            raise ValueError(f"unrecognized course code: {code!r}")
        subject, number, term_id = m.groups()
        return cls(subject=subject.upper(), number=number, term_id=term_id)


@dataclass(frozen=True)
class Meeting:
    days: str
    start: str
    end: str
    building: str
    room: str
    kind: str
    instructor: str

    def weekdays(self) -> list[int]:
        """Course Explorer day codes -> Python weekday ints (Mon=0)."""
        return [_DAY_CODES[c] for c in self.days if c in _DAY_CODES]


@dataclass
class Assignment:
    canvas_id: int
    name: str
    points: float
    due_at: datetime | None
    course: str
    source: Source = Source.CANVAS
    provenance: str = ""  # for extracted dates: the text the date came from
    module: str = ""

    @property
    def uid(self) -> str:
        """Stable calendar UID. The cc- prefix is what the never-delete-foreign-
        events check tests against, so it must never change."""
        return f"cc-{self.canvas_id}"
