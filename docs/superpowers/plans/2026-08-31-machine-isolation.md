# Machine Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decouple the author's scheduled sync from the git working tree, so no branch checkout or refactor can silently change what runs at 07:15.

**Architecture:** No production code changes. The LaunchAgents are repointed from a venv inside the repo to a stable `uv tool install` location; the config gains explicit keys that later work will require; `main` is fast-forwarded so it stops being a regression trap. One regression test is added to lock the UID format that 157 live calendar events depend on.

**Tech Stack:** launchd, `uv tool`, git, pytest

**Spec:** `docs/superpowers/specs/2026-08-31-portable-multi-backend-design.md` (Section 5)

## Global Constraints

- Python `>=3.13`; run everything through `uv run`.
- Verification before any commit: `uv run pytest -q && uv run ruff check src tests`.
- The UID prefix is `cc-` and the format is `cc-<namespace><id>`. It must not change. Every row in `state.db` and every live Outlook event depends on it.
- Exit codes: 0 clean, 1 apply errors, 2 Canvas token expired, 3 Outlook auth failed.
- No behaviour change is acceptable in this plan. Every verification step asserts *zero* calendar writes.
- Never invoke `sync --live` by hand. Every manual verification is a dry run. The single exception is Task 5's `launchctl kickstart`, which runs the real `daily --live` scheduled job — that is the point of the task, and it is expected to be a no-op. If it is not a no-op, stop.

---

### Task 1: Pin the config keys the portability work will require

The portability plan makes `calendar_backend` a required key with no default, because defaulting to `outlook` is wrong for new users and defaulting to `eventkit` would silently change this machine. Writing it now, before any code lands, means that requirement is already satisfied here. `debrief_enabled` is likewise written as `true` to preserve current behaviour, since new installs default it to `false`.

These keys are additive. The current code reads config via `load_sync_options()`, which filters to a known-key allowlist and ignores everything else, so adding them cannot affect today's runs.

**Files:**
- Modify: `~/.config/canvas-calendar/config.json`

**Interfaces:**
- Consumes: nothing
- Produces: a `config.json` containing `calendar_backend`, `debrief_enabled`, and a `term` block, consumed by Task 3 of the portability plan

- [ ] **Step 1: Back up the current config**

```bash
cp ~/.config/canvas-calendar/config.json ~/.config/canvas-calendar/config.json.pre-isolation
cat ~/.config/canvas-calendar/config.json
```

- [ ] **Step 2: Add the three key groups**

Edit `~/.config/canvas-calendar/config.json`, keeping every existing key exactly as-is and adding:

```json
  "calendar_backend": "outlook",
  "debrief_enabled": true,
  "term": {
    "year": 2026,
    "season": "fall",
    "start": "2026-08-24",
    "end": "2026-12-09",
    "holidays": ["2026-09-07",
                 "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24",
                 "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28",
                 "2026-11-29"]
  }
```

The `term` values are copied from `meetings.py:24-28` (`TERM_START`, `TERM_END`, `HOLIDAYS`) and must match them exactly. `season` is `"fall"` to match the Course Explorer path in `sync_meetings.py:20`.

- [ ] **Step 3: Verify the file is valid JSON and the old keys survived**

```bash
uv run python -c "
import json; c = json.load(open('$HOME/.config/canvas-calendar/config.json'))
old = json.load(open('$HOME/.config/canvas-calendar/config.json.pre-isolation'))
assert all(c[k] == v for k, v in old.items()), 'an existing key changed'
assert c['calendar_backend'] == 'outlook'
assert len(c['term']['holidays']) == 10
print('config OK,', len(c), 'keys')
"
```

Expected: `config OK, 11 keys` — and no assertion error.

- [ ] **Step 4: Verify the running tool is unaffected**

The test is **differential**, not absolute. Canvas drifts on its own — an
instructor moving a due date produces a legitimate pending UPDATE that has
nothing to do with this change. Comparing against zero would flag that as a
regression. Compare the plan before and after instead:

```bash
cp ~/.config/canvas-calendar/config.json ~/.config/canvas-calendar/config.json.with-new-keys
cp ~/.config/canvas-calendar/config.json.pre-isolation ~/.config/canvas-calendar/config.json
uv run canvas-calendar sync 2>&1 | tail -1          # baseline
cp ~/.config/canvas-calendar/config.json.with-new-keys ~/.config/canvas-calendar/config.json
uv run canvas-calendar sync 2>&1 | tail -1          # after
```

Expected: the two count lines are **identical**. `load_sync_options()` filters
config through a known-key allowlist, so the added keys cannot reach any code
path; this step proves that rather than assuming it.

Observed on 2026-08-31: both runs reported
`{'skip': 30, 'noop': 158, 'update': 2}`. The two updates are MCB 436's
"Lecture 2 - Specific Activity" Ebola homework items, whose dates the
instructor moved — genuine drift, applied by the scheduled run in Task 5.

---

### Task 2: Lock the UID format with a regression test

Before any refactor touches the adapter layer, the UID contract needs a test that fails loudly if it changes. `assert_ours` already tests the `cc-` prefix guard; nothing tests that `Assignment.uid` still *produces* that shape for each namespace in use.

**Files:**
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `Assignment` from `canvas_calendar.models`, `UID_PREFIX` from `canvas_calendar.calendars.base`
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
import pytest

from canvas_calendar.calendars.base import UID_PREFIX, assert_ours


@pytest.mark.parametrize(
    "canvas_id,namespace,expected",
    [
        (1652210, "", "cc-1652210"),          # Canvas assignment
        (5440557, "mi-", "cc-mi-5440557"),    # module item / SubHeader event
        ("mcb320-quiz1", "man-", "cc-man-mcb320-quiz1"),  # manual addition
    ],
)
def test_uid_format_is_frozen(canvas_id, namespace, expected):
    """157 live calendar events and every state.db row depend on this exact
    shape. Changing it orphans all of them silently."""
    a = Assignment(
        canvas_id=canvas_id, name="x", points=0.0, due_at=None,
        course="MCB 320", namespace=namespace,
    )
    assert a.uid == expected
    assert a.uid.startswith(UID_PREFIX)
    assert_ours(a.uid)
```

If `tests/test_models.py` already imports `Assignment`, do not import it twice.

- [ ] **Step 2: Run it and confirm it passes against current code**

```bash
uv run pytest tests/test_models.py::test_uid_format_is_frozen -v
```

Expected: 3 passed. This test documents existing behaviour, so it passes immediately — that is correct. Its value is failing later.

- [ ] **Step 3: Prove it actually guards, by breaking the code temporarily**

Change `UID_PREFIX` in `src/canvas_calendar/calendars/base.py:11` from `"cc-"` to `"cx-"`, then:

```bash
uv run pytest tests/test_models.py::test_uid_format_is_frozen -v
```

Expected: FAIL. Now revert the change:

```bash
git checkout src/canvas_calendar/calendars/base.py
```

- [ ] **Step 4: Full suite and lint**

```bash
uv run pytest -q && uv run ruff check src tests
```

Expected: all pass, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add tests/test_models.py
git commit -m "test: freeze the cc- UID format against refactor

157 live events and every state.db row key off this shape."
```

---

### Task 3: Install a stable copy and repoint the LaunchAgents

Today both plists execute `/Users/aryansachdev/code/canvas-calendar/.venv/bin/canvas-calendar`, a path inside the git working tree. Whatever branch is checked out at 07:15 is what runs.

**Files:**
- Modify: `~/Library/LaunchAgents/com.aryan.canvas-calendar.plist`
- Modify: `~/Library/LaunchAgents/com.aryan.canvas-debrief.plist`

**Interfaces:**
- Consumes: a clean `feat/read-path` working tree
- Produces: `~/.local/bin/canvas-calendar`, a binary independent of the checkout

- [ ] **Step 1: Confirm the tree is clean and record the commit being installed**

```bash
git status --short && git rev-parse --short HEAD
```

Expected: no output from `git status` (clean tree). Note the SHA — it is what gets installed.

- [ ] **Step 2: Install the tool to a stable location**

```bash
uv tool install --force /Users/aryansachdev/code/canvas-calendar
ls -l ~/.local/bin/canvas-calendar
```

Expected: the binary exists. If `~/.local/bin` is not on PATH that is fine — the plists use the absolute path.

- [ ] **Step 3: Verify the installed copy behaves identically**

```bash
~/.local/bin/canvas-calendar sync 2>&1 | tail -1
```

Expected: identical to Task 1 Step 4 — `skip` and `noop` only, no creates, updates or deletes. If this differs, STOP and investigate before touching the plists.

- [ ] **Step 4: Record the current plist contents**

```bash
cp ~/Library/LaunchAgents/com.aryan.canvas-calendar.plist /tmp/canvas-calendar.plist.bak
cp ~/Library/LaunchAgents/com.aryan.canvas-debrief.plist /tmp/canvas-debrief.plist.bak
grep -A3 ProgramArguments ~/Library/LaunchAgents/com.aryan.canvas-*.plist
```

- [ ] **Step 5: Repoint both plists**

In each plist, replace the first `<string>` inside `ProgramArguments`:

```
/Users/aryansachdev/code/canvas-calendar/.venv/bin/canvas-calendar
```

with:

```
/Users/aryansachdev/.local/bin/canvas-calendar
```

Leave every other key untouched — labels, `StartCalendarInterval`, log paths, and environment all stay as they are.

- [ ] **Step 6: Reload both agents**

```bash
launchctl bootout gui/$UID/com.aryan.canvas-calendar 2>/dev/null
launchctl bootout gui/$UID/com.aryan.canvas-debrief 2>/dev/null
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.aryan.canvas-calendar.plist
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.aryan.canvas-debrief.plist
launchctl print gui/$UID/com.aryan.canvas-calendar | grep -E "program|state"
```

Expected: `state = waiting` and a `program` path under `~/.local/bin`.

- [ ] **Step 7: Prove the decoupling actually works**

```bash
git stash list
git checkout main
~/.local/bin/canvas-calendar sync 2>&1 | tail -1
git checkout feat/read-path
```

Expected: the installed binary produces the same `skip`/`noop` counts while `main` is checked out. Before this change, that command would have run a 30-commit-old tool. This is the whole point of the task — do not skip it.

---

### Task 4: Merge `feat/read-path` into `main`

`main` sits 30 commits behind and contains a tool without module extraction, overrides, meetings, or the debrief. Task 3 removed its ability to hijack the scheduled run, but it remains a trap for any future clone or checkout.

**Files:**
- Modify: git refs only. PR #1 on `Arluigi/canvas-calendar`.

**Interfaces:**
- Consumes: a verified-green `feat/read-path`
- Produces: a `main` that is the known-good tool, the base for the portability branch

- [ ] **Step 1: Confirm green before merging**

```bash
uv run pytest -q && uv run ruff check src tests
```

Expected: all tests pass, ruff clean.

- [ ] **Step 2: Confirm the gap is what we think**

```bash
git rev-list --left-right --count main...feat/read-path
```

Expected: `0	31` or similar — zero commits on `main` that are not on the branch, so this is a fast-forward with nothing to lose.

- [ ] **Step 3: Merge PR #1**

```bash
gh pr merge 1 --merge
```

If the PR is not mergeable via `gh`, merge locally instead:

```bash
git checkout main && git merge --ff-only feat/read-path && git push origin main
git checkout feat/read-path
```

- [ ] **Step 4: Verify `main` is now the real tool**

```bash
git log --oneline -1 main
git rev-list --left-right --count main...feat/read-path
```

Expected: `main` points at the spec commit or later, and the count is `0	0`.

---

### Task 5: Confirm a full scheduled cycle runs clean

The plists fire at 07:00, 07:15 and 19:15. Nothing is proven until one of those has actually run from the new binary.

**Files:**
- Read: `~/.config/canvas-calendar/daily.log`, `~/.config/canvas-calendar/launchd.err.log`

**Interfaces:**
- Consumes: Tasks 1–4 complete
- Produces: verified confidence that the portability refactor can begin

- [ ] **Step 1: Trigger a run manually rather than waiting**

```bash
launchctl kickstart -p gui/$UID/com.aryan.canvas-calendar
```

- [ ] **Step 2: Check the exit code and log**

```bash
launchctl print gui/$UID/com.aryan.canvas-calendar | grep -E "last exit code|state"
tail -20 ~/.config/canvas-calendar/daily.log
tail -20 ~/.config/canvas-calendar/launchd.err.log
```

Expected: last exit code `0`, and a daily.log entry timestamped just now. A code of `2` means the Canvas token expired (run `canvas-calendar token`); `3` means Graph auth failed (run `canvas-calendar login`).

- [ ] **Step 3: Confirm the calendar was not disturbed**

```bash
~/.local/bin/canvas-calendar digest | head -30
sqlite3 ~/.config/canvas-calendar/state.db "select count(*) from events"
```

Expected: the state row count matches what it was before this plan began (157 events plus the four MCB 320 quizzes, so 161 tracked rows less any skipped). The digest should show no apply errors.

- [ ] **Step 4: Remove the config backup once satisfied**

```bash
mv ~/.config/canvas-calendar/config.json.pre-isolation /tmp/
```

---

## Notes for the executor

- **Do not run `sync --live` anywhere in this plan.** Every verification is a dry run. If a step appears to require a live write, something has gone wrong.
- If Step 3 of Task 3 shows any create/update/delete, stop. That means the installed copy disagrees with the working tree, and repointing the plists would apply that disagreement to a real calendar.
- The `.pre-isolation` config backup is the rollback for Task 1; `/tmp/canvas-*.plist.bak` are the rollbacks for Task 3. Keep both until Task 5 passes.
