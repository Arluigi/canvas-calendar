"""The wizard's testable core. `main()` is interactive and not tested here.

Every test writes only to tmp_path -- setup must never touch the real config
during a test run.
"""

import json
from datetime import date

from canvas_calendar.setup_wizard import choose_term, write_config


def test_choose_term_picks_the_one_containing_today():
    t = choose_term(today=date(2026, 10, 1))
    assert (t["year"], t["season"]) == (2026, "fall")


def test_choose_term_picks_the_next_one_when_between_terms():
    t = choose_term(today=date(2026, 12, 20))
    assert (t["year"], t["season"]) == (2027, "spring")


def test_choose_term_spring_dates_match_the_registrar():
    """Verified against registrar.illinois.edu on 2026-08-31."""
    t = choose_term(today=date(2027, 3, 1))
    assert t["start"] == "2027-01-19"
    assert t["end"] == "2027-05-05"
    assert "2027-03-15" in t["holidays"]      # spring break Monday
    assert "2027-01-18" not in t["holidays"]  # MLK is before instruction starts


def test_chosen_term_parses_into_a_Term():
    from canvas_calendar.terms import term_from_config

    for today in (date(2026, 10, 1), date(2027, 3, 1)):
        assert term_from_config(choose_term(today=today)).year


def test_write_config_preserves_unrelated_keys(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"client_id": "abc", "debrief_to": "x@y.edu"}))

    changes = write_config({"calendar_backend": "eventkit"}, path=p)

    after = json.loads(p.read_text())
    assert after["client_id"] == "abc"
    assert after["debrief_to"] == "x@y.edu"
    assert after["calendar_backend"] == "eventkit"
    assert any("calendar_backend" in c for c in changes)


def test_write_config_reports_an_overwrite_rather_than_doing_it_silently(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"calendar_backend": "outlook"}))

    changes = write_config({"calendar_backend": "eventkit"}, path=p)

    assert any("outlook" in c and "eventkit" in c for c in changes), changes


def test_write_config_is_silent_about_an_unchanged_value(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"calendar_backend": "outlook"}))
    assert write_config({"calendar_backend": "outlook"}, path=p) == []


def test_write_config_creates_the_file_when_absent(tmp_path):
    p = tmp_path / "sub" / "config.json"
    write_config({"calendar_backend": "eventkit"}, path=p)
    assert json.loads(p.read_text())["calendar_backend"] == "eventkit"


def test_write_config_output_is_valid_json(tmp_path):
    p = tmp_path / "config.json"
    write_config({"term": {"year": 2027}, "calendar_backend": "eventkit"}, path=p)
    json.loads(p.read_text())  # must not raise
