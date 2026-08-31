# Completion Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove an assignment from the calendar and the digest once it has been completed on Canvas, without ever removing work that is merely ungraded.

**Architecture:** Canvas submission state arrives free on the existing assignments call via `include[]=submission`. A pure predicate in a new `completion.py` decides what "complete" means. `Assignment` gains a `completed` flag parallel to the existing `digest_only`; `diff()` maps it to an explicit DELETE, and the digest reports what it removed rather than dropping it silently.

**Tech Stack:** Python 3.13, httpx, pytest

**Spec:** `docs/superpowers/specs/2026-08-31-portable-multi-backend-design.md` (Section 1)

## Global Constraints

- Python `>=3.13`; run everything through `uv run`.
- Verification before any commit: `uv run pytest -q && uv run ruff check src tests`.
- Line length 100 (ruff).
- **The predicate must fail toward keeping an event.** A missing event is the failure this project exists to prevent; a stale one is merely untidy.
- Never filter completed items out of the fetch. They must reach `diff()` so retraction restores them and the digest can count them.
- Do not change the UID format `cc-<namespace><id>`.
- This plan depends on `2026-08-31-machine-isolation.md` being complete, so that the tool under test is not the tool on the 07:15 schedule.

---

### Task 1: The completion predicate

Isolated in its own module because it is pure, it is the part most likely to need tuning as new submission shapes appear, and it deserves its own test file. The rules come from live data measured 2026-08-31 across 190 assignments.

**Files:**
- Create: `src/canvas_calendar/completion.py`
- Create: `tests/test_completion.py`

**Interfaces:**
- Consumes: nothing
- Produces: `is_complete(submission: dict | None) -> bool`, used by Task 2

- [ ] **Step 1: Write the failing tests**

Create `tests/test_completion.py`:

```python
"""Cases are real submission shapes observed on 2026-08-31, not invented ones."""

import pytest

from canvas_calendar.completion import is_complete


@pytest.mark.parametrize(
    "submission,expected,why",
    [
        (None, False, "assignment carried no submission key at all"),
        ({}, False, "empty submission"),
        ({"workflow_state": "unsubmitted"}, False, "the 175-item majority"),
        # graded with a score but no timestamp: external_tool passback.
        # MCB 244 'Chapter 1', MCB 354 'iClicker Grade', MCB 364 'Wk1'.
        ({"workflow_state": "graded", "score": 10.0, "submitted_at": None}, True,
         "graded by passback, no Canvas submission timestamp"),
        # A zero score is still a grade. Truthiness on score would break this.
        ({"workflow_state": "graded", "score": 0.0, "submitted_at": None}, True,
         "zero is a real score"),
        # THE TRAP: MCB 436 'Class 1 - Poll'. Gradebook placeholder, no work done.
        ({"workflow_state": "graded", "score": None, "submitted_at": None}, False,
         "graded placeholder with no evidence of work"),
        ({"workflow_state": "graded", "score": None, "submitted_at": "2026-08-26T15:04:29Z"},
         True, "graded, no score yet, but it was turned in"),
        # FSHN 120 'PILLAR A - REFLECTIVE ASSIGNMENT'
        ({"workflow_state": "submitted", "score": None,
          "submitted_at": "2026-08-27T18:13:33Z"}, True, "submitted"),
        # MCB 436 'Lecture 1 - Specific Activity'
        ({"workflow_state": "pending_review", "score": None,
          "submitted_at": "2026-08-31T21:13:46Z"}, True, "awaiting instructor review"),
        ({"workflow_state": "unsubmitted", "excused": True}, True, "excused is done"),
        ({"workflow_state": "unsubmitted", "excused": False}, False, "not excused"),
    ],
)
def test_is_complete(submission, expected, why):
    assert is_complete(submission) is expected, why
```

- [ ] **Step 2: Run the tests and confirm they fail**

```bash
uv run pytest tests/test_completion.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'canvas_calendar.completion'`.

- [ ] **Step 3: Write the implementation**

Create `src/canvas_calendar/completion.py`:

```python
"""When is a Canvas assignment done?

Measured against live data on 2026-08-31 across 190 assignments. Two facts
drive every rule here.

`graded` with no `submitted_at` is normal, not anomalous: 100 of 219
assignments are `external_tool`, graded by passback, and never record a Canvas
submission timestamp. A test based on `submitted_at` would miss most completed
work.

But bare `graded` is not evidence either. MCB 436's 'Class 1 - Poll' is
`graded` with neither a score nor a timestamp -- a gradebook placeholder for
work that was never started. Treating that as complete would clear it from the
calendar and the student would never see it again.

So `graded` counts only when corroborated by a score or a timestamp. Every
rule fails toward keeping the event: an extra event is untidy, a missing one
loses coursework.
"""

from __future__ import annotations

_DONE_STATES = ("submitted", "pending_review")


def is_complete(submission: dict | None) -> bool:
    """True only on positive evidence that the work was turned in or excused."""
    if not submission:
        return False
    if submission.get("excused"):
        return True
    state = submission.get("workflow_state")
    if state in _DONE_STATES:
        return True
    if state == "graded":
        # `is not None` deliberately, not truthiness: a score of 0.0 is a real
        # grade and must count as complete.
        return submission.get("score") is not None or bool(submission.get("submitted_at"))
    return False
```

- [ ] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_completion.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/canvas_calendar/completion.py tests/test_completion.py
git commit -m "feat: completion predicate for Canvas submissions

Bare 'graded' is not evidence -- MCB 436's polls are graded with no score
and no timestamp. Requires corroboration before treating work as done."
```

---

### Task 2: Fetch submissions and flag assignments

**Files:**
- Modify: `src/canvas_calendar/canvas/client.py:43-44` (`list_assignments`)
- Modify: `src/canvas_calendar/models.py` (`Assignment`)
- Modify: `src/canvas_calendar/pipeline.py:40-53` (`build_assignments`)
- Modify: `tests/test_canvas_client.py`
- Modify: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `is_complete` from Task 1
- Produces: `Assignment.completed: bool`, consumed by Tasks 3 and 4

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pipeline.py`. `build_assignments` is already imported at
line 4 of that file — do not add a second import.

```python
def test_build_assignments_flags_completed():
    raw = [
        {"id": 1, "name": "done", "points_possible": 10,
         "due_at": "2026-09-01T04:59:00Z",
         "submission": {"workflow_state": "graded", "score": 10.0}},
        {"id": 2, "name": "not done", "points_possible": 10,
         "due_at": "2026-09-01T04:59:00Z",
         "submission": {"workflow_state": "unsubmitted"}},
        {"id": 3, "name": "no submission key", "points_possible": 10,
         "due_at": "2026-09-01T04:59:00Z"},
    ]
    out = {a.canvas_id: a.completed for a in build_assignments(raw, course="MCB 244")}
    assert out == {1: True, 2: False, 3: False}


def test_completed_assignments_keep_their_due_date():
    """Completion must not blank the date -- diff still needs it to report."""
    raw = [{"id": 1, "name": "done", "points_possible": 1,
            "due_at": "2026-09-01T04:59:00Z",
            "submission": {"workflow_state": "submitted",
                           "submitted_at": "2026-08-30T10:00:00Z"}}]
    a = build_assignments(raw, course="MCB 244")[0]
    assert a.completed is True
    assert a.due_at is not None
```

Append to `tests/test_canvas_client.py`:

```python
def test_list_assignments_requests_submission(monkeypatch):
    """Without include[]=submission every assignment looks unsubmitted."""
    seen = {}

    class FakeResp:
        status_code = 200
        headers = {}

        def json(self):
            return []

        def raise_for_status(self):
            return None

    class FakeHTTP:
        def get(self, url, headers=None, params=None):
            seen.update(params or {})
            return FakeResp()

    from canvas_calendar.canvas.client import CanvasClient

    CanvasClient("https://x/api/v1", "tok", http=FakeHTTP()).list_assignments(1)
    assert seen.get("include[]") == "submission"
```

- [ ] **Step 2: Run them and confirm they fail**

```bash
uv run pytest tests/test_pipeline.py -k completed tests/test_canvas_client.py -k submission -v
```

Expected: `TypeError` on the unexpected `completed` keyword, or `AssertionError: None != 'submission'`.

- [ ] **Step 3: Add the `completed` field**

In `src/canvas_calendar/models.py`, inside `@dataclass class Assignment`, add immediately after the `digest_only` field and its comment:

```python
    # Set when Canvas reports the work turned in, graded with corroboration,
    # or excused. diff() turns this into a DELETE so the event leaves the
    # calendar. Never filtered out of the fetch: retraction must restore the
    # event, and the digest must be able to count what it removed.
    completed: bool = False
```

Place it before `namespace` so the existing positional argument order is
unaffected for any caller using keywords only — every call site in this
codebase uses keywords.

- [ ] **Step 4: Request submissions in the client**

In `src/canvas_calendar/canvas/client.py`, replace `list_assignments`:

```python
    def list_assignments(self, course_id: int) -> list[dict]:
        """`include[]=submission` attaches the current user's submission to
        each assignment. Same endpoint, same pagination, no extra calls --
        and without it every assignment looks unsubmitted."""
        return self._get_all(
            f"/courses/{course_id}/assignments", **{"include[]": "submission"}
        )
```

- [ ] **Step 5: Flag assignments during construction**

In `src/canvas_calendar/pipeline.py`, add the import near the other
`canvas_calendar` imports:

```python
from canvas_calendar.completion import is_complete
```

and in `build_assignments`, add one argument to the `Assignment(...)` call:

```python
                completed=is_complete(a.get("submission")),
```

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_pipeline.py tests/test_canvas_client.py -v
```

Expected: all pass, including the pre-existing tests in both files.

- [ ] **Step 7: Full suite and lint**

```bash
uv run pytest -q && uv run ruff check src tests
```

Expected: all pass. `test_diff.py` should still be green — nothing consumes `completed` yet.

- [ ] **Step 8: Commit**

```bash
git add src/canvas_calendar/models.py src/canvas_calendar/canvas/client.py \
        src/canvas_calendar/pipeline.py tests/test_pipeline.py tests/test_canvas_client.py
git commit -m "feat: fetch Canvas submissions and flag completed assignments"
```

---

### Task 3: Make `diff()` remove completed work

**Files:**
- Modify: `src/canvas_calendar/diff.py:63-70` (top of the `for a in assignments` loop)
- Modify: `tests/test_diff.py`

**Interfaces:**
- Consumes: `Assignment.completed` from Task 2
- Produces: DELETE plan entries whose `assignment` is not None, consumed by Task 4

- [ ] **Step 1: Extend the existing `_a` helper**

`tests/test_diff.py:8` already defines `_a(cid, name, when, source)`. Add one
parameter rather than writing a second fixture:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_diff.py`. The `_commit` helper at line 19 is how the
existing tests seed state; reuse it.

```python
def test_completed_with_state_row_is_deleted(tmp_path):
    store = StateStore(tmp_path / "s.db")
    _commit(diff([_a(1)], store), store)          # event exists on the calendar

    plan = diff([_a(1, completed=True)], store)   # Canvas now reports it done
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
    store = StateStore(tmp_path / "s.db")
    plan = diff([_a(1, completed=False)], store)
    assert [p.action for p in plan] == [Action.CREATE]
```

- [ ] **Step 3: Run them and confirm they fail**

```bash
uv run pytest tests/test_diff.py -k "completed or retracted" -v
```

Expected: the first three fail. `test_completed_with_state_row_is_deleted` should report two entries (a SKIP plus a prune DELETE) or a CREATE, depending on ordering — either way, not a single DELETE.

- [ ] **Step 4: Implement**

In `src/canvas_calendar/diff.py`, at the very top of the `for a in assignments:` loop, **before** the existing `if a.due_at is None or ...` check:

```python
        if a.completed:
            # Positive evidence the work is done, so this deletes even when
            # prune is off -- unlike absence from a filtered fetch, which is
            # evidence of nothing. Added to `seen` so the prune pass below
            # does not emit a second DELETE for the same uid.
            seen.add(a.uid)
            action = Action.DELETE if store.get(a.uid) is not None else Action.SKIP
            plan.append(PlanEntry(action, a.uid, a))
            continue
```

Then extend the `diff()` docstring with a sentence after the `prune` paragraph:

```
    A completed assignment is removed regardless of `prune`: completion is
    positive evidence, whereas absence from a filtered fetch is not.
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_diff.py -v
```

Expected: all pass, including every pre-existing diff test.

- [ ] **Step 6: Confirm `apply_plan` needs no change**

```bash
uv run pytest tests/test_apply.py -v
```

Expected: all pass. The DELETE branch of `apply_plan` already calls `assert_ours`, `adapter.delete` and `store.delete`, and ignores `entry.assignment` — so a completion DELETE flows through unmodified.

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/diff.py tests/test_diff.py
git commit -m "feat: diff removes completed assignments from the calendar

Deletes regardless of prune -- completion is positive evidence, absence
from a filtered fetch is not."
```

---

### Task 4: Report what was cleared, never drop it silently

The digest must name what it removed. Silently deleting 15 items would violate the principle the project is built on, and it is the only way the user would catch a false-positive completion.

**Files:**
- Modify: `src/canvas_calendar/daily.py:198-206` (the "Due in the next 7 days" block) and immediately after it
- Modify: `tests/test_daily.py`

**Interfaces:**
- Consumes: DELETE entries with a non-None `assignment` whose `completed` is True, from Task 3
- Produces: nothing consumed later

- [ ] **Step 1: Extend the existing `_a` helper**

`tests/test_daily.py:10` already defines `_a(name, course, days, source, digest_only)`.
Add one parameter:

```python
def _a(name, course="MCB 244", days=2, source=Source.CANVAS, digest_only=False,
       completed=False):
    return Assignment(
        canvas_id=1, name=name, points=1.0, course=course, source=source,
        digest_only=digest_only, completed=completed,
        due_at=datetime.now(CHICAGO) + timedelta(days=days),
    )
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_daily.py`. The `_digest` helper at line 18 patches
`LOG_DIR` and `DIGEST_PATH` and returns the rendered text; use it rather than
calling `write_digest` directly.

```python
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
```

- [ ] **Step 3: Run them and confirm they fail**

```bash
uv run pytest tests/test_daily.py -k "completed" -v
```

Expected: `AssertionError` — "Cleared as completed" is not in the digest, and the completed item does appear under "Due in the next 7 days".

- [ ] **Step 4: Exclude completed items from the "due" section**

In `src/canvas_calendar/daily.py`, in the `upcoming` comprehension, change:

```python
        if a.due_at and now <= a.due_at <= soon and not a.digest_only
```

to:

```python
        if a.due_at and now <= a.due_at <= soon and not a.digest_only and not a.completed
```

- [ ] **Step 5: Add the new section**

Immediately after the `lines.append("")` that closes the "Due in the next 7 days" block, and before the `# 4. Extra credit` comment, insert:

```python
    # 4. What we removed because Canvas says it is done. Named, never merely
    #    counted: this is the only place a false-positive completion becomes
    #    visible before the work is missed.
    cleared = [
        p.assignment
        for p in plan
        if p.action is Action.DELETE and p.assignment and p.assignment.completed
    ]
    if cleared:
        lines += [f"## Cleared as completed ({len(cleared)})", ""]
        lines += [
            f"- {a.course} — {a.name[:60]}"
            + (f" (was due {to_local(a.due_at):%a %b %d})" if a.due_at else "")
            for a in cleared
        ]
        lines += [
            "",
            "> Removed from the calendar because Canvas reports them submitted, "
            "graded or excused. If something here is not actually done, it was "
            "graded early or marked in error — check Canvas.",
            "",
        ]
```

Then renumber the trailing comments in the function: `# 4. Extra credit` becomes `# 5.`, and `# 5. The blind spots` becomes `# 6.`.

- [ ] **Step 6: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_daily.py -v
```

Expected: all pass.

- [ ] **Step 7: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/daily.py tests/test_daily.py
git commit -m "feat: digest names assignments cleared as completed"
```

---

### Task 5: Config toggle and live verification

**Files:**
- Modify: `src/canvas_calendar/config.py:49-66` (`load_sync_options`)
- Modify: `src/canvas_calendar/pipeline.py` (`collect`)
- Modify: `tests/test_pipeline.py`
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: everything above
- Produces: the `clear_completed` config key, default `True`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pipeline.py`:

```python
def test_clear_completed_false_keeps_events(monkeypatch):
    """The toggle must clear the flag, not filter the item out."""
    from canvas_calendar import pipeline

    raw = [{"id": 1, "name": "done", "points_possible": 1,
            "due_at": "2026-09-01T04:59:00Z",
            "submission": {"workflow_state": "graded", "score": 5.0}}]
    items = pipeline.build_assignments(raw, course="MCB 244")
    assert items[0].completed is True

    kept = pipeline.apply_completion_policy(items, clear_completed=False)
    assert kept[0].completed is False
    assert len(kept) == 1, "the item stays in the fetch; only the flag clears"
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
uv run pytest tests/test_pipeline.py -k clear_completed -v
```

Expected: `AttributeError: module 'canvas_calendar.pipeline' has no attribute 'apply_completion_policy'`.

- [ ] **Step 3: Add the default to `load_sync_options`**

In `src/canvas_calendar/config.py`, add to the `defaults` dict:

```python
        "clear_completed": True,
```

- [ ] **Step 4: Add the policy function and wire it into `collect`**

In `src/canvas_calendar/pipeline.py`, add:

```python
def apply_completion_policy(
    items: list[Assignment], *, clear_completed: bool
) -> list[Assignment]:
    """Honour the `clear_completed` config key.

    Clears the flag rather than dropping the item: an assignment filtered out
    of the fetch would be pruned from the calendar, which is the opposite of
    what disabling this feature should do.
    """
    if not clear_completed:
        for a in items:
            a.completed = False
    return items
```

In `collect()`, change the final return to run the policy before overrides:

```python
    opts = load_sync_options()
    results = apply_completion_policy(results, clear_completed=opts["clear_completed"])
    return apply_overrides(results, load_overrides(), applied)
```

`collect()` already calls `load_sync_options()` for `exclude_assignment_ids`;
reuse that call rather than loading the config twice.

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_pipeline.py -v
```

Expected: all pass.

- [ ] **Step 6: Full suite and lint**

```bash
uv run pytest -q && uv run ruff check src tests
```

- [ ] **Step 7: Dry run against live Canvas and read the DELETE list**

```bash
uv run canvas-calendar sync 2>&1 | grep -A40 "DELETE"
uv run canvas-calendar sync 2>&1 | tail -1
```

Expected: a DELETE section listing completed work, and a count line. As of 2026-08-31 roughly 15 items read as complete, though several are extra-credit or undated and so were never calendared — expect fewer deletes than that.

**STOP and read the list before continuing.** Every name in it must be work that is genuinely done. If anything unfamiliar appears, do not run `--live`; report it, because it means the predicate is wrong.

- [ ] **Step 8: Document the behaviour**

In `CLAUDE.md`, under "Gotchas that cost hours", add:

```markdown
- **Bare `graded` is not completion.** MCB 436's polls are `graded` with no
  score and no `submitted_at` — a gradebook placeholder, not work done.
  `completion.py` requires corroboration, and the digest names everything it
  clears so a false positive is visible before the work is missed.
```

- [ ] **Step 9: Commit**

```bash
git add src/canvas_calendar/config.py src/canvas_calendar/pipeline.py \
        tests/test_pipeline.py CLAUDE.md
git commit -m "feat: clear_completed config toggle, default on"
```

- [ ] **Step 10: Apply it for real**

Only after Step 7's list was read and every entry confirmed:

```bash
uv run canvas-calendar sync --live 2>&1 | tail -3
uv run canvas-calendar sync 2>&1 | tail -1
```

Expected: the first reports the deletes applied; the second is a clean no-op
with only `skip` and `noop`. Then upgrade the scheduled copy:

```bash
uv tool install --force /Users/aryansachdev/code/canvas-calendar
~/.local/bin/canvas-calendar sync 2>&1 | tail -1
```

Expected: no-op, confirming the installed binary agrees with the working tree.

---

## Notes for the executor

- The debrief email needs **no changes**. `todays_events()` in `debrief.py:168` reads the calendar through Graph `calendarview`, not the assignment list, so a deleted event disappears from the debrief automatically. Do not add filtering there.
- `Source.UNRESOLVED` items can never be completed in a way that matters — they are already SKIP and never reach the calendar. No special handling needed.
- Manual additions (`man-` namespace) and SubHeader events (`mi-`) have no Canvas submission, so `is_complete` returns False for them permanently. That is expected; the MCB 320 quizzes will never auto-clear.
- If a completed item flickers — deleted one run, recreated the next — that means Canvas is returning inconsistent submission data. Report it rather than adding retry logic; the diff is already correct in both directions.
