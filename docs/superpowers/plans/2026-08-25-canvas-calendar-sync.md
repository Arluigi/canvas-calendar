# Canvas Calendar — Sync Engine Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the read path into a stateful sync — diff against SQLite, then write through a calendar adapter — without corrupting anything on failure.

**Architecture:** A pure diff engine over stored state, plus an adapter interface with Outlook and Google implementations. The diff is total and side-effect-free; only the applier touches a calendar. Every destructive operation is guarded by a UID-prefix check.

**Tech Stack:** Python 3.13, stdlib `sqlite3`, `httpx`, `msal` (Graph device-code auth), `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-25-canvas-calendar-design.md`
**Precedes:** plan 3 (LaunchAgent, digest, drift check)

## Ordering change from the spec

The spec made Outlook auth Milestone 1, a gate. Investigation on 2026-08-25 found
the UIUC tenant (`44467e6f-462c-4ea2-823f-7800de5434e3`, "University of Illinois -
Urbana", managed) exposes a working device-code endpoint, but whether a student may
register an application is not publicly documented and cannot be determined without
an authenticated attempt.

Rather than block, this plan builds every auth-independent component first. The
adapter interface means the backend decision changes one module, not the design.
Auth becomes a configuration step, not a gate.

---

## Chunk 1: State and diff

### Task 1: SQLite state store

**Files:**
- Create: `src/canvas_calendar/state.py`
- Test: `tests/test_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_state.py
from canvas_calendar.state import StateStore


def test_roundtrips_a_record(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at="2026-08-25T19:00:00Z", title_hash="abc", source="canvas")
    rec = s.get("cc-1")
    assert rec.title_hash == "abc"
    assert rec.source == "canvas"


def test_missing_uid_returns_none(tmp_path):
    assert StateStore(tmp_path / "s.db").get("cc-nope") is None


def test_upsert_overwrites(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at="a", title_hash="h1", source="canvas")
    s.upsert("cc-1", due_at="b", title_hash="h2", source="extracted")
    assert s.get("cc-1").title_hash == "h2"
    assert s.all_uids() == {"cc-1"}


def test_delete_removes(tmp_path):
    s = StateStore(tmp_path / "s.db")
    s.upsert("cc-1", due_at="a", title_hash="h", source="canvas")
    s.delete("cc-1")
    assert s.get("cc-1") is None


def test_survives_reopen(tmp_path):
    p = tmp_path / "s.db"
    StateStore(p).upsert("cc-1", due_at="a", title_hash="h", source="canvas")
    assert StateStore(p).get("cc-1") is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_state.py -v` — expect `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

```python
# src/canvas_calendar/state.py
"""SQLite-backed sync state. This is what makes a run a diff, not a re-import."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    uid         TEXT PRIMARY KEY,
    due_at      TEXT,
    title_hash  TEXT NOT NULL,
    source      TEXT NOT NULL,
    last_synced TEXT
)
"""


@dataclass(frozen=True)
class Record:
    uid: str
    due_at: str | None
    title_hash: str
    source: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.execute(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def upsert(self, uid: str, *, due_at: str | None, title_hash: str, source: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO events (uid, due_at, title_hash, source, last_synced) "
                "VALUES (?,?,?,?,datetime('now')) ON CONFLICT(uid) DO UPDATE SET "
                "due_at=excluded.due_at, title_hash=excluded.title_hash, "
                "source=excluded.source, last_synced=excluded.last_synced",
                (uid, due_at, title_hash, source),
            )

    def get(self, uid: str) -> Record | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT uid, due_at, title_hash, source FROM events WHERE uid=?", (uid,)
            ).fetchone()
        return Record(*row) if row else None

    def all_uids(self) -> set[str]:
        with self._conn() as c:
            return {r[0] for r in c.execute("SELECT uid FROM events")}

    def delete(self, uid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM events WHERE uid=?", (uid,))
```

- [ ] **Step 4: Verify pass, then commit**

```bash
uv run pytest tests/test_state.py -v
git add src/canvas_calendar/state.py tests/test_state.py
git commit -m "feat: SQLite sync state store"
```

---

### Task 2: Diff engine

Pure and total. Every assignment lands in exactly one bucket.

**Files:**
- Create: `src/canvas_calendar/diff.py`
- Test: `tests/test_diff.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diff.py
from datetime import datetime
from zoneinfo import ZoneInfo

from canvas_calendar.diff import Action, diff
from canvas_calendar.models import Assignment, Source
from canvas_calendar.state import StateStore

CH = ZoneInfo("America/Chicago")


def _a(cid, name="X", when="2026-08-25T19:00:00Z", source=Source.CANVAS):
    return Assignment(
        canvas_id=cid, name=name, points=1.0, course="C", source=source,
        due_at=datetime.fromisoformat(when) if when else None,
    )


def test_new_assignment_is_created(tmp_path):
    plan = diff([_a(1)], StateStore(tmp_path / "s.db"))
    assert [p.action for p in plan] == [Action.CREATE]


def test_unchanged_assignment_is_noop(tmp_path):
    s = StateStore(tmp_path / "s.db")
    a = _a(1)
    for p in diff([a], s):
        s.upsert(p.uid, due_at=p.due_key, title_hash=p.title_hash, source=p.source)
    assert [p.action for p in diff([a], s)] == [Action.NOOP]


def test_changed_due_date_is_update(tmp_path):
    s = StateStore(tmp_path / "s.db")
    for p in diff([_a(1)], s):
        s.upsert(p.uid, due_at=p.due_key, title_hash=p.title_hash, source=p.source)
    moved = _a(1, when="2026-09-01T19:00:00Z")
    assert [p.action for p in diff([moved], s)] == [Action.UPDATE]


def test_renamed_assignment_is_update(tmp_path):
    s = StateStore(tmp_path / "s.db")
    for p in diff([_a(1, name="Old")], s):
        s.upsert(p.uid, due_at=p.due_key, title_hash=p.title_hash, source=p.source)
    assert [p.action for p in diff([_a(1, name="New")], s)] == [Action.UPDATE]


def test_source_change_is_update(tmp_path):
    """Canvas backfills a due date on a previously extracted item. Same UID,
    so this must be an UPDATE, never a duplicate event."""
    s = StateStore(tmp_path / "s.db")
    for p in diff([_a(1, source=Source.EXTRACTED)], s):
        s.upsert(p.uid, due_at=p.due_key, title_hash=p.title_hash, source=p.source)
    plan = diff([_a(1, source=Source.CANVAS)], s)
    assert [p.action for p in plan] == [Action.UPDATE]
    assert plan[0].uid == "cc-1"


def test_vanished_assignment_is_deleted(tmp_path):
    s = StateStore(tmp_path / "s.db")
    for p in diff([_a(1)], s):
        s.upsert(p.uid, due_at=p.due_key, title_hash=p.title_hash, source=p.source)
    plan = diff([], s)
    assert [p.action for p in plan] == [Action.DELETE]
    assert plan[0].uid == "cc-1"


def test_unresolved_item_is_never_calendared(tmp_path):
    a = _a(1, when=None, source=Source.UNRESOLVED)
    assert [p.action for p in diff([a], StateStore(tmp_path / "s.db"))] == [Action.SKIP]


def test_diff_is_total(tmp_path):
    """Every input produces exactly one plan entry -- nothing is silently lost."""
    items = [_a(i) for i in range(5)]
    assert len(diff(items, StateStore(tmp_path / "s.db"))) == 5
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

```python
# src/canvas_calendar/diff.py
"""Pure diff between desired assignments and stored state.

Total by construction: every input assignment yields exactly one plan entry,
so nothing can be silently dropped between fetch and write.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from canvas_calendar.models import Assignment, Source
from canvas_calendar.state import StateStore


class Action(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"
    SKIP = "skip"  # no date; digest only


@dataclass(frozen=True)
class PlanEntry:
    action: Action
    uid: str
    assignment: Assignment | None
    title_hash: str = ""
    due_key: str | None = None
    source: str = ""


def _hash(a: Assignment) -> str:
    return hashlib.sha256(a.name.strip().encode()).hexdigest()[:16]


def diff(assignments: list[Assignment], store: StateStore) -> list[PlanEntry]:
    plan: list[PlanEntry] = []
    seen: set[str] = set()

    for a in assignments:
        if a.due_at is None or a.source is Source.UNRESOLVED:
            plan.append(PlanEntry(Action.SKIP, a.uid, a))
            continue
        seen.add(a.uid)
        due_key = a.due_at.isoformat()
        title_hash = _hash(a)
        entry = PlanEntry(
            action=Action.CREATE,
            uid=a.uid,
            assignment=a,
            title_hash=title_hash,
            due_key=due_key,
            source=a.source.value,
        )
        prior = store.get(a.uid)
        if prior is None:
            plan.append(entry)
        elif (prior.due_at, prior.title_hash, prior.source) == (
            due_key,
            title_hash,
            a.source.value,
        ):
            plan.append(PlanEntry(Action.NOOP, a.uid, a, title_hash, due_key, a.source.value))
        else:
            plan.append(PlanEntry(Action.UPDATE, a.uid, a, title_hash, due_key, a.source.value))

    for stale in sorted(store.all_uids() - seen):
        plan.append(PlanEntry(Action.DELETE, stale, None))
    return plan
```

- [ ] **Step 4: Verify pass, then commit**

---

## Chunk 2: Calendar adapters

### Task 3: Adapter interface and the never-delete guard

The single most dangerous operation in this project. A bug here wipes a real
calendar, so the guard gets its own module and its own tests.

**Files:**
- Create: `src/canvas_calendar/calendars/__init__.py`
- Create: `src/canvas_calendar/calendars/base.py`
- Test: `tests/test_calendar_guard.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_calendar_guard.py
import pytest

from canvas_calendar.calendars.base import UID_PREFIX, ForeignEventError, assert_ours


def test_accepts_our_uids():
    assert_ours("cc-1682585")
    assert_ours("cc-mi-5440597")


@pytest.mark.parametrize(
    "uid",
    ["", "AAMkAD-outlook-native-id", "cc", "ccx-1", "1682585", "google-event-abc"],
)
def test_rejects_foreign_uids(uid):
    """Anything we did not create must be untouchable. This is the guard that
    stands between a bug and someone's real calendar."""
    with pytest.raises(ForeignEventError):
        assert_ours(uid)


def test_prefix_is_the_documented_one():
    assert UID_PREFIX == "cc-"
```

- [ ] **Step 2: Implement**

```python
# src/canvas_calendar/calendars/base.py
"""Calendar adapter interface and the never-delete-foreign-events guard."""

from __future__ import annotations

from typing import Protocol

from canvas_calendar.models import Assignment

UID_PREFIX = "cc-"


class ForeignEventError(RuntimeError):
    """Raised when an operation targets an event this tool did not create."""


def assert_ours(uid: str) -> None:
    """Gate every destructive operation. Deliberately strict: an event we did
    not create must never be modified or deleted, whatever else goes wrong."""
    if not uid or not uid.startswith(UID_PREFIX) or len(uid) <= len(UID_PREFIX):
        raise ForeignEventError(f"refusing to touch non-managed event: {uid!r}")


class CalendarAdapter(Protocol):
    def ensure_calendar(self, name: str) -> str: ...
    def upsert(self, calendar_id: str, uid: str, assignment: Assignment) -> None: ...
    def delete(self, calendar_id: str, uid: str) -> None: ...
    def list_uids(self, calendar_id: str) -> set[str]: ...
```

- [ ] **Step 3: Verify pass, then commit**

---

### Task 4: Outlook (Microsoft Graph) adapter

**Files:**
- Create: `src/canvas_calendar/calendars/outlook.py`
- Test: `tests/test_outlook_adapter.py`

Tested entirely against `httpx.MockTransport`. No credentials needed to build
or verify this; auth is supplied at runtime.

Key Graph details:
- Events live at `/me/calendars/{id}/events`.
- Our UID goes in `singleValueExtendedProperties` (Graph has no writable iCalUID
  on create), filtered on read.
- All-day events use `isAllDay: true` with date-only start/end, end exclusive.
- Timed events send `dateTime` plus `timeZone: "Central Standard Time"` (Graph's
  Windows zone name, not the IANA one).

- [ ] **Step 1: Write tests covering: calendar creation is idempotent, all-day
      vs timed payload shape, delete refuses a foreign UID, and 401 surfaces.**

- [ ] **Step 2: Implement against those tests.**

- [ ] **Step 3: Commit.**

---

### Task 5: Device-code auth for Graph

**Files:**
- Create: `src/canvas_calendar/calendars/msauth.py`
- Test: `tests/test_msauth.py`

Tenant `44467e6f-462c-4ea2-823f-7800de5434e3`. Public client, device-code flow,
scopes `Calendars.ReadWrite offline_access`. Refresh token cached to
`~/.config/canvas-calendar/msal_cache.json` with mode `0600` so the LaunchAgent
can run unattended.

- [ ] **Step 1: Test token cache read/write and expiry handling with a fake
      MSAL client. Do not test against live Microsoft.**

- [ ] **Step 2: Implement.**

- [ ] **Step 3: Commit.**

**Blocked on:** a client ID from an app registration. See "User action required"
below. Everything else in this plan proceeds without it.

---

### Task 6: Google adapter (fallback)

Same interface, `iCalUID` field carries our UID natively, service account or
OAuth. Build only if the Outlook registration is refused.

---

## Chunk 3: Apply

### Task 7: Applier

**Files:**
- Create: `src/canvas_calendar/apply.py`
- Test: `tests/test_apply.py`

- [ ] Dry-run mode is the default and performs zero writes.
- [ ] State advances only after a successful calendar write, so a failed write
      is retried next run rather than being lost.
- [ ] A `TokenExpired` or adapter error aborts without partial state advance.
- [ ] Every DELETE passes through `assert_ours` first.
- [ ] Returns counts per action for the digest.

---

## User action required (unblocks Outlook)

Whether a student may register an application in the UIUC tenant is not publicly
documented. Two minutes settles it:

1. Sign in to <https://entra.microsoft.com> with the illinois.edu account.
2. **App registrations → New registration**.
   - Name: `canvas-calendar`
   - Supported account types: *Accounts in this organizational directory only*
   - Redirect URI: leave blank
3. On the created app: **Authentication → Allow public client flows → Yes**.
4. **API permissions → Add → Microsoft Graph → Delegated →** `Calendars.ReadWrite`
   and `offline_access`. Grant consent if the button is available.
5. Copy the **Application (client) ID** into `~/.config/canvas-calendar/config.toml`.

If step 2 is blocked or greyed out, UIUC restricts registrations. Fall back to
the Google adapter (Task 6), or request an app registration from
`techservices-iamu@illinois.edu`.

## Not in this plan

- LaunchAgent scheduling, digest rendering, notifications (plan 3)
- Drift check (plan 3)
- Recurring class-meeting events (needs section resolution, deferred to plan 3)
