# Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a non-technical UIUC student install and run canvas-calendar in a few minutes, guided by Claude Code, Gemini CLI, or Codex CLI.

**Architecture:** Two new commands — `doctor`, which diagnoses everything an agent would otherwise have to guess at, and `setup`, an ordered wizard. `AGENTS.md` becomes the single canonical instruction file with `CLAUDE.md` and `GEMINI.md` as symlinks, so all three agents read the same text. LaunchAgent plists are rendered per-user against the installed binary.

**Tech Stack:** Python 3.13, launchd, uv

**Spec:** `docs/superpowers/specs/2026-08-31-portable-multi-backend-design.md` (Section 4)

## Global Constraints

- Python `>=3.13`; run everything through `uv run`.
- Verification before any commit: `uv run pytest -q && uv run ruff check src tests`.
- **Depends on `2026-08-31-backend-seam.md` being complete.** `setup` writes `calendar_backend`, which only the factory from that plan consumes.
- **The Calendar permission grant must happen interactively, during `setup`, and before the LaunchAgent is installed.** The spike measured a first-run agent blocking ~37s on a prompt; a *scheduled* run would block on a dialog nobody sees, and stall silently. This ordering is not cosmetic.
- `setup` must never overwrite an existing config key without saying so. The author's working install is the case that must not break.
- Nothing in this plan may run `sync --live` on its own initiative.

---

### Task 1: `doctor` — diagnose before guessing

Written first because it is what an agent runs when a friend says "it's broken", and because `setup` reuses its checks as verification.

**Files:**
- Create: `src/canvas_calendar/doctor.py`
- Create: `tests/test_doctor.py`
- Modify: `src/canvas_calendar/cli.py:114` (command choices)

**Interfaces:**
- Consumes: `load_sync_options`, `load_term`, `load_canvas_credentials`, `make_adapter`, `CanvasClient`
- Produces: `run_checks() -> list[Check]` where `Check` is a dataclass of `name: str`, `ok: bool`, `detail: str`, `fix: str`; `render(checks) -> str`; `main() -> int` returning 0 when every check passes, 1 otherwise

- [ ] **Step 1: Write the failing tests**

Create `tests/test_doctor.py`:

```python
from datetime import date

from canvas_calendar.doctor import Check, render


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


def test_term_check_fails_when_today_is_out_of_range():
    from canvas_calendar.doctor import check_term
    from canvas_calendar.terms import Term

    t = Term(year=2026, season="fall", start=date(2026, 8, 24),
             end=date(2026, 12, 9), holidays=())
    assert check_term(t, today=date(2026, 9, 15)).ok is True
    stale = check_term(t, today=date(2027, 2, 1))
    assert stale.ok is False
    assert "outside" in stale.detail
```

- [ ] **Step 2: Run and confirm they fail**

```bash
uv run pytest tests/test_doctor.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_calendar.doctor'`.

- [ ] **Step 3: Implement**

Create `src/canvas_calendar/doctor.py`:

```python
"""Diagnose an install without guessing.

Every failure mode this project has is silent: a token that expired, a term
that ended, a calendar that stopped being writable, a LaunchAgent that never
loaded. Each one presents as "my calendar stopped updating" days later. This
command turns all of them into one line of text with the command that fixes
it, so an agent helping a non-technical user has something to read.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"{'✓' if c.ok else '✗'} {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"    fix: {c.fix}")
    failed = [c for c in checks if not c.ok]
    lines.append("")
    lines.append("All checks passed." if not failed else f"{len(failed)} problem(s) found.")
    return "\n".join(lines)


def check_term(term, today: date | None = None) -> Check:
    today = today or date.today()
    if term.covers(today):
        left = (term.end - today).days
        return Check("Term", True, f"{term.year} {term.season}, {left} days left", "")
    return Check(
        "Term", False,
        f"today is outside {term.year} {term.season} ({term.start}..{term.end})",
        "canvas-calendar setup   # pick the current term",
    )


def check_canvas() -> Check:
    from canvas_calendar.canvas.client import CanvasClient, TokenExpired
    from canvas_calendar.config import load_canvas_credentials

    try:
        base, tok = load_canvas_credentials()
        courses = CanvasClient(base, tok).list_courses()
    except TokenExpired:
        return Check("Canvas token", False, "expired or rejected (401)",
                     "canvas-calendar token   # prints the renewal steps")
    except Exception as exc:  # noqa: BLE001 -- report, never crash the doctor
        return Check("Canvas token", False, f"{type(exc).__name__}: {exc}",
                     "canvas-calendar setup")
    return Check("Canvas token", True, f"valid, {len(courses)} active courses", "")


def check_calendar() -> Check:
    from canvas_calendar.calendars.factory import UnknownBackend, make_adapter
    from canvas_calendar.config import load_sync_options

    opts = load_sync_options()
    try:
        _, cal_id = make_adapter(opts)
    except UnknownBackend as exc:
        return Check("Calendar", False, str(exc), "canvas-calendar setup")
    except Exception as exc:  # noqa: BLE001
        return Check("Calendar", False, f"{type(exc).__name__}: {exc}",
                     "canvas-calendar setup")
    return Check(
        "Calendar", True,
        f"{opts['calendar_backend']} -> {opts['assignments_calendar']!r} ({cal_id[:12]}…)",
        "",
    )


def check_agents() -> Check:
    """Is the scheduled job actually loaded, and did it last exit cleanly?"""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=10).stdout
    except Exception as exc:  # noqa: BLE001
        return Check("Scheduled run", False, f"launchctl unavailable: {exc}", "")
    rows = [ln for ln in out.splitlines() if "canvas" in ln]
    if not rows:
        return Check("Scheduled run", False, "no canvas-calendar agent loaded",
                     "canvas-calendar setup   # installs the LaunchAgents")
    bad = [r for r in rows if r.split("\t")[1] not in ("0", "-")]
    if bad:
        return Check("Scheduled run", False, f"last exit non-zero: {bad[0].strip()}",
                     "canvas-calendar doctor after checking the log")
    return Check("Scheduled run", True, f"{len(rows)} agent(s) loaded, last exit 0", "")


def run_checks() -> list[Check]:
    from canvas_calendar.config import load_term

    return [check_canvas(), check_term(load_term()), check_calendar(), check_agents()]


def main() -> int:
    checks = run_checks()
    print(render(checks))
    return 0 if all(c.ok for c in checks) else 1
```

- [ ] **Step 4: Wire the command**

In `src/canvas_calendar/cli.py`, add `"doctor"` and `"setup"` to the
`choices` list on line 114, and add a branch alongside the other early
dispatches in `main()`:

```python
    if args.command == "doctor":
        from canvas_calendar.doctor import main as doctor_main

        return doctor_main()
```

- [ ] **Step 5: Run the tests, then run it for real**

```bash
uv run pytest tests/test_doctor.py -v
uv run canvas-calendar doctor
```

Expected: 3 tests pass. The live run shows four ✓ lines — Canvas token valid,
term in range, outlook backend resolving `UIUC Assignments`, and two agents
loaded with last exit 0.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/doctor.py tests/test_doctor.py src/canvas_calendar/cli.py
git commit -m "feat: canvas-calendar doctor

Every failure mode here presents as 'my calendar stopped updating' days
later. This turns each into one line plus the command that fixes it."
```

---

### Task 2: `setup` — the ordered wizard

**Files:**
- Create: `src/canvas_calendar/setup_wizard.py`
- Create: `tests/test_setup_wizard.py`
- Create: `src/canvas_calendar/data/terms.json`
- Modify: `src/canvas_calendar/cli.py`
- Modify: `pyproject.toml` (package data)

**Interfaces:**
- Consumes: `Check`/`run_checks` from Task 1, `EventKitAdapter`, `device_login`
- Produces: `write_config(updates: dict, *, path) -> list[str]` returning a description of each change; `choose_term(today) -> dict`; `main() -> int`

- [ ] **Step 1: Ship the term table**

Create `src/canvas_calendar/data/terms.json`:

```json
[
  {"year": 2026, "season": "fall",   "start": "2026-08-24", "end": "2026-12-09",
   "holidays": ["2026-09-07", "2026-11-21", "2026-11-22", "2026-11-23",
                "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27",
                "2026-11-28", "2026-11-29"]},
  {"year": 2027, "season": "spring", "start": "2027-01-19", "end": "2027-05-05",
   "holidays": ["2027-03-13", "2027-03-14", "2027-03-15", "2027-03-16",
                "2027-03-17", "2027-03-18", "2027-03-19", "2027-03-20",
                "2027-03-21"]}
]
```

Spring 2027 verified against the UIUC registrar on 2026-08-31: instruction
runs Jan 19 – May 5, spring break Mar 13–21 (classes resume Mar 22). **MLK Day
(Mon Jan 18 2027) falls before the first day of instruction**, so it is not an
in-term non-instruction day and is deliberately absent. Weekend dates are kept
in the list for consistency with Fall; `excluded_dates` filters by weekday, so
they are inert.

The registrar marks these "tentative until closer to the beginning of the
semester". Re-check before a spring install.

In `pyproject.toml`, ensure the data file ships:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/canvas_calendar"]

[tool.hatch.build.targets.wheel.force-include]
"src/canvas_calendar/data" = "canvas_calendar/data"
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_setup_wizard.py`:

```python
import json
from datetime import date

from canvas_calendar.setup_wizard import choose_term, write_config


def test_choose_term_picks_the_one_containing_today():
    t = choose_term(today=date(2026, 10, 1))
    assert (t["year"], t["season"]) == (2026, "fall")


def test_choose_term_picks_the_next_one_when_between_terms():
    t = choose_term(today=date(2026, 12, 20))
    assert (t["year"], t["season"]) == (2027, "spring")


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


def test_write_config_creates_the_file_when_absent(tmp_path):
    p = tmp_path / "sub" / "config.json"
    write_config({"calendar_backend": "eventkit"}, path=p)
    assert json.loads(p.read_text())["calendar_backend"] == "eventkit"
```

- [ ] **Step 3: Run and confirm they fail**

```bash
uv run pytest tests/test_setup_wizard.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_calendar.setup_wizard'`.

- [ ] **Step 4: Implement the testable core**

Create `src/canvas_calendar/setup_wizard.py`:

```python
"""Interactive first-run setup, also drivable by a coding agent.

The step order matters and is not cosmetic. Calendar permission is requested
while the user is present, BEFORE the LaunchAgents are installed: a background
agent has its own TCC identity, and its first run blocks on a permission
prompt. Scheduled at 07:15, that prompt appears to nobody and the job stalls
silently -- the exact failure this project exists to prevent.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from canvas_calendar.config import GRAPH_CONFIG


def _terms() -> list[dict]:
    p = Path(__file__).parent / "data" / "terms.json"
    return json.loads(p.read_text())


def choose_term(today: date | None = None) -> dict:
    """The term containing today, else the next one to start."""
    today = today or date.today()
    terms = sorted(_terms(), key=lambda t: t["start"])
    for t in terms:
        if date.fromisoformat(t["start"]) <= today <= date.fromisoformat(t["end"]):
            return t
    for t in terms:
        if date.fromisoformat(t["start"]) > today:
            return t
    return terms[-1]


def write_config(updates: dict, *, path: Path | None = None) -> list[str]:
    """Merge `updates` into config.json, describing every change.

    Returns one human-readable line per change. An overwrite is reported with
    both values: setup must never quietly replace a working setting.
    """
    p = Path(path or GRAPH_CONFIG)
    existing = json.loads(p.read_text()) if p.exists() else {}
    changes = []
    for k, v in updates.items():
        if k in existing and existing[k] != v:
            changes.append(f"{k}: {existing[k]!r} -> {v!r}  (overwritten)")
        elif k not in existing:
            changes.append(f"{k}: {v!r}  (added)")
        existing[k] = v
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(existing, indent=2) + "\n")
    return changes
```

- [ ] **Step 5: Run the tests and confirm they pass**

```bash
uv run pytest tests/test_setup_wizard.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Add the interactive flow**

Append to `src/canvas_calendar/setup_wizard.py`:

```python
def _ask(prompt: str, default: str = "") -> str:
    raw = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return raw or default


def main() -> int:
    print("canvas-calendar setup\n")

    # 1. Backend.
    print("Which calendar do you use?")
    print("  1) iCloud, Google, or Outlook already set up in the macOS Calendar app")
    print("  2) Outlook via Microsoft sign-in (no Calendar app needed)")
    backend = "eventkit" if _ask("Choice", "1") == "1" else "outlook"

    # 2. Permission FIRST, while the user is here to answer the prompt.
    if backend == "eventkit":
        from canvas_calendar.calendars.eventkit import (
            CalendarAccessDenied,
            EventKitAdapter,
        )

        print("\nmacOS will ask permission to use your calendars. Please allow it.")
        try:
            adapter = EventKitAdapter()
        except CalendarAccessDenied as exc:
            print(f"\n{exc}")
            return 1
        writable = [
            c.title()
            for c in adapter._store.calendarsForEntityType_(
                adapter._ek.EKEntityTypeEvent
            )
            or []
            if c.allowsContentModifications()
        ]
        if not writable:
            print(
                "\nNo writable calendars found. Add your calendar account in\n"
                "System Settings > Internet Accounts, then run setup again."
            )
            return 1
        print("\nWritable calendars: " + ", ".join(writable))
        cal_name = _ask("Calendar to use (created if absent)", "UIUC Assignments")
    else:
        from canvas_calendar.calendars.device_login import device_login

        if device_login() != 0:
            return 1
        cal_name = _ask("Calendar to use", "UIUC Assignments")

    # 3. Canvas token.
    print("\nCanvas token: canvas.illinois.edu > Account > Settings > + New Access Token")
    token = _ask("Paste it here")
    if token:
        creds = Path.home() / ".config" / "canvas-calendar" / "credentials.json"
        creds.parent.mkdir(parents=True, exist_ok=True)
        import os

        fd = os.open(creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(
                {"CANVAS_API_TOKEN": token,
                 "CANVAS_API_URL": "https://canvas.illinois.edu/api/v1"}, fh)

    # 4. Debrief. Requires Graph Mail.Send, so it is offered only on outlook.
    debrief = {}
    if backend == "outlook":
        to = _ask("Morning debrief email address (blank to skip)", "")
        debrief = {"debrief_enabled": bool(to), "debrief_to": to} if to else {}
    else:
        print("\nMorning debrief needs the Outlook backend; skipping. "
              "Use `canvas-calendar digest` instead.")

    # 5. Term.
    term = choose_term()
    print(f"\nTerm: {term['year']} {term['season']} "
          f"({term['start']} to {term['end']})")

    for line in write_config(
        {"calendar_backend": backend, "assignments_calendar": cal_name,
         "term": term, **debrief}
    ):
        print(f"  config {line}")

    # 6. Verify before offering to schedule anything.
    from canvas_calendar.doctor import render, run_checks

    print()
    checks = run_checks()
    print(render(checks))
    if not all(c.ok for c in checks if c.name != "Scheduled run"):
        print("\nFix the above, then run: canvas-calendar setup")
        return 1

    print("\nNext: canvas-calendar sync        (dry run — shows what would happen)")
    print("      canvas-calendar sync --live  (writes it)")
    print("      canvas-calendar install-agents  (run it twice a day)")
    return 0
```

Add the `setup` dispatch in `cli.py` next to `doctor`.

`load_canvas_credentials` must also read `credentials.json`; if the backend
seam plan has not already added that, extend its resolution order to
`CANVAS_API_TOKEN` env var, then `credentials.json`, then the legacy
`~/code/canvas-mcp/.env` path.

- [ ] **Step 7: Verify setup cannot damage the author's config**

```bash
cp ~/.config/canvas-calendar/config.json /tmp/cfg.before
uv run pytest tests/test_setup_wizard.py -v
diff /tmp/cfg.before ~/.config/canvas-calendar/config.json && echo "config untouched by tests"
```

Expected: no diff. The tests write only to `tmp_path`.

- [ ] **Step 8: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/setup_wizard.py tests/test_setup_wizard.py \
        src/canvas_calendar/data/terms.json src/canvas_calendar/cli.py pyproject.toml
git commit -m "feat: canvas-calendar setup wizard

Requests Calendar permission while the user is present, before any
LaunchAgent exists -- a background agent's first run blocks on a prompt
nobody would see."
```

---

### Task 3: `install-agents` — per-user LaunchAgents

`deploy/*.plist.template` hardcode `com.aryan.*` and an absolute home
directory.

**Files:**
- Modify: `deploy/com.aryan.canvas-calendar.plist.template` → `deploy/canvas-calendar.plist.template`
- Modify: `deploy/com.aryan.canvas-debrief.plist.template` → `deploy/canvas-debrief.plist.template`
- Create: `src/canvas_calendar/agents.py`
- Create: `tests/test_agents.py`
- Modify: `src/canvas_calendar/cli.py`

**Interfaces:**
- Consumes: nothing
- Produces: `render_plist(template: str, *, binary: str, label: str) -> str`; `install() -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agents.py`:

```python
from canvas_calendar.agents import LABEL_SYNC, render_plist

TEMPLATE = """<plist><dict>
<key>Label</key><string>{{LABEL}}</string>
<key>ProgramArguments</key><array><string>{{BINARY}}</string><string>daily</string></array>
</dict></plist>"""


def test_render_substitutes_binary_and_label():
    out = render_plist(TEMPLATE, binary="/opt/bin/cc", label=LABEL_SYNC)
    assert "/opt/bin/cc" in out
    assert LABEL_SYNC in out
    assert "{{" not in out, "an unsubstituted placeholder survived"


def test_label_is_not_user_specific():
    assert "aryan" not in LABEL_SYNC.lower()
```

- [ ] **Step 2: Run and confirm it fails**

```bash
uv run pytest tests/test_agents.py -v
```

Expected: `ModuleNotFoundError: No module named 'canvas_calendar.agents'`.

- [ ] **Step 3: Implement**

Create `src/canvas_calendar/agents.py`:

```python
"""Render and install the LaunchAgents for the current user.

The binary path is resolved at install time and must point at an installed
copy, never at a path inside a git checkout: the branch that happens to be
checked out would otherwise decide what runs at 07:15.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

LABEL_SYNC = "io.github.canvas-calendar.sync"
LABEL_DEBRIEF = "io.github.canvas-calendar.debrief"
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"


def render_plist(template: str, *, binary: str, label: str) -> str:
    return template.replace("{{BINARY}}", binary).replace("{{LABEL}}", label)


def resolve_binary() -> str:
    found = shutil.which("canvas-calendar") or sys.argv[0]
    p = Path(found).resolve()
    if ".venv" in p.parts or "/src/" in str(p):
        raise RuntimeError(
            f"{p} looks like a development checkout. Install the tool first:\n"
            f"  uv tool install git+https://github.com/Arluigi/canvas-calendar"
        )
    return str(p)


def install() -> int:
    binary = resolve_binary()
    root = Path(__file__).resolve().parent.parent.parent / "deploy"
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    for label, name in ((LABEL_SYNC, "canvas-calendar"),
                        (LABEL_DEBRIEF, "canvas-debrief")):
        tmpl = (root / f"{name}.plist.template").read_text()
        target = AGENT_DIR / f"{label}.plist"
        target.write_text(render_plist(tmpl, binary=binary, label=label))
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{label}"],
                       capture_output=True)
        r = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{_uid()}", str(target)],
            capture_output=True, text=True)
        print(f"{'installed' if r.returncode == 0 else 'FAILED'} {label}"
              + ("" if r.returncode == 0 else f": {r.stderr.strip()}"))
    return 0


def _uid() -> int:
    import os

    return os.getuid()
```

Update both templates to use `{{BINARY}}` and `{{LABEL}}` placeholders and
rename them, dropping the `com.aryan.` prefix from the filenames.

- [ ] **Step 4: Run tests, wire the command, verify without installing**

Add `"install-agents"` to the `choices` list and a dispatch branch.

```bash
uv run pytest tests/test_agents.py -v
uv run python -c "
from canvas_calendar.agents import resolve_binary
try: print('would install against:', resolve_binary())
except RuntimeError as e: print('correctly refused:', e)
"
```

Expected: 2 tests pass. The second command **refuses**, because `uv run`
resolves to the repo `.venv` — which is exactly the guard working. Running
`~/.local/bin/canvas-calendar` instead should print a real path.

- [ ] **Step 5: Do not re-install the author's agents**

The author's agents are already installed under the `com.aryan.*` labels and
working. Installing the new labels would double every scheduled run. Leave
them; the rename applies to new installs only. Note this in `AGENTS.md` in
Task 4.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add src/canvas_calendar/agents.py tests/test_agents.py deploy/ src/canvas_calendar/cli.py
git commit -m "feat: per-user LaunchAgent install with a neutral label

resolve_binary refuses a path inside a checkout: the branch checked out
would otherwise decide what runs at 07:15."
```

---

### Task 4: One instruction file, three agents

**Files:**
- Create: `AGENTS.md` (from the current `CLAUDE.md` plus an install section)
- Replace: `CLAUDE.md` with a symlink to `AGENTS.md`
- Create: `GEMINI.md` as a symlink to `AGENTS.md`
- Create: `install.sh`

**Interfaces:**
- Consumes: everything above
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Move the content**

```bash
git mv CLAUDE.md AGENTS.md
```

- [ ] **Step 2: Add the install section**

Append to `AGENTS.md`:

```markdown
## Installing for a new user

Run these in order. Steps 3 and 4 must not be reordered — the Calendar
permission prompt has to be answered by a human who is present, and a
LaunchAgent installed before that grant will stall on a prompt nobody sees.

1. `./install.sh` — installs `uv` if missing, then the tool to `~/.local/bin`.
2. `canvas-calendar setup` — choose backend, grant Calendar access, paste a
   Canvas token, pick the term.
3. `canvas-calendar sync` — dry run. Read the plan before writing anything.
4. `canvas-calendar sync --live` — writes the events.
5. `canvas-calendar install-agents` — schedules it twice daily.
6. `canvas-calendar doctor` — confirms all of the above.

If anything looks wrong later, `canvas-calendar doctor` prints each problem
with the command that fixes it. Start there.

**Requirements:** macOS, a UIUC Canvas account, and — for the `eventkit`
backend — the calendar account already added in System Settings > Internet
Accounts. The tool cannot see a Google account that only exists in a browser.

**Known gap:** Google's CalDAV was never tested for calendar creation. If
`setup` cannot create the calendar on a Google account, create it at
calendar.google.com first and re-run.

**Author's machine note:** the original install uses the legacy
`com.aryan.*` LaunchAgent labels. `install-agents` writes the newer
`io.github.canvas-calendar.*` labels; running it there would double every
scheduled run. Leave the existing agents in place.
```

- [ ] **Step 3: Symlink the other two**

```bash
ln -s AGENTS.md CLAUDE.md
ln -s AGENTS.md GEMINI.md
git add AGENTS.md CLAUDE.md GEMINI.md
git config core.symlinks true
```

Verify git stored symlinks rather than copies:

```bash
git ls-files -s CLAUDE.md GEMINI.md
```

Expected: mode `120000` on both. If it shows `100644`, git stored a copy —
remove and re-add with `git update-index --add --cacheinfo 120000`.

- [ ] **Step 4: Write the installer**

Create `install.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "canvas-calendar supports macOS only." >&2; exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv tool install --force git+https://github.com/Arluigi/canvas-calendar

echo
echo "Installed: $(command -v canvas-calendar || echo "$HOME/.local/bin/canvas-calendar")"
echo "Next: canvas-calendar setup"
```

```bash
chmod +x install.sh
bash -n install.sh && echo "syntax OK"
```

- [ ] **Step 5: Confirm the symlinks resolve and content survived**

```bash
head -3 CLAUDE.md && head -3 GEMINI.md
grep -c "Gotchas that cost hours" AGENTS.md
```

Expected: both symlinks print the `AGENTS.md` heading, and the gotchas
section is present exactly once — the operations manual must not have been
lost in the move.

- [ ] **Step 6: Full suite, lint, commit**

```bash
uv run pytest -q && uv run ruff check src tests
git add install.sh AGENTS.md CLAUDE.md GEMINI.md
git commit -m "docs: AGENTS.md as the single instruction file, plus install.sh

CLAUDE.md and GEMINI.md become symlinks so Claude Code, Codex and Gemini
CLI all read the same text."
```

---

## Notes for the executor

- **Never run `canvas-calendar setup` non-interactively on the author's machine.** It would prompt for a Canvas token and could overwrite `calendar_backend`. `write_config` reports overwrites, but the safe test is the unit tests, which write only to `tmp_path`.
- **Do not run `install-agents` on the author's machine.** Their agents already exist under `com.aryan.*`; adding the new labels would run every sync twice.
- Spring 2027 dates in `terms.json` were verified against the registrar on 2026-08-31, but are marked "tentative" by the university. Re-check before a spring install.
- `doctor` must never raise. Every check catches broadly and reports; a crashing diagnostic is worse than none.
