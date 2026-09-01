from datetime import date

from canvas_calendar.doctor import Check, check_term, render


def test_render_marks_failures_and_shows_the_fix():
    out = render([
        Check("Canvas token", True, "expires in 23 days (Sep 24)", ""),
        Check("Term", False, "today is outside 2026 fall", "canvas-calendar setup"),
    ])
    assert "✓" in out and "✗" in out
    assert "expires in 23 days" in out
    assert "canvas-calendar setup" in out


def test_render_omits_the_fix_line_when_a_check_passes():
    out = render([Check("Canvas token", True, "fine", "should not appear")])
    assert "should not appear" not in out


def test_render_reports_the_failure_count():
    out = render([Check("a", False, "x", ""), Check("b", False, "y", ""),
                  Check("c", True, "z", "")])
    assert "2 problem(s) found" in out


def test_render_says_so_when_everything_passes():
    assert "All checks passed" in render([Check("a", True, "x", "")])


def _term():
    from canvas_calendar.terms import Term

    return Term(year=2026, season="fall", start=date(2026, 8, 24),
                end=date(2026, 12, 9), holidays=())


def test_term_check_passes_inside_the_term():
    c = check_term(_term(), today=date(2026, 9, 15))
    assert c.ok is True
    assert "days left" in c.detail


def test_term_check_fails_when_today_is_out_of_range():
    """A spring install with fall dates syncs a silently wrong schedule."""
    c = check_term(_term(), today=date(2027, 2, 1))
    assert c.ok is False
    assert "outside" in c.detail
    assert "setup" in c.fix


def test_term_check_fails_before_the_term_starts_too():
    assert check_term(_term(), today=date(2026, 7, 1)).ok is False
