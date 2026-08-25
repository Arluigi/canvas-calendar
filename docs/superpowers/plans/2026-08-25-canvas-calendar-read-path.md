# Canvas Calendar — Read Path Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete read path — fetch class meetings from UIUC Course Explorer and assignments from Canvas, resolve dates, classify them, and print a correct schedule via `canvas-calendar preview`.

**Architecture:** Layered and side-effect-free. HTTP clients return raw payloads, parsers turn them into dataclasses, and pure functions do the date and classification logic. Nothing writes to a calendar in this plan, so every rule is unit-testable against recorded fixtures with no credentials involved.

**Tech Stack:** Python 3.13, `uv`, `httpx`, `pytest`, `zoneinfo` (stdlib), `python-dotenv`, `ruff`.

**Spec:** `docs/superpowers/specs/2026-08-25-canvas-calendar-design.md`

**Scope:** This is plan 1 of 3. Plan 2 adds SQLite state, the diff engine, and calendar adapters. Plan 3 adds the LaunchAgent, digest, and drift check.

---

## Chunk 1: Foundation

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/canvas_calendar/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "canvas-calendar"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = ["httpx>=0.28", "python-dotenv>=1.0"]

[project.scripts]
canvas-calendar = "canvas_calendar.cli:main"

[dependency-groups]
dev = ["pytest>=8.0", "pytest-cov>=5.0", "ruff>=0.15"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/canvas_calendar"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

- [ ] **Step 2: Create package markers**

```bash
mkdir -p src/canvas_calendar tests/fixtures
touch src/canvas_calendar/__init__.py tests/__init__.py
```

- [ ] **Step 3: Install and verify**

Run: `uv sync && uv run pytest --collect-only`
Expected: exits 0, "no tests ran" (no tests exist yet).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/canvas_calendar/__init__.py tests/__init__.py
git commit -m "chore: scaffold canvas-calendar package"
```

---

### Task 2: Time handling and DST

This is the highest-risk pure-logic component. The semester crosses the CDT→CST
boundary on 2026-11-01, and every deadline after it shifts by an hour under a
naive implementation. Build it first, with real observed timestamps as fixtures.

**Files:**
- Create: `src/canvas_calendar/timeutil.py`
- Test: `tests/test_timeutil.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_timeutil.py
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from canvas_calendar.timeutil import CHICAGO, is_end_of_day, parse_canvas_ts, to_local


def test_parses_canvas_utc_timestamp():
    dt = parse_canvas_ts("2026-08-25T19:00:00Z")
    assert dt.tzinfo is not None
    assert dt.year == 2026 and dt.hour == 19


def test_converts_to_central_during_cdt():
    # Real MCB 244 reading deadline: 19:00Z in August is 14:00 CDT (UTC-5)
    local = to_local(parse_canvas_ts("2026-08-25T19:00:00Z"))
    assert (local.hour, local.minute) == (14, 0)
    assert local.utcoffset().total_seconds() == -5 * 3600


def test_converts_to_central_during_cst():
    # Real MCB 354 deadline: 05:59Z in November is 23:59 CST (UTC-6)
    local = to_local(parse_canvas_ts("2026-11-07T05:59:00Z"))
    assert (local.month, local.day) == (11, 6)
    assert (local.hour, local.minute) == (23, 59)
    assert local.utcoffset().total_seconds() == -6 * 3600


def test_dst_boundary_keeps_both_sides_at_local_2359():
    """The bug this guards: 04:59Z and 05:59Z are the SAME local wall time,
    on opposite sides of the Nov 1 transition. A fixed -5 offset breaks one."""
    before = to_local(parse_canvas_ts("2026-10-29T04:59:00Z"))  # CDT
    after = to_local(parse_canvas_ts("2026-11-07T05:59:00Z"))   # CST
    assert before.hour == after.hour == 23
    assert before.minute == after.minute == 59


@pytest.mark.parametrize(
    "ts,expected",
    [
        ("2026-10-29T04:59:00Z", True),   # 23:59 CDT -> end of day
        ("2026-11-07T05:59:00Z", True),   # 23:59 CST -> end of day
        ("2026-09-01T04:59:59Z", True),   # 23:59:59 CDT
        ("2026-08-25T19:00:00Z", False),  # 14:00 -> real timed deadline
        ("2026-08-31T14:00:00Z", False),  # 09:00 -> real timed deadline
    ],
)
def test_end_of_day_classification(ts, expected):
    assert is_end_of_day(parse_canvas_ts(ts)) is expected


def test_end_of_day_uses_local_date_not_utc_date():
    """04:59:59Z on Sep 1 is Aug 31 locally. The all-day event must land Aug 31."""
    local = to_local(parse_canvas_ts("2026-09-01T04:59:59Z"))
    assert (local.month, local.day) == (8, 31)


def test_chicago_is_a_real_tz_not_an_offset():
    assert CHICAGO == ZoneInfo("America/Chicago")
    jan = datetime(2026, 1, 15, 12, tzinfo=CHICAGO)
    jul = datetime(2026, 7, 15, 12, tzinfo=CHICAGO)
    assert jan.utcoffset() != jul.utcoffset()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_timeutil.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'canvas_calendar.timeutil'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/timeutil.py
"""Timezone handling for Canvas timestamps.

Canvas returns UTC. Courses run in America/Chicago, which crosses the CDT->CST
boundary on 2026-11-01 -- mid-semester. Never use a fixed UTC offset here: an
11:59PM local deadline arrives as 04:59Z before the transition and 05:59Z after,
and a hardcoded -5 silently shifts every November deadline by an hour.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")

# A deadline within this window of local midnight is administrative
# ("submit by end of day"), not a real timed deadline.
END_OF_DAY_WINDOW = timedelta(minutes=5)


def parse_canvas_ts(raw: str) -> datetime:
    """Parse a Canvas ISO-8601 UTC timestamp into an aware datetime."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def to_local(dt: datetime) -> datetime:
    """Convert an aware datetime to America/Chicago via the tz database."""
    if dt.tzinfo is None:
        raise ValueError("refusing to localize a naive datetime")
    return dt.astimezone(CHICAGO)


def is_end_of_day(dt: datetime) -> bool:
    """True if the deadline falls in the last few minutes of its local day."""
    local = to_local(dt)
    midnight = (local + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return midnight - local <= END_OF_DAY_WINDOW
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_timeutil.py -v`
Expected: PASS, 11 passed.

- [ ] **Step 5: Commit**

```bash
git add src/canvas_calendar/timeutil.py tests/test_timeutil.py
git commit -m "feat: timezone handling with DST-safe end-of-day classification"
```

---

### Task 3: Domain models

**Files:**
- Create: `src/canvas_calendar/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
from canvas_calendar.models import Assignment, CourseRef, Meeting, Source


def test_course_ref_parses_canvas_course_code():
    ref = CourseRef.from_canvas_code("mcb_244_120268_262964")
    assert ref.subject == "MCB"
    assert ref.number == "244"
    assert ref.term_id == "120268"


def test_course_ref_rejects_malformed_code():
    import pytest

    with pytest.raises(ValueError, match="unrecognized"):
        CourseRef.from_canvas_code("not-a-course-code")


def test_course_ref_handles_open_course_codes():
    """Non-departmental courses (provost_unihigh_open_219093) are not term
    courses and must be rejected rather than silently mis-parsed."""
    import pytest

    with pytest.raises(ValueError):
        CourseRef.from_canvas_code("provost_unihigh_open_219093")


def test_meeting_days_expand_to_weekdays():
    m = Meeting(days="TR", start="02:00PM", end="03:20PM", building="Foellinger",
                room="AUD", kind="Lecture", instructor="Garcia, M")
    assert m.weekdays() == [1, 3]  # Tue, Thu


def test_meeting_handles_mwf():
    m = Meeting(days="MWF", start="09:00AM", end="09:50AM", building="Burrill",
                room="124", kind="Lecture", instructor="X")
    assert m.weekdays() == [0, 2, 4]


def test_assignment_defaults_to_canvas_source():
    a = Assignment(canvas_id=1, name="X", points=10.0, due_at=None, course="MCB 244")
    assert a.source is Source.CANVAS
    assert a.uid == "cc-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/models.py
"""Core dataclasses. No I/O, no side effects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

# e.g. mcb_244_120268_262964 -> subject mcb, number 244, term 120268
_COURSE_CODE = re.compile(r"^([a-z]{2,4})_(\d{3})_(\d{6})_\d+$", re.I)

_DAY_CODES = {"M": 0, "T": 1, "W": 2, "R": 3, "F": 4, "S": 5, "U": 6}


class Source(str, Enum):
    CANVAS = "canvas"       # instructor-set due date
    EXTRACTED = "extracted"  # parsed from module title / SubHeader text
    UNRESOLVED = "unresolved"  # no date available; digest only


@dataclass(frozen=True)
class CourseRef:
    subject: str
    number: str
    term_id: str

    @classmethod
    def from_canvas_code(cls, code: str) -> CourseRef:
        m = _COURSE_CODE.match(code)
        if not m:
            raise ValueError(f"unrecognized course code: {code!r}")
        subject, number, term_id = m.groups()
        return cls(subject=subject.upper(), number=number, term_id=term_id)


@dataclass(frozen=True)
class Meeting:
    days: str
    start: str
    end: str
    building: str
    room: str
    kind: str
    instructor: str

    def weekdays(self) -> list[int]:
        """Course Explorer day codes -> Python weekday ints (Mon=0)."""
        return [_DAY_CODES[c] for c in self.days if c in _DAY_CODES]


@dataclass
class Assignment:
    canvas_id: int
    name: str
    points: float
    due_at: datetime | None
    course: str
    source: Source = Source.CANVAS
    provenance: str = ""  # for extracted dates: the text the date came from
    module: str = ""

    @property
    def uid(self) -> str:
        """Stable calendar UID. The cc- prefix is what the never-delete-foreign-
        events check tests against, so it must never change."""
        return f"cc-{self.canvas_id}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v`
Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/canvas_calendar/models.py tests/test_models.py
git commit -m "feat: domain models with course-code parsing"
```

---

## Chunk 2: Course Explorer

### Task 4: Course Explorer parser

Parse before fetching — the parser is pure and needs no network.

**Files:**
- Create: `src/canvas_calendar/catalog/__init__.py`
- Create: `src/canvas_calendar/catalog/parser.py`
- Create: `tests/fixtures/mcb244_section.xml`
- Test: `tests/test_catalog_parser.py`

- [ ] **Step 1: Record the real fixture**

```bash
mkdir -p src/canvas_calendar/catalog && touch src/canvas_calendar/catalog/__init__.py
curl -sS -A "canvas-calendar/0.1" \
  "https://courses.illinois.edu/cisapp/explorer/schedule/2026/fall/MCB/244/56301.xml" \
  -o tests/fixtures/mcb244_section.xml
grep -c "meetings" tests/fixtures/mcb244_section.xml
```

Expected: file written, grep finds the meetings block.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_catalog_parser.py
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_catalog_parser.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 4: Write minimal implementation**

```python
# src/canvas_calendar/catalog/parser.py
"""Parse UIUC Course Explorer section XML.

Date format note: the API emits startDate as "08-24-26Z" (MM-DD-YY with a
trailing Z that is not a timezone). Parse it explicitly rather than trusting
a generic ISO parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from xml.etree import ElementTree as ET

from canvas_calendar.models import Meeting


@dataclass
class Section:
    meetings: list[Meeting]
    start_date: date | None = None
    end_date: date | None = None


def _text(node, tag: str, default: str = "") -> str:
    found = node.find(tag)
    return (found.text or default).strip() if found is not None else default


def _parse_uiuc_date(raw: str) -> date | None:
    raw = raw.strip().rstrip("Z")
    if not raw:
        return None
    return datetime.strptime(raw, "%m-%d-%y").date()


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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_catalog_parser.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/canvas_calendar/catalog tests/test_catalog_parser.py tests/fixtures/mcb244_section.xml
git commit -m "feat: parse Course Explorer section XML"
```

---

### Task 5: Course Explorer client

**Files:**
- Create: `src/canvas_calendar/catalog/client.py`
- Test: `tests/test_catalog_client.py`

**Critical:** the API returns HTTP 403 to some default user agents. An explicit
`User-Agent` header is required, not optional. This was observed live on
2026-08-25 — `WebFetch` got 403 where `curl` succeeded.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_client.py
import httpx
import pytest

from canvas_calendar.catalog.client import TERM_SLUGS, CatalogClient
from canvas_calendar.models import CourseRef


def _client(handler):
    transport = httpx.MockTransport(handler)
    return CatalogClient(http=httpx.Client(transport=transport))


def test_sends_explicit_user_agent():
    """Guards a real 403: the API rejects some default agents."""
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="<course/>")

    _client(handler).fetch_course(CourseRef("MCB", "244", "120268"))
    assert "canvas-calendar" in seen["ua"]


def test_builds_correct_url_from_term_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text="<course/>")

    _client(handler).fetch_course(CourseRef("MCB", "244", "120268"))
    assert seen["url"].endswith("/schedule/2026/fall/MCB/244.xml")


def test_unknown_term_id_raises():
    with pytest.raises(KeyError):
        CatalogClient().url_for(CourseRef("MCB", "244", "999999"))


def test_known_term_maps_to_year_and_season():
    assert TERM_SLUGS["120268"] == (2026, "fall")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_catalog_client.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/catalog/client.py
"""HTTP client for the UIUC Course Explorer API."""

from __future__ import annotations

import httpx

from canvas_calendar.models import CourseRef

BASE = "https://courses.illinois.edu/cisapp/explorer/schedule"
USER_AGENT = "canvas-calendar/0.1 (personal course schedule sync)"

# Canvas course codes embed the Course Explorer term id. Extend as terms roll.
TERM_SLUGS: dict[str, tuple[int, str]] = {
    "120268": (2026, "fall"),
}


class CatalogClient:
    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=30)

    def url_for(self, ref: CourseRef) -> str:
        year, season = TERM_SLUGS[ref.term_id]
        return f"{BASE}/{year}/{season}/{ref.subject}/{ref.number}.xml"

    def fetch_course(self, ref: CourseRef) -> str:
        r = self._http.get(self.url_for(ref), headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.text

    def fetch_section(self, ref: CourseRef, section_id: str) -> str:
        year, season = TERM_SLUGS[ref.term_id]
        url = f"{BASE}/{year}/{season}/{ref.subject}/{ref.number}/{section_id}.xml"
        r = self._http.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_catalog_client.py -v`
Expected: PASS, 4 passed.

- [ ] **Step 5: Verify against the live API once**

Run:
```bash
uv run python -c "
from canvas_calendar.catalog.client import CatalogClient
from canvas_calendar.catalog.parser import parse_section
from canvas_calendar.models import CourseRef
c = CatalogClient(); ref = CourseRef('MCB','244','120268')
s = parse_section(c.fetch_section(ref, '56301'))
print(s.meetings[0], s.start_date, s.end_date)
"
```
Expected: prints the TR 02:00PM Foellinger meeting and the 2026-08-24 → 2026-12-09 range.

- [ ] **Step 6: Commit**

```bash
git add src/canvas_calendar/catalog/client.py tests/test_catalog_client.py
git commit -m "feat: Course Explorer client with required user agent"
```

---

## Chunk 3: Canvas and classification

### Task 6: Canvas client

**Files:**
- Create: `src/canvas_calendar/config.py`
- Create: `src/canvas_calendar/canvas/__init__.py`
- Create: `src/canvas_calendar/canvas/client.py`
- Test: `tests/test_canvas_client.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_canvas_client.py
import httpx
import pytest

from canvas_calendar.canvas.client import CanvasClient, TokenExpired


def _client(handler, token="tok"):
    return CanvasClient(
        base_url="https://canvas.illinois.edu/api/v1",
        token=token,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_sends_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    _client(handler).list_assignments(1)
    assert seen["auth"] == "Bearer tok"


def test_expired_token_raises_dedicated_error():
    """A 401 must be loud and specific -- never a silent empty result.
    This is the failure mode that silently broke the previous setup."""

    def handler(request):
        return httpx.Response(
            401, json={"errors": [{"message": "Expired access token"}]}
        )

    with pytest.raises(TokenExpired, match="Expired"):
        _client(handler).list_assignments(1)


def test_follows_link_header_pagination():
    pages = {
        1: httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": '<https://x/api/v1/c?page=2>; rel="next"'},
        ),
        2: httpx.Response(200, json=[{"id": 2}]),
    }
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return pages[calls["n"]]

    out = _client(handler).list_assignments(1)
    assert [a["id"] for a in out] == [1, 2]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_canvas_client.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the config module**

```python
# src/canvas_calendar/config.py
"""Configuration. The Canvas token is shared with the canvas-mcp install."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV = Path.home() / "code" / "canvas-mcp" / ".env"


def load_canvas_credentials(env_path: Path | None = None) -> tuple[str, str]:
    """Return (base_url, token). Environment variables win over the file."""
    values = dotenv_values(env_path or DEFAULT_ENV)
    token = os.environ.get("CANVAS_API_TOKEN") or values.get("CANVAS_API_TOKEN") or ""
    url = os.environ.get("CANVAS_API_URL") or values.get("CANVAS_API_URL") or ""
    if not token:
        raise RuntimeError("CANVAS_API_TOKEN not found -- see spec, milestone 7")
    url = url.rstrip("/")
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url, token
```

- [ ] **Step 4: Write the client**

```python
# src/canvas_calendar/canvas/client.py
"""Thin Canvas REST client.

Deliberately does not import the canvas_mcp package: that repo moved 432
commits in six months and this project must not break on an upstream rename.
"""

from __future__ import annotations

import re

import httpx


class TokenExpired(RuntimeError):
    """Raised on 401. Must never be swallowed into an empty result."""


_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class CanvasClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http or httpx.Client(timeout=30)

    def _get_all(self, path: str, **params) -> list[dict]:
        url = f"{self._base}{path}"
        params = {"per_page": 100, **params}
        out: list[dict] = []
        while url:
            r = self._http.get(url, headers=self._headers, params=params)
            if r.status_code == 401:
                raise TokenExpired(r.text)
            r.raise_for_status()
            out.extend(r.json())
            m = _NEXT.search(r.headers.get("Link", ""))
            url, params = (m.group(1) if m else None), {}
        return out

    def list_courses(self) -> list[dict]:
        return self._get_all("/courses", enrollment_state="active")

    def list_assignments(self, course_id: int) -> list[dict]:
        return self._get_all(f"/courses/{course_id}/assignments")

    def list_modules(self, course_id: int) -> list[dict]:
        return self._get_all(f"/courses/{course_id}/modules")

    def list_module_items(self, course_id: int, module_id: int) -> list[dict]:
        return self._get_all(f"/courses/{course_id}/modules/{module_id}/items")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_canvas_client.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/canvas_calendar/config.py src/canvas_calendar/canvas tests/test_canvas_client.py
git commit -m "feat: Canvas client with loud 401 handling and pagination"
```

---

### Task 7: Inclusion rules

The spec's most safety-critical rule. An earlier draft filtered on `points > 0`,
which would have silently dropped seven FSHN 120 items that read as required
coursework. Encode the corrected rule with those exact names as fixtures.

**Files:**
- Create: `src/canvas_calendar/rules.py`
- Test: `tests/test_rules.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rules.py
import pytest

from canvas_calendar.models import Assignment
from canvas_calendar.rules import Disposition, classify


def make(name, points=10.0, canvas_id=1):
    return Assignment(canvas_id=canvas_id, name=name, points=points,
                      due_at=None, course="X")


@pytest.mark.parametrize(
    "name",
    [
        'PILLAR A VIDEO QUIZ - extra credit points will update within 2 weeks',
        "PILLAR C - EXTRA CREDIT QUIZ - extra credit points will be awarded",
        "Extra Credit - Submit Here - Summary #1 (classic)",
    ],
)
def test_extra_credit_goes_to_digest(name):
    assert classify(make(name, points=0.0)) is Disposition.DIGEST


@pytest.mark.parametrize(
    "name",
    [
        "PILLAR A - REFLECTIVE ASSIGNMENT (discussion)",
        "PILLAR B - Data for Improvement (DI) quiz",
        "PILLAR C REFLECTIVE ASSIGNMENT (discussion) ",
    ],
)
def test_zero_point_non_extra_credit_is_calendared(name):
    """The seven FSHN items a points>0 filter would have silently dropped.
    Being wrong here loses coursework, so 0-point defaults to calendar."""
    assert classify(make(name, points=0.0)) is Disposition.CALENDAR


def test_excluded_ids_are_skipped():
    a = make("DISABILITY RESOURCES - DROP YOUR LOA HERE", points=0.0, canvas_id=1697990)
    assert classify(a, exclude={1697990}) is Disposition.SKIP


def test_graded_work_is_calendared():
    assert classify(make("Chapter 1; Sections 1.1, 1.4-1.7", 10.0)) is Disposition.CALENDAR


def test_mcb364_lab_items_are_calendared():
    """2-3 point undated lab work -- a points>0 filter never touched these,
    but confirm they survive the corrected rule too."""
    assert classify(make("Image submission -Wk1", 2.0)) is Disposition.CALENDAR
    assert classify(make("Pre-Lab Quiz -Week 1", 2.0)) is Disposition.CALENDAR


def test_extra_credit_match_is_case_and_space_insensitive():
    assert classify(make("EXTRA  CREDIT bonus", 5.0)) is Disposition.DIGEST
    assert classify(make("extracredit bonus", 5.0)) is Disposition.DIGEST
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rules.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/rules.py
"""Inclusion rules.

Point value is NOT a proxy for importance. Measured 2026-08-25: MCB 364's 22
undated lab items are worth 2-3 points each, while FSHN 120 carries 20 zero-point
items -- seven of which (PILLAR A-D reflective assignments, PILLAR B/C/D DI
quizzes) carry no extra-credit marker and read as required coursework.

Rule order matters. A 0-point assignment is calendared by default: being wrong
that way adds an event, being wrong the other way loses coursework.
"""

from __future__ import annotations

import re
from enum import Enum

from canvas_calendar.models import Assignment

_EXTRA_CREDIT = re.compile(r"extra\s*credit", re.I)


class Disposition(str, Enum):
    CALENDAR = "calendar"
    DIGEST = "digest"
    SKIP = "skip"


def classify(assignment: Assignment, exclude: set[int] | None = None) -> Disposition:
    if exclude and assignment.canvas_id in exclude:
        return Disposition.SKIP
    if _EXTRA_CREDIT.search(assignment.name):
        return Disposition.DIGEST
    return Disposition.CALENDAR
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rules.py -v`
Expected: PASS, 10 passed.

- [ ] **Step 5: Commit**

```bash
git add src/canvas_calendar/rules.py tests/test_rules.py
git commit -m "feat: inclusion rules keyed on extra-credit marker, not points"
```

---

### Task 8: Module date extraction

Replaces inference entirely. Courses publish real dates in module titles and
SubHeader text; this reads them.

**Files:**
- Create: `src/canvas_calendar/modules.py`
- Test: `tests/test_modules.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_modules.py
from datetime import date

import pytest

from canvas_calendar.modules import extract_dates, parse_subheader_date


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Week 1 - Intro to Cell culture and Aseptic Techniques     -   August 26th/28th",
         [date(2026, 8, 26), date(2026, 8, 28)]),
        ("Week 6 -  Cell Viabilty and Cell Death - September 30th/October 2nd",
         [date(2026, 9, 30), date(2026, 10, 2)]),
        ("Week 7 - Midterm -  October 7th/ 9th",
         [date(2026, 10, 7), date(2026, 10, 9)]),
    ],
)
def test_extracts_dates_from_mcb364_module_titles(title, expected):
    assert extract_dates(title, year=2026) == expected


def test_handles_merged_week_module():
    """Weeks 12 & 13 share one module. Ordinal or label arithmetic both break
    here; reading the title does not."""
    title = ("Weeks 12 &13 - Independent project  November 11th/13th "
             "& November 18th/20th")
    assert extract_dates(title, year=2026) == [
        date(2026, 11, 11), date(2026, 11, 13),
        date(2026, 11, 18), date(2026, 11, 20),
    ]


def test_cancelled_week_still_parses_its_date():
    assert extract_dates("Week 15 - December 9th -  NO CLASS", year=2026) == [
        date(2026, 12, 9)
    ]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("September 16: EXAM 1 (Lectures 1-7 of PARTS 1-2)", date(2026, 9, 16)),
        ("November 6: EXAM 3 (Lectures 14-21 of PARTS 4-5)", date(2026, 11, 6)),
        ("December 9: EXAM 4 (Lectures 22-28 of PART 6)", date(2026, 12, 9)),
    ],
)
def test_extracts_mcb320_exam_dates_from_subheaders(text, expected):
    assert parse_subheader_date(text, year=2026) == expected


def test_subheader_with_range_takes_first_date():
    assert parse_subheader_date("August 24 - 26: Cell Death", year=2026) == date(2026, 8, 24)


def test_subheader_with_two_explicit_dates():
    """'November 20, November 30: Stroke' -- two separate sessions."""
    assert extract_dates("November 20, November 30: Stroke", year=2026) == [
        date(2026, 11, 20), date(2026, 11, 30)
    ]


def test_returns_empty_when_no_date_present():
    assert extract_dates("Appendices - Contains important information", year=2026) == []
    assert parse_subheader_date("Resourses", year=2026) is None


def test_mcb436_lecture_module_slash_format():
    assert extract_dates("8/24 - Lecture 1: Brenda Wilson", year=2026) == [date(2026, 8, 24)]
    assert extract_dates("11/30 - Lecture 13: James Slauch", year=2026) == [date(2026, 11, 30)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_modules.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/modules.py
"""Extract dates from Canvas module titles and SubHeader text.

Courses publish real schedules here, not in due-date fields. Verified
2026-08-25 across MCB 364 (dated module titles), MCB 320 (dated SubHeaders),
and MCB 436 (slash-dated lecture modules).

This is extraction from authored text, not inference. Where a course omits a
date, that fact is reported rather than guessed around.
"""

from __future__ import annotations

import re
from datetime import date

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

# "August 26th", "October 2nd", "December 9"
_NAMED = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I
)
# A bare day following a named month within the same title: "August 26th/28th"
_BARE_DAY = re.compile(r"/\s*(\d{1,2})(?:st|nd|rd|th)?\b")
# "8/24 - Lecture 1"
_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})\b")


def extract_dates(text: str, year: int) -> list[date]:
    """All dates mentioned in a module title, in order of appearance."""
    out: list[date] = []

    for m in _SLASH.finditer(text):
        month, day = int(m.group(1)), int(m.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            out.append(date(year, month, day))
    if out:
        return out

    # Named months, plus bare days that follow one via a slash.
    for m in _NAMED.finditer(text):
        month = _MONTHS[m.group(1).lower()]
        out.append(date(year, month, int(m.group(2))))
        tail = text[m.end() : m.end() + 8]
        bare = _BARE_DAY.match(tail)
        if bare:
            out.append(date(year, month, int(bare.group(1))))
    return out


def parse_subheader_date(text: str, year: int) -> date | None:
    """First date in a SubHeader, or None. Ranges take the start date."""
    found = extract_dates(text, year)
    return found[0] if found else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_modules.py -v`
Expected: PASS, 12 passed.

Note: `test_handles_merged_week_module` exercises the bare-day-after-named-month
path twice in one title. If it fails, the `_BARE_DAY` lookahead window is the
thing to adjust — do not loosen it into a general digit match, which would pick
up lecture numbers.

- [ ] **Step 5: Commit**

```bash
git add src/canvas_calendar/modules.py tests/test_modules.py
git commit -m "feat: extract dates from module titles and SubHeaders"
```

---

### Task 9: Preview CLI

Ties the read path together into something runnable and inspectable.

**Files:**
- Create: `src/canvas_calendar/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py
from datetime import datetime, timezone

from canvas_calendar.cli import render_preview
from canvas_calendar.models import Assignment, Source


def _a(name, ts, points=10.0, cid=1):
    return Assignment(
        canvas_id=cid, name=name, points=points, course="MCB 244",
        due_at=datetime.fromisoformat(ts.replace("Z", "+00:00")),
    )


def test_renders_timed_and_all_day_separately():
    out = render_preview([
        _a("Chapter 1", "2026-08-25T19:00:00Z", cid=1),          # 14:00 local
        _a("Reflective", "2026-09-01T04:59:59Z", cid=2),          # 23:59 local Aug 31
    ])
    assert "2:00 PM" in out
    assert "all day" in out


def test_flags_extracted_dates_in_output():
    a = _a("Image submission -Wk1", "2026-08-28T04:59:00Z", points=2.0, cid=3)
    a.source = Source.EXTRACTED
    a.provenance = "module: Week 1 ... August 26th/28th"
    out = render_preview([a])
    assert "[extracted]" in out
    assert "Week 1" in out


def test_empty_input_does_not_crash():
    assert "no assignments" in render_preview([]).lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/cli.py
"""Read-path CLI. Prints; never writes to a calendar."""

from __future__ import annotations

import argparse

from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import is_end_of_day, to_local


def render_preview(assignments: list[Assignment]) -> str:
    if not assignments:
        return "No assignments found."
    lines = []
    for a in sorted(assignments, key=lambda x: (x.due_at is None, x.due_at)):
        if a.due_at is None:
            when = "unresolved"
        elif is_end_of_day(a.due_at):
            when = f"{to_local(a.due_at):%a %b %d} (all day)"
        else:
            when = f"{to_local(a.due_at):%a %b %d, %-I:%M %p}"
        tag = " [extracted]" if a.source is Source.EXTRACTED else ""
        lines.append(f"{when:<28} {a.course:<10} {a.name[:56]}{tag}")
        if a.provenance:
            lines.append(f"{'':<28} └─ {a.provenance}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="canvas-calendar")
    parser.add_argument("command", choices=["preview"])
    parser.parse_args()
    from canvas_calendar.pipeline import collect

    print(render_preview(collect()))
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS, 3 passed.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -v && uv run ruff check src tests`
Expected: all pass, ruff clean. Note `main()` is not runnable until Task 10
supplies `pipeline.collect`; only `render_preview` is exercised here.

- [ ] **Step 6: Commit**

```bash
git add src/canvas_calendar/cli.py tests/test_cli.py
git commit -m "feat: preview CLI rendering timed and all-day deadlines"
```

---

### Task 10: Pipeline wiring

Without this, `cli.main()` imports a module that does not exist and the
`preview` command cannot run. This task is what makes plan 1 deliver working
software rather than a library.

**Files:**
- Create: `src/canvas_calendar/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pipeline.py
from datetime import date

from canvas_calendar.models import Source
from canvas_calendar.pipeline import build_assignments, resolve_undated


def test_skips_non_term_courses():
    """Open/teacher courses (provost_unihigh_open_219093) have no term code
    and must be dropped, not crash the run."""
    courses = [
        {"id": 1, "course_code": "mcb_244_120268_262964", "name": "MCB 244"},
        {"id": 2, "course_code": "provost_unihigh_open_219093", "name": "Nonfiction"},
    ]
    assert [c["id"] for c in build_assignments.term_courses(courses)] == [1]


def test_dated_assignment_keeps_canvas_source():
    raw = [{"id": 9, "name": "Chapter 1", "points_possible": 10.0,
            "due_at": "2026-08-25T19:00:00Z"}]
    out = build_assignments(raw, course="MCB 244")
    assert out[0].source is Source.CANVAS
    assert out[0].due_at is not None


def test_undated_assignment_starts_unresolved():
    raw = [{"id": 9, "name": "Image submission -Wk1", "points_possible": 2.0,
            "due_at": None}]
    out = build_assignments(raw, course="MCB 364")
    assert out[0].source is Source.UNRESOLVED
    assert out[0].due_at is None


def test_resolve_undated_uses_containing_module_date():
    """The core extraction path: assignment inherits its module's stated date."""
    raw = [{"id": 9, "name": "Image submission -Wk1", "points_possible": 2.0,
            "due_at": None}]
    items = build_assignments(raw, course="MCB 364")
    modules = {
        9: "Week 1 - Intro to Cell culture and Aseptic Techniques - August 26th/28th"
    }
    resolved = resolve_undated(items, modules, year=2026)
    assert resolved[0].source is Source.EXTRACTED
    assert resolved[0].due_at.date() == date(2026, 8, 28)  # last stated date
    assert "Week 1" in resolved[0].provenance


def test_resolve_undated_leaves_undatable_items_unresolved():
    """MCB 436 polls: module gives no usable date, so they stay unresolved
    and surface in the digest rather than getting a guess."""
    raw = [{"id": 9, "name": "Class 16 - Poll", "points_possible": 5.0,
            "due_at": None}]
    items = build_assignments(raw, course="MCB 436")
    resolved = resolve_undated(items, {9: "Extra Credit Opportunities"}, year=2026)
    assert resolved[0].source is Source.UNRESOLVED
    assert resolved[0].due_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/canvas_calendar/pipeline.py
"""Wire the read path together: Canvas + Course Explorer -> Assignment list."""

from __future__ import annotations

from datetime import datetime, time

from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.config import load_canvas_credentials
from canvas_calendar.models import Assignment, CourseRef, Source
from canvas_calendar.modules import extract_dates
from canvas_calendar.rules import Disposition, classify
from canvas_calendar.timeutil import CHICAGO, parse_canvas_ts

# An extracted date has no clock time; treat it as end of day, which the
# renderer then classifies as all-day.
_EXTRACTED_TIME = time(23, 59)


def _term_courses(courses: list[dict]) -> list[dict]:
    out = []
    for c in courses:
        try:
            CourseRef.from_canvas_code(c.get("course_code", ""))
        except ValueError:
            continue
        out.append(c)
    return out


def build_assignments(raw: list[dict], course: str) -> list[Assignment]:
    out = []
    for a in raw:
        due = a.get("due_at")
        out.append(
            Assignment(
                canvas_id=a["id"],
                name=a.get("name", ""),
                points=a.get("points_possible") or 0.0,
                due_at=parse_canvas_ts(due) if due else None,
                course=course,
                source=Source.CANVAS if due else Source.UNRESOLVED,
            )
        )
    return out


build_assignments.term_courses = _term_courses  # exposed for testing


def resolve_undated(
    items: list[Assignment], module_titles: dict[int, str], year: int
) -> list[Assignment]:
    """Give undated assignments a date from their containing module's title.

    Uses the LAST date stated in the title: for a lab meeting "August 26th/28th"
    the work is due by the end of that week's sessions, not the first one.
    Anything with no parseable date stays UNRESOLVED and goes to the digest.
    """
    for a in items:
        if a.source is not Source.UNRESOLVED:
            continue
        title = module_titles.get(a.canvas_id, "")
        found = extract_dates(title, year=year)
        if not found:
            continue
        a.due_at = datetime.combine(found[-1], _EXTRACTED_TIME, tzinfo=CHICAGO)
        a.source = Source.EXTRACTED
        a.provenance = f"module: {title.strip()}"
    return items


def collect() -> list[Assignment]:
    """Full read path against live Canvas. Used by `canvas-calendar preview`."""
    base_url, token = load_canvas_credentials()
    client = CanvasClient(base_url, token)
    results: list[Assignment] = []
    for course in _term_courses(client.list_courses()):
        cid, label = course["id"], course.get("name", "")
        items = build_assignments(client.list_assignments(cid), course=label)

        if any(a.source is Source.UNRESOLVED for a in items):
            titles: dict[int, str] = {}
            for module in client.list_modules(cid):
                for entry in client.list_module_items(cid, module["id"]):
                    if entry.get("type") == "Assignment" and entry.get("content_id"):
                        titles[entry["content_id"]] = module.get("name", "")
            items = resolve_undated(items, titles, year=2026)

        results.extend(
            a for a in items if classify(a) is not Disposition.SKIP
        )
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: PASS, 5 passed.

- [ ] **Step 5: Run the real command end to end**

Run: `uv run canvas-calendar preview`
Expected: a sorted schedule across all six courses. MCB 364's `Wk` items appear
with `[extracted]` and a `module:` provenance line. MCB 436's polls appear as
`unresolved`.

Note: this hits the live Canvas API and needs a valid token. A `TokenExpired`
here is the expected loud failure, not a bug.

- [ ] **Step 6: Commit**

```bash
git add src/canvas_calendar/pipeline.py tests/test_pipeline.py
git commit -m "feat: wire read path into a runnable preview command"
```

---

## Verification gate

Before starting plan 2, confirm the read path against live data:

- [ ] All tests pass: `uv run pytest -v`
- [ ] Ruff clean: `uv run ruff check src tests`
- [ ] MCB 244 meetings match Course Explorer (TR 2:00PM, Foellinger AUD)
- [ ] MCB 364 `Wk1`–`Wk11` all receive dates from module titles
- [ ] MCB 320's four exam dates extract correctly (Sep 16, Oct 12, Nov 6, Dec 9)
- [ ] The seven zero-point FSHN items classify as CALENDAR, not DIGEST
- [ ] A November deadline renders at 11:59PM local, not 10:59PM

That last check is the DST regression. If it fails, nothing downstream is
trustworthy.

## Not in this plan

- SQLite state and the diff engine (plan 2)
- Calendar adapters and the Outlook auth spike (plan 2)
- LaunchAgent, digest, notifications (plan 3)
- Monthly drift check (plan 3)
- MCB 436 poll mapping — deliberately unresolved; those 14 items stay
  `Source.UNRESOLVED` and surface in the digest

---

## Execution record (2026-08-25)

Completed on branch `feat/read-path`. 94 tests, ruff clean, all seven gate
checks passing against live data.

### Task 11 (added during execution): SubHeader assessment extraction

The verification gate caught a gap this plan shipped with. `parse_subheader_date`
was implemented and tested in Task 8, but `collect()` in Task 10 only read
`/assignments` and only walked modules when an undated assignment existed.
MCB 320 has zero assignments, so it walked nothing and contributed nothing --
the precise silent-omission failure the project exists to prevent. Tested
capability is not the same as wired behaviour, and only the live gate exposed
the difference.

Fixes applied:

- `subheader_events()` builds assessment events from dated SubHeader text,
  keyword-matched so lecture topics are not mistaken for deadlines.
- `collect()` walks modules unconditionally, serving both extraction paths in
  one pass.
- **Latent UID collision fixed.** The spec asserted every managed item is a
  Canvas assignment with a stable id. SubHeader events break that: module items
  occupy a separate ID space, so module item `5440597` and assignment `5440597`
  would have produced identical UIDs and clobbered each other on the calendar.
  `Assignment` now carries a `namespace`; SubHeader events use `mi-`.

This last point matters for plan 2 — the never-delete-foreign-events check and
the diff engine both key on UID.

### Known unresolved (correct behaviour, not defects)

| Course | Count | Why |
|---|---:|---|
| MCB 436 | 18 | 14 polls whose numbering does not match its lectures, + 4 EC summaries |
| MCB 364 | 12 | `Wk1`-`Wk11` and `Assignment 1` are not inside the dated week modules |
| FSHN 120 | 2 | Alternate dropbox and the error-report EC item |
| MCB 354 | 1 | Roll Call Attendance -- not a deadline |

All are surfaced in output rather than dropped. The MCB 364 case was not
anticipated by the spec: those items live outside the week modules, so module
extraction cannot reach them. Worth a look before plan 3's drift check.
