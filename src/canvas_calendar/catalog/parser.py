"""Parse UIUC Course Explorer section XML.

Date format note: the API emits startDate as "08-24-26Z" (MM-DD-YY with a
trailing Z that is not a timezone). Parse it explicitly rather than trusting
a generic ISO parser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from xml.etree import ElementTree as ET

from canvas_calendar.models import Meeting


@dataclass
class Section:
    meetings: list[Meeting] = field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None


def _text(node, tag: str, default: str = "") -> str:
    found = node.find(tag)
    return (found.text or default).strip() if found is not None else default


def _parse_uiuc_date(raw: str) -> date | None:
    raw = raw.strip().rstrip("Z")
    if not raw:
        return None
    # A term start/end date carries no time or zone; .date() is the whole point.
    return datetime.strptime(raw, "%m-%d-%y").date()  # noqa: DTZ007


def parse_section(xml: str) -> Section:
    root = ET.fromstring(xml)
    meetings: list[Meeting] = []
    for node in root.iter("meeting"):
        instructors = [i.text or "" for i in node.iter("instructor")]
        type_node = node.find("type")
        meetings.append(
            Meeting(
                days=_text(node, "daysOfTheWeek"),
                start=_text(node, "start"),
                end=_text(node, "end"),
                building=_text(node, "buildingName"),
                room=_text(node, "roomNumber"),
                kind=(type_node.text or "").strip() if type_node is not None else "",
                instructor="; ".join(i.strip() for i in instructors if i.strip()),
            )
        )
    return Section(
        meetings=meetings,
        start_date=_parse_uiuc_date(_text(root, "startDate")),
        end_date=_parse_uiuc_date(_text(root, "endDate")),
    )
