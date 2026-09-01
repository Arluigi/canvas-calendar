from pathlib import Path

from canvas_calendar.catalog.parser import parse_section

FIXTURE = Path(__file__).parent / "fixtures" / "mcb244_section.xml"


def test_parses_real_mcb244_section():
    section = parse_section(FIXTURE.read_text())
    assert len(section.meetings) == 1
    m = section.meetings[0]
    assert m.days == "TR"
    assert m.start == "02:00PM"
    assert m.end == "03:20PM"
    assert m.building == "Foellinger Auditorium"
    assert m.room == "AUD"
    assert m.kind == "Lecture"
    assert "Garcia" in m.instructor


def test_parses_section_date_range():
    section = parse_section(FIXTURE.read_text())
    assert section.start_date.isoformat() == "2026-08-24"
    assert section.end_date.isoformat() == "2026-12-09"


def test_handles_section_with_no_meetings():
    xml = '<?xml version="1.0"?><ns2:section xmlns:ns2="http://rest.cis.illinois.edu"/>'
    section = parse_section(xml)
    assert section.meetings == []


def test_meeting_weekdays_from_real_data():
    """TR must expand to Tue/Thu -- this drives recurring event generation."""
    section = parse_section(FIXTURE.read_text())
    assert section.meetings[0].weekdays() == [1, 3]
