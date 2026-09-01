from collections import Counter
from datetime import datetime, timedelta

from canvas_calendar.daily import write_digest
from canvas_calendar.diff import Action, PlanEntry
from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import CHICAGO


def _a(name, course="MCB 244", days=2, source=Source.CANVAS, digest_only=False,
       completed=False):
    return Assignment(
        canvas_id=1, name=name, points=1.0, course=course, source=source,
        digest_only=digest_only, completed=completed,
        due_at=datetime.now(CHICAGO) + timedelta(days=days),
    )


def _digest(plan, counts=None, errors=None, applied=None, tmp_path=None, monkeypatch=None):
    if tmp_path is not None:
        monkeypatch.setattr("canvas_calendar.daily.LOG_DIR", tmp_path)
        monkeypatch.setattr("canvas_calendar.daily.DIGEST_PATH", tmp_path / "digest.md")
    write_digest(plan, counts or Counter(), errors or [], applied or [])
    return (tmp_path / "digest.md").read_text()


def test_reports_what_changed(tmp_path, monkeypatch):
    plan = [PlanEntry(Action.CREATE, "cc-1", _a("New Quiz"))]
    out = _digest(plan, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Changed since last run" in out
    assert "New Quiz" in out
    assert "**create**" in out


def test_says_so_when_nothing_changed(tmp_path, monkeypatch):
    out = _digest([], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "nothing changed" in out


def test_lists_upcoming_week(tmp_path, monkeypatch):
    plan = [PlanEntry(Action.NOOP, "cc-1", _a("Chapter 3", days=2))]
    out = _digest(plan, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Due in the next 7 days (1)" in out
    assert "Chapter 3" in out


def test_far_future_items_are_not_in_the_week_view(tmp_path, monkeypatch):
    plan = [PlanEntry(Action.NOOP, "cc-1", _a("Final", days=60))]
    out = _digest(plan, tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Due in the next 7 days (0)" in out


def test_unresolved_items_are_always_surfaced(tmp_path, monkeypatch):
    """The blind-spot section is the whole reason the digest exists: work the
    system knows about but cannot place on a calendar."""
    a = Assignment(canvas_id=9, name="Class 16 - Poll", points=5.0, due_at=None,
                   course="MCB 436", source=Source.UNRESOLVED)
    out = _digest([PlanEntry(Action.SKIP, "cc-9", a)], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "no date available (1)" in out
    assert "Class 16 - Poll" in out
    assert "MCB 436" in out


def test_extra_credit_appears_in_digest_not_calendar(tmp_path, monkeypatch):
    a = _a("PILLAR B extra credit quiz", course="FSHN 120", digest_only=True)
    out = _digest([PlanEntry(Action.SKIP, "cc-1", a)], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Extra credit open this week" in out


def test_manual_corrections_are_reported(tmp_path, monkeypatch):
    out = _digest([], applied=["override Ch19: Nov 06 -> Nov 02"],
                  tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Manual corrections applied" in out
    assert "Ch19" in out


def test_errors_are_reported(tmp_path, monkeypatch):
    out = _digest([], errors=["cc-1: boom"], tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "## Errors" in out
    assert "boom" in out


def test_digest_is_rewritten_not_appended(tmp_path, monkeypatch):
    _digest([PlanEntry(Action.CREATE, "cc-1", _a("First"))],
            tmp_path=tmp_path, monkeypatch=monkeypatch)
    out = _digest([PlanEntry(Action.CREATE, "cc-2", _a("Second"))],
                  tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Second" in out
    assert "First" not in out


def test_token_expiry_days_and_message():
    from canvas_calendar.daily import token_expiry_status

    now = datetime(2026, 9, 19, 12, tzinfo=CHICAGO)
    days, msg = token_expiry_status(
        [{"workflow_state": "active", "expires_at": "2026-09-24T05:00:00Z"}], now
    )
    assert days == 4
    assert "4 days" in msg


def test_token_without_expiry_is_reported_as_permanent():
    from canvas_calendar.daily import token_expiry_status

    days, msg = token_expiry_status(
        [{"workflow_state": "active", "expires_at": None}], datetime.now(CHICAGO)
    )
    assert days is None
    assert "does not expire" in msg


def test_soonest_expiry_wins():
    from canvas_calendar.daily import token_expiry_status

    now = datetime(2026, 9, 1, tzinfo=CHICAGO)
    days, _ = token_expiry_status(
        [
            {"workflow_state": "active", "expires_at": "2026-12-01T05:00:00Z"},
            {"workflow_state": "active", "expires_at": "2026-09-11T05:00:00Z"},
        ],
        now,
    )
    assert days == 10


def test_inactive_tokens_are_ignored():
    from canvas_calendar.daily import token_expiry_status

    days, msg = token_expiry_status(
        [{"workflow_state": "deleted", "expires_at": "2026-09-02T05:00:00Z"}],
        datetime(2026, 9, 1, tzinfo=CHICAGO),
    )
    assert days is None and "no active tokens" in msg


def test_credentials_section_appears_in_digest(tmp_path, monkeypatch):
    monkeypatch.setattr("canvas_calendar.daily.LOG_DIR", tmp_path)
    monkeypatch.setattr("canvas_calendar.daily.DIGEST_PATH", tmp_path / "digest.md")
    write_digest([], Counter(), [], [], "Canvas token expires in 4 days (Sep 24)")
    out = (tmp_path / "digest.md").read_text()
    assert "## Credentials" in out
    assert "4 days" in out


def test_digest_names_completed_items(tmp_path, monkeypatch):
    plan = [PlanEntry(Action.DELETE, "cc-1", _a("Homework Week 1", completed=True))]
    out = _digest(plan, counts=Counter({"delete": 1}),
                  tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Cleared as completed" in out
    assert "Homework Week 1" in out
    assert "MCB 244" in out


def test_completed_items_are_not_listed_as_due(tmp_path, monkeypatch):
    plan = [PlanEntry(Action.DELETE, "cc-1", _a("Already Turned In", completed=True))]
    out = _digest(plan, counts=Counter({"delete": 1}),
                  tmp_path=tmp_path, monkeypatch=monkeypatch)
    due_section = out.split("## Due in the next 7 days")[1].split("\n## ")[0]
    assert "Already Turned In" not in due_section


def test_prune_deletes_do_not_appear_as_completed(tmp_path, monkeypatch):
    """A DELETE with no assignment is a prune, not a completion."""
    out = _digest([PlanEntry(Action.DELETE, "cc-99", None)],
                  counts=Counter({"delete": 1}),
                  tmp_path=tmp_path, monkeypatch=monkeypatch)
    assert "Cleared as completed" not in out
