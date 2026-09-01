# Portable, multi-backend canvas-calendar

**Date:** 2026-08-31
**Status:** approved design, not yet implemented

Make canvas-calendar installable by other UIUC students on macOS, against
whichever calendar they already use, driven by whatever coding agent they have
— without disturbing the working install on the author's machine.

Adds one unrelated feature the author asked for in the same pass: clearing
assignments from the calendar and debrief as they are completed on Canvas.

## Context

The tool today is single-tenant in a literal sense: it syncs one person's
Canvas to one Outlook calendar, and several facts about that person are
compiled into the source.

- `config.py` reads Canvas credentials from `~/code/canvas-mcp/.env`.
- `graph_auth.py` hardcodes the Illinois Entra tenant; `config.json` holds a
  personal app registration's client id.
- `meetings.py` hardcodes `TERM_START`, `TERM_END` and the Fall 2026 holiday
  list. `pipeline.py` hardcodes `TERM_YEAR = 2026`. `sync_meetings.py`
  hardcodes the Course Explorer path `/2026/fall`.
- `"UIUC Assignments"` appears as a literal in four call sites.
- `OutlookAdapter` is the only `CalendarAdapter`, and is constructed directly
  rather than through any factory.

A separate hazard, discovered while surveying: the LaunchAgents execute
`/Users/aryansachdev/code/canvas-calendar/.venv/bin/canvas-calendar`, a path
inside the git working tree. Whatever branch is checked out at 07:15 is what
runs. `feat/read-path` is 30 commits ahead of `main`, so a `git checkout main`
silently reverts the scheduled sync to a tool without module extraction,
overrides, meetings, or the debrief — and it would still exit 0.

Every user targeted by this work is a UIUC student on macOS. That narrows the
problem considerably and is assumed throughout.

## Goals

1. A second UIUC student can install and run this in a few minutes, guided by
   Claude Code, Gemini CLI, or Codex CLI, without being technical.
2. iCloud, Google, and Outlook calendars are all supported.
3. Assignments disappear from the calendar and the debrief once completed on
   Canvas.
4. The author's existing install does not change behaviour at any point.

## Non-goals

- Non-macOS platforms. EventKit is the universal adapter precisely because
  every target user is on a Mac.
- Non-UIUC institutions. Course Explorer has no equivalent elsewhere.
- A portable debrief email. It stays Outlook-only and opt-in (see below).
- An MCP server. Agents are needed to *install* the tool, not to run it; MCP
  would add per-agent configuration for no runtime benefit.

## Section 1 — Completion tracking

### Why the obvious rule is wrong

Measured against live Canvas data on 2026-08-31, across 190 assignments:

| workflow_state | count |
|---|---|
| unsubmitted | 175 |
| graded | 13 |
| submitted | 1 |
| pending_review | 1 |

`graded` with `submitted_at: None` is common and normal — MCB 244 Chapter 1,
MCB 354 iClicker Grade, MCB 364 Wk1. These are `external_tool` assignments
graded by passback, which never record a Canvas submission timestamp. A
completion test based on `submitted_at` would miss most completed work.

`external_tool` accounts for 100 of 219 assignments. Many will never register
a submission until a grade posts, so clearing is partial by nature. That is
the safe direction and is documented rather than fixed.

One trap: `MCB 436 Class 1 - Poll` is `graded` with both `score: None` and
`submitted_at: None` — a gradebook placeholder, not evidence of work done.
Treating bare `graded` as complete would clear untouched work.

### The predicate

```
excused                              -> complete
submitted | pending_review           -> complete
graded AND (score OR submitted_at)   -> complete
otherwise                            -> not complete
```

Written to fail toward keeping an event. The last clause is what protects the
MCB 436 polls.

### Mechanism

`list_assignments()` gains `include[]=submission` — same endpoint, same
pagination, no additional API calls.

Completion is **not** a filter. `Assignment` gains `completed: bool`,
parallel to the existing `digest_only` flag. `diff()` maps it to DELETE when a
state row exists and SKIP when it does not.

Two consequences follow from that choice. Retracting a submission restores the
event automatically, because the item is still in the fetch. And the digest
can report what it hid: `cleared 6 completed` with names, mirroring the
established rule that filtered mail is counted rather than dropped. Silently
removing items would violate the project's founding principle.

### Limits

Only real Canvas assignments participate. The MCB 320 quizzes and
SubHeader-derived exams have no Canvas submission behind them and can never
auto-clear. A `canvas-calendar done <uid>` command would address this; it is
deliberately out of scope here.

`clear_completed` defaults to true, with a config key to disable it.

## Section 2 — The adapter seam

`CalendarAdapter` and `assert_ours` are sound and do not change in substance.

**Protocol completion.** `upsert_recurring` is called by `sync_meetings` but
absent from the Protocol; it is added, so the Protocol describes the real
interface.

**Factory.** `OutlookAdapter` and `GraphAuth` are constructed directly at
`cli.py:101`, `daily.py:125`, `run_debrief.py:69` and in `sync_meetings.py`.
These collapse to one `make_adapter(config)`. The `"UIUC Assignments"`
literal at those same sites moves to config, keeping that string as its
default.

**`EventKitAdapter`.** New, macOS-only, reached via
`pyobjc-framework-EventKit` (verified importable; no Xcode or compiler
needed). It writes into Calendar.app, which already holds the user's iCloud,
Google, or Exchange account, so one adapter serves all three backends with no
OAuth of its own.

- `ensure_calendar` matches an `EKCalendar` by title among sources where
  `allowsContentModifications` is true. Creates it where the source permits,
  and fails with an actionable message where it does not.
- **UID storage** uses `EKEvent.URL`, holding
  `x-canvas-calendar:cc-mi-5440557`. `NSURL` requires a valid URI, so a bare
  `cc-…` string will not round-trip. `state.db` additionally maps our UID to
  the EventKit identifier for lookup, but **the URL is re-read and verified
  before every destructive operation**. The lookup may be wrong; the guard may
  not. This preserves the existing invariant that state corruption can never
  become calendar destruction.
- `list_uids` supplies a date window, which EventKit predicates require: term
  bounds plus one month of margin.
- Recurrence uses `EKRecurrenceRule`. Holiday exclusions still delete
  individual occurrences — EventKit has no EXDATE-on-create either, so this is
  parity with Graph, not an improvement.
- Timezone handling simplifies: `America/Chicago` is passed directly, with no
  Windows timezone name mapping.

**Targeted fix.** `sync_meetings.py` calls Canvas through raw `httpx` rather
than `CanvasClient`, bypassing `TokenExpired`. A 401 during a meetings sync
currently surfaces as a JSON decode error instead of the clean exit-code-2
path the rest of the tool has. It moves onto `CanvasClient`. Its unrelated
side effect — writing `instructors` into config mid-sync — is left alone.

### Spike results (run 2026-08-31 on macOS 26.5.2)

**1. Does the `URL` property survive a save/refetch? YES**, on both source
types available for testing. `x-canvas-calendar:cc-spike-12345` came back
byte-identical from a genuinely fresh `EKEventStore` on CalDAV/iCloud and on
Exchange. The `notes` fallback is not needed for those two. **Google remains
untested** — see the gap below.

**2. Can a LaunchAgent obtain Calendar (TCC) permission? YES, with one
condition.** A background agent has its own TCC identity, separate from the
Terminal's: the first run reported `authorizationStatus = 0` (not determined)
and blocked ~37s on an interactive prompt. Once granted, the status persists
as `3` (full access) and subsequent agent runs are instant, enumerating all 12
calendars with a clean exit.

*Design consequence:* `setup` must trigger the permission grant
**interactively**, while the user is present. If the LaunchAgent is installed
before the grant, its first scheduled run stalls on a prompt nobody sees. This
makes the grant a required, ordered step of the wizard rather than something
left to first run.

**3. Does calendar creation work? YES on iCloud and Exchange, NO on Google.**
Google's CalDAV returns `EKErrorDomain Code=17 "That account does not allow
calendars to be added or removed."` — tested with a fresh store, so this is
genuine and not the stale-handle artifact described below. iCloud and Exchange
both allow creation outright.

*Design consequence:* `ensure_calendar` must treat creation failure as an
expected path for Google, not an error. Setup tells the user to create the
calendar at calendar.google.com and re-run; the tool then finds it by title.

*Method note:* an initial run reported `EKErrorDomain Code=17 "That account
does not allow calendars to be added"` for iCloud. That was an artifact —
`EKEventStore.reset()` invalidates cached `EKSource` objects, so the creation
was attempted against a stale handle. Re-running with a fresh store per phase
gave ALLOWED. Any future EventKit work must not reuse source or calendar
objects across a `reset()`.

### Remaining gap

None. Google was linked to the author's Mac on 2026-08-31 and tested directly:
the `URL` round-trip **survived** on Google CalDAV, and calendar creation
**refused**, as recorded above. All three source types — CalDAV/iCloud,
Exchange, and Google CalDAV — are now measured rather than assumed.

### Verified incidentally

`UIUC Assignments` is visible to EventKit as a writable **Exchange** calendar
on the `School` source — the same calendar the Graph adapter writes to today.
An EventKit backend could therefore manage the author's existing calendar
directly, with no Graph dependency at all. Not acted on: the Outlook path
works, and the debrief still requires Graph for `Mail.Send`.

## Section 3 — Configuration

All new keys default to today's compiled-in behaviour, so an existing
`config.json` keeps working unchanged.

```jsonc
{
  "calendar_backend": "outlook",       // "outlook" | "eventkit" — required, no default
  "assignments_calendar": "UIUC Assignments",
  "clear_completed": true,
  "term": {
    "year": 2026, "season": "fall",
    "start": "2026-08-24", "end": "2026-12-09",
    "holidays": ["2026-09-07",
                 "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24",
                 "2026-11-25", "2026-11-26", "2026-11-27", "2026-11-28",
                 "2026-11-29"]
  },
  "timezone": "America/Chicago",
  "client_id": "<entra app id>",       // outlook backend only
  "debrief_enabled": false,
  "debrief_to": "",
  "debrief_hour": 7
}
```

`holidays` is an explicit list of dates, not a range syntax. Ranges would need
a parser and a second way to be wrong, for the sake of eight characters.

The debrief keys stay **flat**, matching the existing `debrief_to` and
`debrief_hour` already in the author's config. Nesting them under a `debrief`
object would break that file, which Section 5 forbids. `debrief_enabled` is
new and defaults to false for new installs; it is written as true on the
author's machine in Section 5 step 1, preserving current behaviour.

`calendar_backend` deliberately has **no default**. A default of `outlook`
would be wrong for new users; a default of `eventkit` would silently change
the author's machine. A missing key is a hard error naming `setup`. Section 5
writes the key explicitly on the author's machine *before* any code lands, so
the requirement is satisfied there from the start.

**Term data.** `TERM_START`, `TERM_END`, `HOLIDAYS` leave `meetings.py`;
`TERM_YEAR` leaves `pipeline.py`; the Course Explorer path in
`sync_meetings.py` derives from `term.year` and `term.season`. A bundled
`terms.json` ships UIUC dates for the current and next term so setup can
choose without asking the user to look them up. `doctor` warns when today
falls outside the configured range — the failure this prevents is a spring
installation silently syncing a fall calendar.

**Canvas credentials.** The `~/code/canvas-mcp/.env` path is machine-specific.
Resolution order becomes: `CANVAS_API_TOKEN` environment variable, then
`~/.config/canvas-calendar/credentials.json` (mode 0600, written via the
existing `TokenStore` pattern), then the legacy `.env` path. The legacy
fallback stays so the author's machine is unaffected.

## Section 4 — Install and onboarding

**Distribution.** `uv tool install git+https://github.com/Arluigi/canvas-calendar`,
wrapped in an `install.sh` that installs `uv` first if absent. This puts the
binary on a stable path, never inside a git checkout.

**Agent instructions.** `AGENTS.md` becomes the single canonical file — the
existing `CLAUDE.md` operations manual plus a new "Installing for a new user"
section written as an agent-followable procedure. `CLAUDE.md` and `GEMINI.md`
become symlinks to it, so Claude Code, Codex, and Gemini CLI all read
identical instructions from one source. Safe here because every target user is
on macOS.

**`canvas-calendar setup`**, an interactive wizard, also drivable by an agent:

1. Verify macOS and Python version.
2. Choose backend. EventKit is recommended and explained in terms of what the
   user already has; Outlook/Graph is offered for those who want it.
3. EventKit path: request Calendar access, list writable calendars, let the
   user pick or create. Detect the "no accounts configured" case and say so in
   plain language rather than reporting zero calendars.
4. Outlook path: device-code login as today.
5. Prompt for a Canvas token, with the generation steps printed; validate it
   immediately and report its expiry date.
6. Select the term from `terms.json`.
7. Run a dry-run preview so the user sees what would be written before
   anything is.
8. Offer to install the LaunchAgents.

**`canvas-calendar doctor`**, the diagnosis command an agent runs when a user
reports trouble: Canvas token validity and days to expiry, backend
reachability, target calendar existence and writability, whether today falls
inside the configured term, LaunchAgent load state, and the last run's
timestamp and exit code.

**LaunchAgents.** Templates render with the invoking user's paths and a
neutral label (`io.github.canvas-calendar.sync`, replacing `com.aryan.*`),
pointing at the installed binary rather than a repo checkout.

**Debrief.** Requires the Outlook backend and is opt-in, default off. It
depends on Graph `Mail.Send`, which has no equivalent for an iCloud or Google
user without the OAuth problems that ruled out per-backend cloud APIs.
EventKit users get `canvas-calendar digest`. Setup does not offer the debrief
unless the Outlook backend was chosen.

## Section 5 — Isolating the author's machine

Ordered, and the order is the point.

1. **Pin the config now**, before any code changes: write
   `"calendar_backend": "outlook"` and an explicit `term` block into the
   existing `config.json`. Purely additive; current code ignores unknown keys.
2. **Install a stable copy** from the current commit via `uv tool install`,
   repoint both plists at `~/.local/bin/canvas-calendar`, and reload them. The
   working tree becomes development-only, and `git checkout` can no longer
   change what runs at 07:15.
3. **Verify** a dry run from the installed binary reports zero creates,
   updates and deletes.
4. **Merge `feat/read-path` into `main`** so `main` stops being a 30-commit
   regression trap.
5. **All work happens on a feature branch**, with `main` holding the known-good
   tool.
6. **Add a UID-format regression test** asserting `cc-<namespace><id>` is
   unchanged. Every row in `state.db` and every live Outlook event depends on
   it; changing it silently orphans 157 events.
7. **Upgrade the installed copy only after** the refactor's Outlook path runs
   clean against a full day/night cycle.

Steps 1–4 are worth doing regardless of whether the rest proceeds: they
close a hazard that exists today.

## Implementation order

This is too large for one implementation plan and decomposes into three, each
independently shippable and each leaving the tool working:

1. **Completion tracking** (Section 1). Touches `canvas/client.py`,
   `models.py`, `pipeline.py`, `diff.py`, `debrief.py`. No adapter work.
   Ships to the author's machine first and is useful on its own.
2. **Machine isolation** (Section 5 steps 1–4). No code changes at all —
   config, install location, plists, and a branch merge. Closes an existing
   hazard and should not wait for anything.
3. **Portability** (Sections 2–4). The adapter seam, config schema, EventKit
   adapter, setup wizard and install story. The three spike questions in
   Section 2 were answered on 2026-08-31 and all three passed; the only
   untested path is Google's CalDAV, for want of a Google account to test
   against. Ready to plan.

Plans 1 and 2 are independent of each other and of 3. Plan 3 assumes both.

## Testing

- Completion predicate: table-driven over the six observed submission shapes,
  including the `graded`/no-score/no-timestamp placeholder.
- `diff()`: completed with a state row emits DELETE; without one, SKIP;
  retraction re-creates.
- Digest: reports cleared items by name rather than dropping them.
- `EventKitAdapter`: unit tests against a fake `EKEventStore`; one integration
  test marked `macos` writing to a scratch calendar and cleaning up.
- Config: an existing `config.json` with no new keys still loads, except
  `calendar_backend`, whose absence must raise a message naming `setup`.
- UID format regression test, per Section 5 step 6.
- The existing Outlook tests are the regression net and stay untouched.
