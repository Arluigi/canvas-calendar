# Backend Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the tool write to any calendar macOS holds — iCloud, Google, or Exchange — by adding an EventKit backend behind the existing `CalendarAdapter` Protocol, without changing the author's Outlook path.

**Architecture:** Term dates move into a pure `terms.py` so `meetings.py` stays IO-free. A `make_adapter()` factory replaces four direct `OutlookAdapter` constructions. `EventKitAdapter` writes through Calendar.app, storing our UID in `EKEvent.URL` and building a uid→event index from a single predicate query per run — which avoids any `state.db` schema migration.

**Tech Stack:** Python 3.13, pyobjc-framework-EventKit, httpx, pytest

**Spec:** `docs/superpowers/specs/2026-08-31-portable-multi-backend-design.md` (Sections 2–3, and the Spike results)

## Global Constraints

- Python `>=3.13`; run everything through `uv run`.
- Verification before any commit: `uv run pytest -q && uv run ruff check src tests`.
- Line length 100 (ruff). No mutable class attributes in tests (RUF012) — annotate with `ClassVar`.
- **The author's Outlook path must not change behaviour.** `calendar_backend` is already pinned to `outlook` in their config. Every task ends with `uv run canvas-calendar sync` producing the same counts as before that task.
- Do not change the UID format `cc-<namespace><id>`. `tests/test_models.py::test_uid_format_is_frozen` guards it.
- **Never reuse an `EKSource` or `EKCalendar` across `EKEventStore.reset()`.** The spike produced a convincing false negative that way (`EKErrorDomain Code=17`). Build a fresh store per logical phase.
- **EventKit completion blocks must return `None`.** A block returning any value raises `ValueError: did not return None, expecting void return value` and aborts the process. The spike hit this.
- `assert_ours` gates every destructive operation, and the EventKit adapter must additionally re-read `EKEvent.URL` and confirm it matches before deleting. The lookup may be wrong; the guard may not.

---

### Task 1: Extract term settings into a pure module

`meetings.py:24-29` hardcodes Fall 2026 dates and `pipeline.py:20` hardcodes `TERM_YEAR`. A spring installation would silently sync a fall calendar. `meetings.py` is currently IO-free and must stay that way, so the term becomes a value passed in, not a config read.

**Files:**
- Create: `src/canvas_calendar/terms.py`
- Create: `tests/test_terms.py`
- Modify: `src/canvas_calendar/meetings.py:22-29`
- Modify: `src/canvas_calendar/config.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Term` frozen dataclass with `year: int`, `season: str`, `start: date`, `end: date`, `holidays: tuple[date, ...]`; `DEFAULT_TERM`; `load_term() -> Term`; `Term.covers(day: date) -> bool`

- [x] **Step 1: Write the failing tests**

Create `tests/test_terms.py`:

```python
from datetime import date

import pytest

from canvas_calendar.terms import DEFAULT_TERM, Term, term_from_config


def test_default_term_matches_the_shipped_fall_2026_dates():
    assert DEFAULT_TERM.start == date(2026, 8, 24)
    assert DEFAULT_TERM.end == date(2026, 12, 9)
    assert date(2026, 9, 7) in DEFAULT_TERM.holidays          # Labor Day
    assert date(2026, 11, 25) in DEFAULT_TERM.holidays        # Fall Break
    assert len(DEFAULT_TERM.holidays) == 10


def test_covers_reports_whether_a_day_is_in_term():
    assert DEFAULT_TERM.covers(date(2026, 9, 15)) is True
    assert DEFAULT_TERM.covers(date(2026, 8, 1)) is False     # before
    assert DEFAULT_TERM.covers(date(2027, 1, 5)) is False     # after


def test_term_from_config_parses_iso_dates():
    t = term_from_config({
        "year": 2027, "season": "spring",
        "start": "2027-01-19", "end": "2027-05-05",
        "holidays": ["2027-03-20"],
    })
    assert t.year == 2027
    assert t.season == "spring"
    assert t.start == date(2027, 1, 19)
    assert t.holidays == (date(2027, 3, 20),)   # tuple: Term is frozen


def test_term_from_config_rejects_a_backwards_range():
    """A start after the end produces zero meetings and no error. Loud is better."""
    with pytest.raises(ValueError, match="starts after"):
        term_from_config({"year": 2027, "season": "spring",
                          "start": "2027-05-05", "end": "2027-01-19", "holidays": []})


def test_term_from_config_falls_back_to_default_when_absent():
    assert term_from_config(None) is DEFAULT_TERM
```

- [x] **Step 2: Run them and confirm they fail**

```bash
uv run pytest tests/test_terms.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_calendar.terms'`.

- [x] **Step 3: Write the implementation**

Create `src/canvas_calendar/terms.py`:

```python
"""Academic term bounds. Pure: no IO, no config reads.

These were compiled into meetings.py as Fall 2026 constants. That is correct
for exactly one semester, and wrong silently -- a spring install would emit a
fall meeting schedule, or none at all, without complaining. Making the term a
value the caller passes lets config override it while keeping meetings.py
free of IO.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Term:
    year: int
    season: str
    start: date
    end: date
    holidays: tuple[date, ...]

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


# Fall 2026, University of Illinois Urbana-Champaign registrar calendar.
# Cross-checked against MCB 320's module titles, which jump November 20 -> 30.
DEFAULT_TERM = Term(
    year=2026,
    season="fall",
    start=date(2026, 8, 24),
    end=date(2026, 12, 9),
    holidays=(
        date(2026, 9, 7),  # Labor Day
        *[date(2026, 11, d) for d in range(21, 30)],  # Fall Break, Nov 21-29
    ),
)


def term_from_config(block: dict | None) -> Term:
    """Build a Term from the config `term` block, or return the default."""
    if not block:
        return DEFAULT_TERM
    start = date.fromisoformat(block["start"])
    end = date.fromisoformat(block["end"])
    if start > end:
        raise ValueError(f"term starts after it ends: {start} > {end}")
    return Term(
        year=int(block["year"]),
        season=str(block["season"]),
        start=start,
        end=end,
        holidays=tuple(date.fromisoformat(d) for d in block.get("holidays", [])),
    )
```

- [x] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_terms.py -v
```

Expected: 5 passed. Note `DEFAULT_TERM.holidays` is a tuple, so
`len(...) == 10` and `in` both work as the tests expect.

- [x] **Step 5: Point `meetings.py` at the Term without breaking its callers**

In `src/canvas_calendar/meetings.py`, replace the constants block at lines
22-29 with:

```python
from canvas_calendar.terms import DEFAULT_TERM, Term

# Retained as module-level aliases so existing callers and tests keep working.
TERM_START = DEFAULT_TERM.start
TERM_END = DEFAULT_TERM.end
NON_INSTRUCTION: list[date] = list(DEFAULT_TERM.holidays)
```

Then thread the term through the two functions that read those constants.
`excluded_dates` is at line 97 and `build_meetings` at line 107:

```python
def excluded_dates(weekdays: list[int], term: Term = DEFAULT_TERM) -> list[date]:
    return [d for d in term.holidays if d.weekday() in weekdays]
```

and inside `build_meetings`, lines 134-135 become:

```python
                    start_date=section.start_date or term.start,
                    end_date=section.end_date or term.end,
```

with `term: Term = DEFAULT_TERM` added as its last parameter.

Keep the defaults: every existing call site passes no term and must keep
producing identical output.

- [x] **Step 6: Add the config loader**

In `src/canvas_calendar/config.py`, add:

```python
def load_term():
    """Term bounds from config, falling back to the shipped default."""
    from canvas_calendar.terms import term_from_config

    if GRAPH_CONFIG.exists():
        return term_from_config(json.loads(GRAPH_CONFIG.read_text()).get("term"))
    return term_from_config(None)
```

`config.py` imports `json` lazily inside its other functions; follow that
pattern by adding `import json` at the top of `load_term` if the module-level
import is still absent.

- [x] **Step 7: Make Canvas credentials machine-independent**

`config.py:9` hardcodes `~/code/canvas-mcp/.env`, which exists on exactly one
machine. Extend the resolution order in `load_canvas_credentials`, keeping the
legacy path last so the author's machine is unaffected:

```python
CREDENTIALS = Path.home() / ".config" / "canvas-calendar" / "credentials.json"


def load_canvas_credentials(env_path: Path | None = None) -> tuple[str, str]:
    """Return (base_url, token).

    Order: environment, then our own credentials file, then the legacy
    canvas-mcp .env. The legacy path stays last so the original install keeps
    working without being migrated.
    """
    token = os.environ.get("CANVAS_API_TOKEN") or ""
    url = os.environ.get("CANVAS_API_URL") or ""
    if not token and CREDENTIALS.exists():
        import json

        data = json.loads(CREDENTIALS.read_text())
        token = data.get("CANVAS_API_TOKEN", "")
        url = url or data.get("CANVAS_API_URL", "")
    if not token:
        values = dotenv_values(env_path or DEFAULT_ENV)
        token = values.get("CANVAS_API_TOKEN") or ""
        url = url or values.get("CANVAS_API_URL") or ""
    if not token:
        raise RuntimeError(
            "CANVAS_API_TOKEN not found. Run: canvas-calendar setup"
        )
    url = (url or "https://canvas.illinois.edu").rstrip("/")
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url, token
```

Add a test in `tests/test_config.py` (create it if absent) asserting that the
env var wins over `credentials.json`, and that `credentials.json` wins over
the legacy `.env`.

- [x] **Step 8: Full suite, lint, and behaviour check**

```bash
uv run pytest -q && uv run ruff check src tests
uv run canvas-calendar meetings 2>&1 | tail -3
uv run canvas-calendar sync 2>&1 | tail -1
```

Expected: all tests pass; `meetings` still resolves 6 series; `sync` reports
`{'skip': 39, 'noop': 151}` — unchanged.

- [x] **Step 9: Commit**

```bash
git add src/canvas_calendar/terms.py tests/test_terms.py \
        src/canvas_calendar/meetings.py src/canvas_calendar/config.py
git commit -m "refactor: extract term bounds into a pure terms module

Fall 2026 was compiled into meetings.py. A spring install would have
emitted a fall schedule silently."
```

---

### Task 2: Adapter factory and a truthful Protocol

`upsert_recurring` is called by `sync_meetings` but missing from the Protocol, so the Protocol lies about the real interface. Four sites construct `OutlookAdapter` directly, and each hardcodes `"UIUC Assignments"`.

**Files:**
- Modify: `src/canvas_calendar/calendars/base.py` (`CalendarAdapter`)
- Create: `src/canvas_calendar/calendars/factory.py`
- Create: `tests/test_factory.py`
- Modify: `src/canvas_calendar/cli.py:94-101`
- Modify: `src/canvas_calendar/daily.py:118-125`
- Modify: `src/canvas_calendar/config.py`

**Interfaces:**
- Consumes: `load_sync_options` from Task 1's config module
- Produces: `make_adapter(opts: dict) -> tuple[CalendarAdapter, str]` returning the adapter and the resolved calendar id; `load_calendar_backend() -> str`

- [x] **Step 1: Write the failing tests**

Create `tests/test_factory.py`:

```python
import pytest

from canvas_calendar.calendars.factory import UnknownBackend, make_adapter


class FakeAdapter:
    def __init__(self):
        self.asked_for = None

    def ensure_calendar(self, name):
        self.asked_for = name
        return "cal-id-123"


def test_unknown_backend_names_the_setup_command():
    with pytest.raises(UnknownBackend, match="canvas-calendar setup"):
        make_adapter({"calendar_backend": "carrier-pigeon",
                      "assignments_calendar": "X"})


def test_missing_backend_is_an_error_not_a_default():
    """A default of outlook is wrong for new users; a default of eventkit
    would silently change an existing install. Neither is acceptable."""
    with pytest.raises(UnknownBackend, match="canvas-calendar setup"):
        make_adapter({"assignments_calendar": "X"})


def test_factory_resolves_the_calendar_by_configured_name(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(
        "canvas_calendar.calendars.factory._build_outlook", lambda opts: fake
    )
    adapter, cal_id = make_adapter(
        {"calendar_backend": "outlook", "assignments_calendar": "My Calendar"}
    )
    assert adapter is fake
    assert cal_id == "cal-id-123"
    assert fake.asked_for == "My Calendar"
```

- [x] **Step 2: Run them and confirm they fail**

```bash
uv run pytest tests/test_factory.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_calendar.calendars.factory'`.

- [x] **Step 3: Add `upsert_recurring` to the Protocol**

In `src/canvas_calendar/calendars/base.py`, inside `class CalendarAdapter`,
after `list_uids`:

```python
    def upsert_recurring(self, calendar_id: str, meeting) -> str | None:
        """Create or refresh a weekly class-meeting series.

        Returns the backend's event id, or None when the meeting has no
        usable weekday or start time.
        """
        ...
```

- [x] **Step 4: Write the factory**

Create `src/canvas_calendar/calendars/factory.py`:

```python
"""Choose a calendar backend from config.

`calendar_backend` has deliberately no default. Defaulting to "outlook" would
be wrong for every new user; defaulting to "eventkit" would silently move an
existing install onto a different backend. Both failures are silent, so the
key is required and its absence names the command that fixes it.
"""

from __future__ import annotations

from canvas_calendar.calendars.base import CalendarAdapter


class UnknownBackend(RuntimeError):
    """calendar_backend is missing or names a backend we do not have."""


def _build_outlook(opts: dict):
    from canvas_calendar.calendars.graph_auth import GraphAuth
    from canvas_calendar.calendars.outlook import OutlookAdapter
    from canvas_calendar.config import load_graph_client_id

    return OutlookAdapter(
        auth=GraphAuth(client_id=load_graph_client_id()),
        reminder_timed=opts.get("reminder_minutes_timed", 15),
        reminder_all_day=opts.get("reminder_minutes_all_day", 1440),
    )


def _build_eventkit(opts: dict):
    from canvas_calendar.calendars.eventkit import EventKitAdapter

    return EventKitAdapter(
        reminder_timed=opts.get("reminder_minutes_timed", 15),
        reminder_all_day=opts.get("reminder_minutes_all_day", 1440),
    )


_BACKENDS = {"outlook": _build_outlook, "eventkit": _build_eventkit}


def make_adapter(opts: dict) -> tuple[CalendarAdapter, str]:
    """Return (adapter, calendar_id) for the configured backend."""
    name = opts.get("calendar_backend")
    build = _BACKENDS.get(name or "")
    if build is None:
        raise UnknownBackend(
            f"calendar_backend is {name!r}; expected one of "
            f"{sorted(_BACKENDS)}. Run: canvas-calendar setup"
        )
    adapter = build(opts)
    return adapter, adapter.ensure_calendar(opts["assignments_calendar"])
```

- [x] **Step 5: Add the config keys**

In `src/canvas_calendar/config.py`, add to the `defaults` dict in
`load_sync_options`:

```python
        "assignments_calendar": "UIUC Assignments",
        "calendar_backend": None,
```

`None` is the sentinel the factory rejects. The author's config already sets
`"outlook"` explicitly, so their path is unaffected.

- [x] **Step 6: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_factory.py -v
```

Expected: 3 passed.

- [x] **Step 7: Replace the direct constructions**

In `src/canvas_calendar/cli.py`, replace lines 94-101 (the `auth = ...`
through `calendar_id = ...` block) with:

```python
    from canvas_calendar.calendars.factory import make_adapter

    opts = load_sync_options()
    adapter, calendar_id = make_adapter(opts)
```

Remove the now-unused `GraphAuth`, `OutlookAdapter` and
`load_graph_client_id` imports from that file if nothing else uses them.

In `src/canvas_calendar/daily.py`, replace lines 118-125 the same way, keeping
the existing `opts = load_sync_options()` line rather than adding a second.

Leave `run_debrief.py:69` alone. The debrief also calls `todays_events(auth,
…)` and `outlook_unread(auth)`, both of which are Graph-specific; it is
Outlook-only by design and routing it through the factory would imply
otherwise. Change only its hardcoded `"UIUC Assignments"` to
`load_sync_options()["assignments_calendar"]`.

- [x] **Step 8: Full suite, lint, behaviour check**

```bash
uv run pytest -q && uv run ruff check src tests
uv run canvas-calendar sync 2>&1 | tail -1
```

Expected: tests pass; `sync` reports `{'skip': 39, 'noop': 151}` — unchanged.
If the counts differ, the factory resolved a different calendar; stop.

- [x] **Step 9: Commit**

```bash
git add src/canvas_calendar/calendars/base.py \
        src/canvas_calendar/calendars/factory.py tests/test_factory.py \
        src/canvas_calendar/cli.py src/canvas_calendar/daily.py \
        src/canvas_calendar/run_debrief.py src/canvas_calendar/config.py
git commit -m "refactor: adapter factory and a Protocol that tells the truth

upsert_recurring was in the interface but not the Protocol. Four sites
built OutlookAdapter directly and hardcoded the calendar name."
```

---

### Task 3: EventKitAdapter — single events

The core of the plan. Writes into Calendar.app, which already holds the user's
iCloud, Google or Exchange account.

**Files:**
- Create: `src/canvas_calendar/calendars/eventkit.py`
- Create: `tests/test_eventkit_adapter.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: `assert_ours`, `UID_PREFIX` from `calendars/base.py`; `is_end_of_day`, `to_local`, `CHICAGO` from `timeutil.py`
- Produces: `EventKitAdapter` satisfying `CalendarAdapter`; `UID_SCHEME = "x-canvas-calendar:"`

- [x] **Step 1: Add the dependency**

In `pyproject.toml`, change the `dependencies` line to:

```toml
dependencies = [
    "httpx>=0.28",
    "python-dotenv>=1.0",
    "pyobjc-framework-EventKit>=10.0; sys_platform == 'darwin'",
]
```

The environment marker keeps a non-macOS install from failing, even though
macOS is the only supported platform today.

```bash
uv sync
uv run python -c "import EventKit; print('EventKit importable')"
```

- [x] **Step 2: Write the failing tests**

Create `tests/test_eventkit_adapter.py`. These are unit tests against fakes —
no real calendar is touched.

```python
from typing import ClassVar

import pytest

from canvas_calendar.calendars.base import ForeignEventError
from canvas_calendar.calendars.eventkit import UID_SCHEME, EventKitAdapter


class FakeURL:
    def __init__(self, s):
        self._s = s

    def absoluteString(self):
        return self._s


class FakeEvent:
    def __init__(self, uid=None, ident="ek-1"):
        self._url = FakeURL(UID_SCHEME + uid) if uid else None
        self._ident = ident
        self.removed = False

    def URL(self):
        return self._url

    def eventIdentifier(self):
        return self._ident


class FakeAdapter(EventKitAdapter):
    """Bypasses __init__ so no EKEventStore is created."""

    def __init__(self, events):
        self._reminder_timed = 15
        self._reminder_all_day = 1440
        self._store = None
        self._index_cache = {}
        self._events = events

    def _index(self, calendar_id):
        return {u: e for u, e in self._events.items()}


def test_uid_is_read_back_from_the_event_url():
    a = FakeAdapter({"cc-1652210": FakeEvent("cc-1652210")})
    assert a.list_uids("cal") == {"cc-1652210"}


def test_events_without_our_scheme_are_invisible():
    """A user's own event must never appear in list_uids, or prune deletes it."""
    a = FakeAdapter({})
    a._events = {}
    foreign = FakeEvent(None)
    assert a._uid_of(foreign) is None


def test_uid_of_ignores_a_foreign_url_scheme():
    a = FakeAdapter({})
    ev = FakeEvent(None)
    ev._url = FakeURL("https://example.com/meeting")
    assert a._uid_of(ev) is None


def test_delete_refuses_a_uid_that_is_not_ours():
    a = FakeAdapter({})
    with pytest.raises(ForeignEventError):
        a.delete("cal", "not-ours-123")


def test_delete_refuses_when_the_event_url_disagrees(monkeypatch):
    """State said cc-1; the event on the calendar says cc-2. Refuse."""
    mismatched = FakeEvent("cc-2")
    a = FakeAdapter({"cc-1": mismatched})
    with pytest.raises(ForeignEventError, match="does not carry"):
        a.delete("cal", "cc-1")
```

- [x] **Step 3: Run them and confirm they fail**

```bash
uv run pytest tests/test_eventkit_adapter.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_calendar.calendars.eventkit'`.

- [x] **Step 4: Write the adapter**

Create `src/canvas_calendar/calendars/eventkit.py`:

```python
"""macOS EventKit calendar adapter.

Writes into Calendar.app, which already holds whichever account the user
added in System Settings -- iCloud, Google, or Exchange. One adapter therefore
serves all three backends without any OAuth of its own, which is the entire
reason this exists: distributing a Google Calendar integration would mean an
unverified OAuth app whose refresh tokens expire every seven days.

Three facts verified by spike on 2026-08-31 (macOS 26.5.2) shape this file:

- `EKEvent.URL` survives a save/refetch on both CalDAV/iCloud and Exchange,
  so it is where our UID lives. It must be a valid URI -- NSURL rejects a bare
  "cc-..." string -- hence the x-canvas-calendar scheme.
- A completion block that returns anything other than None aborts the process
  with "did not return None, expecting void return value".
- EKSource and EKCalendar handles do not survive EKEventStore.reset(). Never
  cache them across one.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from canvas_calendar.calendars.base import ForeignEventError, assert_ours
from canvas_calendar.models import Assignment
from canvas_calendar.timeutil import CHICAGO, is_end_of_day, to_local

UID_SCHEME = "x-canvas-calendar:"
_ACCESS_TIMEOUT = 60


class CalendarAccessDenied(RuntimeError):
    """macOS refused Calendar access, or nobody answered the prompt."""


class CalendarNotWritable(RuntimeError):
    """The named calendar exists but refuses writes, or cannot be created."""


def _request_access(store) -> None:
    """Block until macOS answers. The first call from a given process context
    shows a prompt; later ones return immediately from the TCC database."""
    done = threading.Event()
    result = {}

    def completion(granted, error):
        # MUST return None. A block returning a value aborts the process.
        result["granted"] = bool(granted)
        result["error"] = error
        done.set()

    if hasattr(store, "requestFullAccessToEventsWithCompletion_"):
        store.requestFullAccessToEventsWithCompletion_(completion)
    else:  # macOS < 14
        import EventKit as EK

        store.requestAccessToEntityType_completion_(EK.EKEntityTypeEvent, completion)

    if not done.wait(_ACCESS_TIMEOUT):
        raise CalendarAccessDenied(
            "timed out waiting for Calendar permission. If this ran from a "
            "LaunchAgent, grant access once interactively: canvas-calendar setup"
        )
    if not result.get("granted"):
        raise CalendarAccessDenied(
            f"Calendar access denied ({result.get('error')}). Grant it in "
            "System Settings > Privacy & Security > Calendars."
        )


class EventKitAdapter:
    def __init__(self, reminder_timed: int = 15, reminder_all_day: int = 1440) -> None:
        import EventKit as EK

        self._ek = EK
        self._reminder_timed = reminder_timed
        self._reminder_all_day = reminder_all_day
        self._store = EK.EKEventStore.alloc().init()
        _request_access(self._store)
        self._index_cache: dict[str, dict] = {}

    # -- calendars ---------------------------------------------------------

    def ensure_calendar(self, name: str) -> str:
        """Return the identifier of a writable calendar titled `name`."""
        for cal in self._store.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or []:
            if cal.title() == name:
                if not cal.allowsContentModifications():
                    raise CalendarNotWritable(
                        f"calendar {name!r} is read-only (subscribed or shared). "
                        "Pick a different one: canvas-calendar setup"
                    )
                return cal.calendarIdentifier()
        return self._create_calendar(name)

    def _create_calendar(self, name: str) -> str:
        source = self._writable_source()
        cal = self._ek.EKCalendar.calendarForEntityType_eventStore_(
            self._ek.EKEntityTypeEvent, self._store
        )
        cal.setTitle_(name)
        cal.setSource_(source)
        ok, err = self._store.saveCalendar_commit_error_(cal, True, None)
        if not ok:
            raise CalendarNotWritable(
                f"could not create calendar {name!r} on {source.title()!r}: {err}. "
                f"Create it manually in that account, then re-run."
            )
        return cal.calendarIdentifier()

    def _writable_source(self):
        """Prefer the source that already holds writable calendars."""
        best = None
        for src in self._store.sources():
            cals = src.calendarsForEntityType_(self._ek.EKEntityTypeEvent) or []
            if any(c.allowsContentModifications() for c in cals):
                best = best or src
        if best is None:
            raise CalendarNotWritable(
                "no writable calendar account found. Add your calendar account "
                "in System Settings > Internet Accounts first."
            )
        return best

    def _calendar(self, calendar_id: str):
        cal = self._store.calendarWithIdentifier_(calendar_id)
        if cal is None:
            raise CalendarNotWritable(f"calendar {calendar_id!r} no longer exists")
        return cal

    # -- uid indexing ------------------------------------------------------

    def _uid_of(self, event) -> str | None:
        """Our UID, or None for anything we did not create."""
        url = event.URL()
        if url is None:
            return None
        s = url.absoluteString() or ""
        if not s.startswith(UID_SCHEME):
            return None
        return s[len(UID_SCHEME):] or None

    def _index(self, calendar_id: str) -> dict:
        """uid -> EKEvent for the whole term window, built once per run.

        One predicate query rather than a lookup per event, and it removes any
        need for a state.db schema change to remember backend ids.
        """
        if calendar_id in self._index_cache:
            return self._index_cache[calendar_id]
        from Foundation import NSDate

        cal = self._calendar(calendar_id)
        # Generous window: a term plus margin either side. EventKit predicates
        # require bounds, and an event outside them is invisible -- which would
        # read as "absent" and get re-created as a duplicate.
        pred = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            NSDate.dateWithTimeIntervalSinceNow_(-86400 * 400),
            NSDate.dateWithTimeIntervalSinceNow_(86400 * 400),
            [cal],
        )
        index = {}
        for ev in self._store.eventsMatchingPredicate_(pred) or []:
            uid = self._uid_of(ev)
            if uid:
                index[uid] = ev
        self._index_cache[calendar_id] = index
        return index

    def list_uids(self, calendar_id: str) -> set[str]:
        return set(self._index(calendar_id))

    # -- writes ------------------------------------------------------------

    def upsert(self, calendar_id: str, uid: str, assignment: Assignment) -> None:
        from Foundation import NSURL

        assert_ours(uid)
        existing = self._index(calendar_id).get(uid)
        ev = existing or self._ek.EKEvent.eventWithEventStore_(self._store)
        ev.setCalendar_(self._calendar(calendar_id))
        ev.setURL_(NSURL.URLWithString_(UID_SCHEME + uid))
        self._apply_fields(ev, assignment)

        ok, err = self._store.saveEvent_span_commit_error_(
            ev, self._ek.EKSpanThisEvent, True, None
        )
        if not ok:
            raise RuntimeError(f"saving {uid}: {err}")
        self._index_cache.setdefault(calendar_id, {})[uid] = ev

    def _apply_fields(self, ev, a: Assignment) -> None:
        from Foundation import NSTimeZone

        subject = f"{a.course}: {a.name}"
        ev.setTitle_(subject[:250])
        ev.setNotes_(
            f"Course: {a.course}\nPoints: {a.points:g}\n"
            + (f"Date derived from {a.provenance}\n" if a.provenance else "")
            + "Synced by canvas-calendar. Edits here will be overwritten."
        )
        ev.setTimeZone_(NSTimeZone.timeZoneWithName_("America/Chicago"))

        local = to_local(a.due_at)
        for alarm in list(ev.alarms() or []):
            ev.removeAlarm_(alarm)

        if is_end_of_day(a.due_at):
            day = local.replace(hour=0, minute=0, second=0, microsecond=0)
            ev.setAllDay_(True)
            ev.setStartDate_(_ns(day))
            ev.setEndDate_(_ns(day + timedelta(days=1)))
            offset = -self._reminder_all_day * 60
        else:
            ev.setAllDay_(False)
            ev.setStartDate_(_ns(local))
            ev.setEndDate_(_ns(local + timedelta(minutes=30)))
            offset = -self._reminder_timed * 60
        ev.addAlarm_(self._ek.EKAlarm.alarmWithRelativeOffset_(offset))

    def delete(self, calendar_id: str, uid: str) -> None:
        assert_ours(uid)
        ev = self._index(calendar_id).get(uid)
        if ev is None:
            return  # already gone; nothing to do
        # Independent of any index or state row: confirm the event itself
        # still claims this uid before removing it.
        if self._uid_of(ev) != uid:
            raise ForeignEventError(
                f"event for {uid} does not carry our uid; refusing to delete"
            )
        ok, err = self._store.removeEvent_span_error_(ev, self._ek.EKSpanThisEvent, None)
        if not ok:
            raise RuntimeError(f"deleting {uid}: {err}")
        self._index_cache.get(calendar_id, {}).pop(uid, None)


def _ns(dt: datetime):
    """Aware datetime -> NSDate."""
    from Foundation import NSDate

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=CHICAGO)
    return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())
```

- [x] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_eventkit_adapter.py -v
```

Expected: 5 passed.

- [x] **Step 6: Full suite, lint, and confirm Outlook is untouched**

```bash
uv run pytest -q && uv run ruff check src tests
uv run canvas-calendar sync 2>&1 | tail -1
```

Expected: `{'skip': 39, 'noop': 151}`. The EventKit module is imported only by
the factory's `_build_eventkit`, which the outlook path never calls.

- [x] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src/canvas_calendar/calendars/eventkit.py \
        tests/test_eventkit_adapter.py
git commit -m "feat: EventKit calendar adapter for single events

Writes through Calendar.app, so iCloud, Google and Exchange are all
reachable without any OAuth of our own. UID lives in EKEvent.URL, verified
by spike to survive a CalDAV and Exchange round-trip."
```

---

### Task 4: EventKitAdapter — recurring class meetings

**Files:**
- Modify: `src/canvas_calendar/calendars/eventkit.py`
- Modify: `tests/test_eventkit_adapter.py`

**Interfaces:**
- Consumes: `ClassMeeting` from `meetings.py`; `excluded_dates`, `first_occurrence`, `parse_clock` from the same module
- Produces: `EventKitAdapter.upsert_recurring(calendar_id, meeting) -> str | None`

- [x] **Step 1: Write the failing test**

Append to `tests/test_eventkit_adapter.py`:

```python
def test_upsert_recurring_refuses_a_foreign_uid():
    a = FakeAdapter({})
    class M:
        uid = "not-ours"
    with pytest.raises(ForeignEventError):
        a.upsert_recurring("cal", M())


def test_weekday_mapping_matches_eventkit_numbering():
    """EKWeekday is 1-based from Sunday; Python weekday() is 0-based from
    Monday. Getting this wrong shifts every class by a day."""
    from canvas_calendar.calendars.eventkit import ek_weekday

    assert ek_weekday(0) == 2   # Monday
    assert ek_weekday(2) == 4   # Wednesday
    assert ek_weekday(4) == 6   # Friday
    assert ek_weekday(6) == 1   # Sunday
```

- [x] **Step 2: Run and confirm they fail**

```bash
uv run pytest tests/test_eventkit_adapter.py -k "recurring or weekday" -v
```

Expected: `ImportError: cannot import name 'ek_weekday'` and an
`AttributeError` for `upsert_recurring`.

- [x] **Step 3: Implement**

Add to `src/canvas_calendar/calendars/eventkit.py`:

```python
def ek_weekday(python_weekday: int) -> int:
    """Python weekday (Mon=0) -> EKWeekday (Sunday=1).

    Two different conventions for the same concept; conflating them shifts
    every class meeting by one day, which looks plausible on a calendar and
    is therefore easy to miss.
    """
    return (python_weekday + 1) % 7 + 1
```

and this method on `EventKitAdapter`:

```python
    def upsert_recurring(self, calendar_id: str, meeting) -> str | None:
        from Foundation import NSTimeZone

        from canvas_calendar.meetings import excluded_dates, first_occurrence, parse_clock

        assert_ours(meeting.uid)
        weekdays = meeting.meeting.weekdays()
        start_clock = parse_clock(meeting.meeting.start)
        end_clock = parse_clock(meeting.meeting.end)
        if not weekdays or start_clock is None:
            return None
        begins = first_occurrence(weekdays, meeting.start_date)
        if begins is None:
            return None

        # Replace wholesale rather than patch: editing a live series in place
        # is unreliable across backends, exactly as it is on Graph.
        existing = self._index(calendar_id).get(meeting.uid)
        if existing is not None:
            self._store.removeEvent_span_error_(
                existing, self._ek.EKSpanFutureEvents, None
            )
            self._index_cache.get(calendar_id, {}).pop(meeting.uid, None)

        from Foundation import NSURL

        ev = self._ek.EKEvent.eventWithEventStore_(self._store)
        ev.setCalendar_(self._calendar(calendar_id))
        ev.setURL_(NSURL.URLWithString_(UID_SCHEME + meeting.uid))
        ev.setTitle_(meeting.title)
        ev.setLocation_(meeting.location)
        ev.setNotes_(
            f"{meeting.section}\nInstructor: {meeting.meeting.instructor}\n"
            "Synced by canvas-calendar. Edits here will be overwritten."
        )
        ev.setTimeZone_(NSTimeZone.timeZoneWithName_("America/Chicago"))
        ev.setAllDay_(False)
        ev.setStartDate_(_ns(datetime.combine(begins, start_clock, tzinfo=CHICAGO)))
        ev.setEndDate_(
            _ns(datetime.combine(begins, end_clock or start_clock, tzinfo=CHICAGO))
        )
        ev.addAlarm_(self._ek.EKAlarm.alarmWithRelativeOffset_(-15 * 60))

        days = [
            self._ek.EKRecurrenceDayOfWeek.dayOfWeek_(ek_weekday(w)) for w in weekdays
        ]
        rule = self._ek.EKRecurrenceRule.alloc()
        rule = rule.initRecurrenceWithFrequency_interval_daysOfTheWeek_daysOfTheMonth_monthsOfTheYear_weeksOfTheYear_daysOfTheYear_setPositions_end_(
            self._ek.EKRecurrenceFrequencyWeekly, 1, days,
            None, None, None, None, None,
            self._ek.EKRecurrenceEnd.recurrenceEndWithEndDate_(
                _ns(datetime.combine(meeting.end_date, start_clock, tzinfo=CHICAGO))
            ),
        )
        ev.setRecurrenceRules_([rule])

        ok, err = self._store.saveEvent_span_commit_error_(
            ev, self._ek.EKSpanFutureEvents, True, None
        )
        if not ok:
            raise RuntimeError(f"saving series {meeting.uid}: {err}")

        self._cancel_occurrences(calendar_id, ev, excluded_dates(weekdays))
        return ev.eventIdentifier()

    def _cancel_occurrences(self, calendar_id: str, series, days: list) -> int:
        """Delete individual occurrences on non-instruction days.

        EventKit has no EXDATE at creation time, the same limitation Graph
        has. Without this a lecture lands on Thanksgiving.
        """
        from Foundation import NSDate

        if not days:
            return 0
        removed = 0
        cal = self._calendar(calendar_id)
        for day in days:
            start = datetime.combine(day, datetime.min.time(), tzinfo=CHICAGO)
            pred = self._store.predicateForEventsWithStartDate_endDate_calendars_(
                _ns(start), _ns(start + timedelta(days=1)), [cal]
            )
            for occ in self._store.eventsMatchingPredicate_(pred) or []:
                if self._uid_of(occ) != self._uid_of(series):
                    continue
                ok, _ = self._store.removeEvent_span_error_(
                    occ, self._ek.EKSpanThisEvent, None
                )
                removed += int(bool(ok))
        return removed
```

Note `NSDate` is imported in `_cancel_occurrences` for symmetry with the rest
of the file; remove the import if ruff flags it as unused.

- [x] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_eventkit_adapter.py -v
```

Expected: 7 passed.

- [x] **Step 5: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/calendars/eventkit.py tests/test_eventkit_adapter.py
git commit -m "feat: recurring class meetings in the EventKit adapter

EKWeekday is 1-based from Sunday, Python's is 0-based from Monday.
Conflating them shifts every class by a day and looks plausible."
```

---

### Task 5: Un-couple `sync_meetings` from Outlook, and fix its 401 handling

`sync_meetings.py` imports `GraphAuth` and `OutlookAdapter` directly, hardcodes
the Course Explorer path `/2026/fall` at line 20, and calls Canvas through raw
`httpx` — bypassing `TokenExpired`, so a 401 surfaces as a JSON decode error
instead of the clean exit-code-2 path.

**Files:**
- Modify: `src/canvas_calendar/sync_meetings.py`
- Modify: `tests/test_meetings.py`

**Interfaces:**
- Consumes: `make_adapter` (Task 2), `load_term` (Task 1), `CanvasClient`
- Produces: no new public interface

- [x] **Step 1: Write the failing test**

Append to `tests/test_meetings.py`:

```python
def test_course_explorer_url_follows_the_configured_term():
    from canvas_calendar.sync_meetings import explorer_base
    from canvas_calendar.terms import Term
    from datetime import date

    t = Term(year=2027, season="spring", start=date(2027, 1, 19),
             end=date(2027, 5, 5), holidays=())
    assert explorer_base(t).endswith("/2027/spring")
```

- [x] **Step 2: Run and confirm it fails**

```bash
uv run pytest tests/test_meetings.py -k explorer -v
```

Expected: `ImportError: cannot import name 'explorer_base'`.

- [x] **Step 3: Implement**

In `src/canvas_calendar/sync_meetings.py`, replace the module-level `CX`
constant at line 20 with:

```python
_CX_ROOT = "https://courses.illinois.edu/cisapp/explorer/schedule"


def explorer_base(term) -> str:
    return f"{_CX_ROOT}/{term.year}/{term.season}"
```

Replace the three raw `httpx.get` calls to Canvas with `CanvasClient`:

```python
    from canvas_calendar.canvas.client import CanvasClient

    base, tok = load_canvas_credentials()
    client = CanvasClient(base, tok)
    courses = client.list_courses()
    enrollments = client._get_all("/users/self/enrollments", **{"state[]": "active"})
```

Leave the per-section `GET /sections/{id}` call on `httpx` if adding a client
method is more churn than it is worth; the important one is the paged list
call that can 401.

Replace the adapter construction in `sync_meetings()`:

```python
    from canvas_calendar.calendars.factory import make_adapter
    from canvas_calendar.config import load_sync_options

    opts = load_sync_options()
    adapter, cal = make_adapter(opts)
```

and delete the `calendar_name` parameter's default, taking it from
`opts["assignments_calendar"]` instead.

Thread the term through: `fetch()` uses `explorer_base(load_term())`.

- [x] **Step 4: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_meetings.py -v
```

- [x] **Step 5: Behaviour check against live data**

```bash
uv run canvas-calendar meetings 2>&1 | tail -10
```

Expected: the same 6 series as before, with the same CRNs, times and rooms.
Any difference means the Course Explorer URL or the enrollment query changed
shape; stop and compare.

- [x] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/sync_meetings.py tests/test_meetings.py
git commit -m "refactor: sync_meetings uses the factory, CanvasClient and the term

It bypassed TokenExpired via raw httpx, so a 401 mid-sync surfaced as a
JSON decode error rather than the clean exit-code-2 path."
```

---

### Task 6: Live integration test against a scratch calendar

Everything above is unit-tested against fakes. One test must touch a real
calendar, or the adapter is unverified where it matters.

**Files:**
- Create: `tests/test_eventkit_live.py`
- Modify: `pyproject.toml` (pytest markers)

**Interfaces:**
- Consumes: `EventKitAdapter`
- Produces: nothing

- [x] **Step 1: Register the marker**

In `pyproject.toml`, under `[tool.pytest.ini_options]`:

```toml
markers = ["live: touches a real calendar; deselected by default"]
addopts = "-m 'not live'"
```

- [x] **Step 2: Write the test**

Create `tests/test_eventkit_live.py`:

```python
"""Touches a real calendar. Run explicitly:  uv run pytest -m live

Creates its own scratch calendar and removes it, so it never writes into
'UIUC Assignments' or any calendar the user cares about.
"""

from datetime import datetime, timedelta

import pytest

from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import CHICAGO

pytestmark = pytest.mark.live

SCRATCH = "canvas-calendar test (safe to delete)"


@pytest.fixture
def adapter():
    from canvas_calendar.calendars.eventkit import EventKitAdapter

    a = EventKitAdapter()
    cal_id = a.ensure_calendar(SCRATCH)
    yield a, cal_id
    cal = a._store.calendarWithIdentifier_(cal_id)
    if cal is not None:
        a._store.removeCalendar_commit_error_(cal, True, None)


def test_roundtrip_create_read_delete(adapter):
    a, cal_id = adapter
    item = Assignment(
        canvas_id=999001, name="Spike Homework", points=10.0,
        due_at=datetime.now(CHICAGO) + timedelta(days=3),
        course="MCB 999", source=Source.CANVAS,
    )
    a.upsert(cal_id, item.uid, item)
    a._index_cache.clear()
    assert item.uid in a.list_uids(cal_id), "uid did not survive the round-trip"

    a.delete(cal_id, item.uid)
    a._index_cache.clear()
    assert item.uid not in a.list_uids(cal_id)


def test_foreign_events_are_never_listed(adapter):
    """An event the user made must be invisible to us, or prune deletes it."""
    import EventKit as EK
    from Foundation import NSDate

    a, cal_id = adapter
    ev = EK.EKEvent.eventWithEventStore_(a._store)
    ev.setTitle_("the user's own event")
    ev.setCalendar_(a._store.calendarWithIdentifier_(cal_id))
    ev.setStartDate_(NSDate.dateWithTimeIntervalSinceNow_(86400))
    ev.setEndDate_(NSDate.dateWithTimeIntervalSinceNow_(90000))
    ok, err = a._store.saveEvent_span_commit_error_(ev, EK.EKSpanThisEvent, True, None)
    assert ok, err

    a._index_cache.clear()
    assert a.list_uids(cal_id) == set(), "a foreign event leaked into list_uids"
```

- [x] **Step 3: Run it explicitly**

```bash
uv run pytest -m live -v
```

Expected: 2 passed. If `ensure_calendar` raises `CalendarNotWritable`, the
account refuses calendar creation — record which source, since that is the
Google case the spike could not test.

- [x] **Step 4: Confirm the default run still deselects it**

```bash
uv run pytest -q
```

Expected: the live tests are not collected; the count matches the previous
task's total.

- [x] **Step 5: Confirm no scratch calendar survived**

```bash
uv run python -c "
import threading, EventKit as EK
s = EK.EKEventStore.alloc().init(); d = threading.Event()
def cb(ok, e): d.set()
s.requestFullAccessToEventsWithCompletion_(cb); d.wait(30)
names = [c.title() for c in s.calendarsForEntityType_(EK.EKEntityTypeEvent) or []]
print('scratch left behind:', [n for n in names if 'canvas-calendar test' in n] or 'none')
"
```

Expected: `none`.

- [x] **Step 6: Commit**

```bash
git add tests/test_eventkit_live.py pyproject.toml
git commit -m "test: live EventKit round-trip against a scratch calendar

Deselected by default; run with -m live. Asserts a foreign event never
appears in list_uids, since that is what would let prune delete it."
```

---

## Notes for the executor

- **Stop if `uv run canvas-calendar sync` ever reports anything other than the counts from the previous task.** The author's Outlook path is a hard constraint, and every task has a check for it.
- The EventKit adapter is imported lazily inside `_build_eventkit`, so nothing in the outlook path can be broken by an EventKit error.
- If a test hangs for 60s and then raises `CalendarAccessDenied`, macOS is showing a permission prompt somewhere — answer it, then re-run.
- Do not add a `backend_id` column to `state.db`. The uid→event index makes it unnecessary, and migrating a live database with 151 rows for no functional gain is a bad trade.
- **Google's CalDAV refuses calendar creation** (`EKErrorDomain Code=17`), confirmed by direct test on 2026-08-31. `_create_calendar` raising `CalendarNotWritable` there is the expected path, not a bug — its message already tells the user to create the calendar in that account and re-run. iCloud and Exchange both allow creation. Writing *events* works on all three.
