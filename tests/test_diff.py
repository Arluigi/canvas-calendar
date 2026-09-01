from datetime import datetime

from canvas_calendar.diff import Action, diff
from canvas_calendar.models import Assignment, Source
from canvas_calendar.state import StateStore


def _a(cid, name="X", when="2026-08-25T19:00:00Z", source=Source.CANVAS, completed=False):
    return Assignment(
        canvas_id=cid,
        name=name,
        points=1.0,
        course="C",
        source=source,
        completed=completed,
        due_at=datetime.fromisoformat(when) if when else None,
    )


def _commit(plan, store):
    for p in plan:
        if p.action in (Action.CREATE, Action.UPDATE, Action.NOOP):
            store.upsert(p.uid, due_at=p.due_key, title_hash=p.title_hash, source=p.source)


def test_new_assignment_is_created(tmp_path):
    plan = diff([_a(1)], StateStore(tmp_path / "s.db"))
    assert [p.action for p in plan] == [Action.CREATE]


def test_unchanged_assignment_is_noop(tmp_path):
    s = StateStore(tmp_path / "s.db")
    a = _a(1)
    _commit(diff([a], s), s)
    assert [p.action for p in diff([a], s)] == [Action.NOOP]


def test_changed_due_date_is_update(tmp_path):
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], s), s)
    assert [p.action for p in diff([_a(1, when="2026-09-01T19:00:00Z")], s)] == [Action.UPDATE]


def test_renamed_assignment_is_update(tmp_path):
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1, name="Old")], s), s)
    assert [p.action for p in diff([_a(1, name="New")], s)] == [Action.UPDATE]


def test_canvas_date_supersedes_extracted_as_update_not_duplicate(tmp_path):
    """An instructor backfills a real due date on a previously extracted item.
    Same UID, so this must be an UPDATE -- never a second event alongside the
    extracted one."""
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1, source=Source.EXTRACTED)], s), s)
    plan = diff([_a(1, source=Source.CANVAS)], s)
    assert [p.action for p in plan] == [Action.UPDATE]
    assert plan[0].uid == "cc-1"
    assert plan[0].source == "canvas"


def test_vanished_assignment_is_deleted(tmp_path):
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], s), s)
    plan = diff([], s)
    assert [p.action for p in plan] == [Action.DELETE]
    assert plan[0].uid == "cc-1"


def test_unresolved_item_is_never_calendared(tmp_path):
    a = _a(1, when=None, source=Source.UNRESOLVED)
    assert [p.action for p in diff([a], StateStore(tmp_path / "s.db"))] == [Action.SKIP]


def test_unresolved_item_does_not_trigger_delete_of_itself(tmp_path):
    """A SKIP must not be mistaken for 'vanished' and delete a real event."""
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], s), s)
    plan = diff([_a(1, when=None, source=Source.UNRESOLVED)], s)
    actions = [p.action for p in plan]
    assert Action.SKIP in actions
    # It is no longer datable, so the stale event is removed -- but explicitly,
    # as a DELETE we can see, not silently.
    assert Action.DELETE in actions


def test_diff_is_total(tmp_path):
    """Every input yields exactly one plan entry -- nothing is lost silently."""
    items = [_a(i) for i in range(5)]
    plan = diff(items, StateStore(tmp_path / "s.db"))
    assert len(plan) == 5
    assert {p.uid for p in plan} == {f"cc-{i}" for i in range(5)}


def test_namespaced_uid_is_tracked_separately(tmp_path):
    """SubHeader events (cc-mi-*) must not collide with assignments (cc-*)."""
    s = StateStore(tmp_path / "s.db")
    sub = _a(5440597)
    sub.namespace = "mi-"
    asg = _a(5440597)
    plan = diff([sub, asg], s)
    assert {p.uid for p in plan} == {"cc-mi-5440597", "cc-5440597"}


def test_digest_only_items_are_never_calendared(tmp_path):
    """classify() marks extra credit as DIGEST. Computing that verdict and then
    calendaring the item anyway is how 9 FSHN extra-credit entries leaked into
    a dry run."""
    a = _a(1, name="PILLAR A VIDEO QUIZ - extra credit points will update")
    a.digest_only = True
    assert [p.action for p in diff([a], StateStore(tmp_path / "s.db"))] == [Action.SKIP]


def test_digest_only_beats_a_valid_due_date(tmp_path):
    a = _a(1, when="2026-08-28T15:00:00Z")
    a.digest_only = True
    assert diff([a], StateStore(tmp_path / "s.db"))[0].action is Action.SKIP


def test_filtered_run_must_not_prune_other_courses(tmp_path):
    """A partial fetch is not evidence that missing events are gone. Pruning on
    a --course subset would delete every event of every other course."""
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1), _a(2)], s), s)
    plan = diff([_a(1)], s, prune=False)
    assert Action.DELETE not in [p.action for p in plan]
    assert s.get("cc-2") is not None


def test_full_run_still_prunes(tmp_path):
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1), _a(2)], s), s)
    plan = diff([_a(1)], s, prune=True)
    assert [p.uid for p in plan if p.action is Action.DELETE] == ["cc-2"]


def test_force_rewrites_unchanged_events(tmp_path):
    """Reminder timings and subject format sit outside the comparison key, so
    changing them would register as NOOP and never reach the calendar."""
    s = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], s), s)
    assert [p.action for p in diff([_a(1)], s)] == [Action.NOOP]
    assert [p.action for p in diff([_a(1)], s, force=True)] == [Action.UPDATE]


def test_completed_with_state_row_is_deleted(tmp_path):
    store = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], store), store)  # event exists on the calendar

    plan = diff([_a(1, completed=True)], store)  # Canvas now reports it done
    entries = [p for p in plan if p.uid == "cc-1"]
    assert len(entries) == 1, "must not emit both a SKIP and a prune DELETE"
    assert entries[0].action is Action.DELETE
    assert entries[0].assignment is not None, "digest needs it to name the item"


def test_completed_without_state_row_is_skipped(tmp_path):
    plan = diff([_a(1, completed=True)], StateStore(tmp_path / "s.db"))
    assert [p.action for p in plan] == [Action.SKIP]


def test_completed_deletes_even_when_pruning_is_off(tmp_path):
    """Completion is positive evidence, unlike absence from a filtered fetch."""
    store = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], store), store)

    plan = diff([_a(1, completed=True)], store, prune=False)
    assert [p.action for p in plan] == [Action.DELETE]


def test_retracted_submission_restores_the_event(tmp_path):
    """The event was deleted; Canvas now reports it unsubmitted again."""
    plan = diff([_a(1, completed=False)], StateStore(tmp_path / "s.db"))
    assert [p.action for p in plan] == [Action.CREATE]
