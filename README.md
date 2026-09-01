# canvas-calendar

Puts your UIUC class meetings and Canvas assignment deadlines into one
calendar, and emails you a morning debrief. Runs unattended on your Mac.

It reads Canvas *and* the UIUC Course Explorer, so you get recurring lecture
blocks with real rooms alongside every deadline — including the ones Canvas
doesn't put a due date on.

## Install

```bash
./install.sh                    # installs uv if needed, then the tool
canvas-calendar setup           # backend, calendar permission, Canvas token, term
canvas-calendar sync            # dry run — read this before writing anything
canvas-calendar sync --live     # writes the events
canvas-calendar install-agents  # runs it twice a day from then on
canvas-calendar doctor          # confirms all of the above
```

Any coding agent — Claude Code, Codex CLI, Gemini CLI — can walk you through
this; they all read `AGENTS.md`, which has the same steps written for them.

**Requirements:** macOS, a UIUC Canvas account, and your calendar account
already added in System Settings → Internet Accounts. A Google account that
only exists in a browser tab is invisible to the tool.

## Which calendar?

Pick `eventkit` at setup and it writes through the macOS Calendar app, which
already holds your **iCloud**, **Google**, or **Outlook/Exchange** account. One
code path covers all three, with no OAuth of its own — which is the point:
distributing a Google Calendar integration would otherwise mean an unverified
OAuth app whose refresh tokens expire weekly.

The `outlook` backend talks to Microsoft Graph directly, and is what the
morning debrief email needs.

> **Google won't let anything create a calendar over CalDAV** (`EKErrorDomain
> Code=17`; iCloud and Exchange are fine). Make one called `UIUC Assignments`
> at calendar.google.com first, then run setup. Writing *events* works
> normally on all three.

## Commands

```bash
canvas-calendar preview            # what it found, nothing written
canvas-calendar sync               # dry run; --live to write, --force to rewrite
canvas-calendar sync --course "MCB 244"   # filtered run (never prunes)
canvas-calendar meetings --live    # recurring class meetings
canvas-calendar debrief            # preview the email; --live sends
canvas-calendar daily --live       # what the scheduled job runs
canvas-calendar digest             # last run's report
canvas-calendar doctor             # diagnose an install
canvas-calendar token              # Canvas token expiry + renewal steps
```

Something broken? Run `doctor` first. It checks your token and its expiry, the
term, the calendar, and the scheduled job, and prints the command that fixes
whatever failed.

## What it handles that a naive importer doesn't

**Dates hiding outside due-date fields.** MCB 320 exposes zero assignments but
publishes its whole exam schedule as SubHeader text inside modules. Those get
parsed and calendared.

**Zero-point items.** FSHN 120 has twenty of them and seven are required
coursework with no "extra credit" marker, so filtering on points would have
silently dropped real work.

**Completed work disappearing on its own.** Once Canvas reports something
submitted, graded or excused, the event is removed and the digest names what
it removed — a bare `graded` with no score and no timestamp is a gradebook
placeholder, not evidence, and is deliberately left alone.

**Daylight saving.** Timed events carry local wall time plus a zone, so nothing
drifts across the November change mid-semester.

**Canvas being wrong.** `~/.config/canvas-calendar/overrides.json` holds
corrections and manual additions, and every one is reprinted on every run.

## Design principle

Every failure mode here is silent omission — work that exists but never
surfaces. Extra events are cheap; a missing one is not. So zero-point items
are calendared by default, undatable items are reprinted in every digest,
filtered email is counted rather than dropped, and nothing is ever deleted
from a calendar without a UID this tool wrote.

When extending it, ask what a change could make *invisible*, not just what it
adds.

## Development

```bash
uv run pytest -q && uv run ruff check src tests   # before any commit
uv run pytest -m live                             # touches a real calendar
```

Design docs and implementation plans live in `docs/superpowers/`. They record
the audit each decision came from — read them before changing behaviour.
`AGENTS.md` is the operations manual (`CLAUDE.md` and `GEMINI.md` symlink to
it).

## Caveats

- **macOS only.** EventKit is what makes one adapter cover three backends.
- **UIUC only.** Course Explorer has no equivalent elsewhere; Canvas itself
  would port, class meetings would not.
- **Canvas tokens expire roughly monthly.** Illinois caps the lifetime and
  blocks token creation via API, so renewal is manual — `canvas-calendar
  token` prints the steps, and `doctor` warns before it bites.
- **The debrief email is Outlook-only**, and off by default.
