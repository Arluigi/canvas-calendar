# canvas-calendar

Syncs UIUC class meetings and Canvas assignment deadlines into one calendar
— iCloud, Google or Outlook — and emails a morning debrief. Runs unattended
on macOS. See README.md for the user-facing install.

Design rationale lives in `docs/superpowers/specs/` — read it before changing
behaviour; it records the audit every decision came from.

## Commands

```bash
uv run pytest -q && uv run ruff check src tests   # before any commit
canvas-calendar preview            # read path, prints nothing else
canvas-calendar sync               # dry run; --live to write, --force to rewrite
canvas-calendar sync --course "MCB 244"   # filtered run (never prunes)
canvas-calendar meetings --live    # recurring class series
canvas-calendar debrief            # preview email; --live sends, --force re-sends
canvas-calendar daily --live       # what the LaunchAgent runs
canvas-calendar token              # Canvas expiry + renewal steps
canvas-calendar digest             # last run's report
canvas-calendar login              # Graph device-code auth (after scope changes)
canvas-calendar setup              # first-run wizard; backend, permission, token, term
canvas-calendar doctor             # token, term, calendar, scheduled job
canvas-calendar install-agents     # render + load the LaunchAgents for this user
uv run pytest -m live              # touches a real calendar; deselected by default
```

## Gotchas that cost hours

- **Course Explorer 403s on some user agents.** Send an explicit one.
- **Canvas section names embed the CRN** (`MCB 354 ADI Fall 2026 CRN40604`),
  which is Course Explorer's section id. Section resolution needs no user input.
- **Real dates often live in module titles and SubHeaders**, not due-date
  fields. MCB 320 exposes zero assignments but publishes its whole exam
  schedule as SubHeader text.
- **Never filter on `points > 0`.** FSHN 120 has 20 zero-point items; seven are
  required coursework with no "extra credit" marker.
- **A filtered run must pass `prune=False`.** `diff()` emits DELETE for any
  state row absent from the fetch, so pruning on a `--course` subset would
  delete every other course's events.
- **Graph allows one reminder per event** and has no EXDATE on create; holiday
  occurrences are deleted after the series exists.
- **Timed events need local wall time + a Windows zone name.** A UTC instant
  drifts every event across the Nov 1 DST change.
- **Bare `graded` is not completion.** MCB 436's polls are `graded` with no
  score and no `submitted_at` — a gradebook placeholder, not work done.
  `completion.py` requires corroboration, and the digest names everything it
  clears so a false positive is visible before the work is missed.
- **Canvas is not authoritative.** `~/.config/canvas-calendar/overrides.json`
  holds corrections; every one is reported on each run.

## Scheduled

```
06:55  pmset repeat wakeorpoweron   (AC power only; macOS ignores it on battery)
07:00  com.aryan.canvas-debrief
07:15 + 19:15  com.aryan.canvas-calendar
```

Both run `~/.local/bin/canvas-calendar`, NOT the repo `.venv` — the branch
checked out must never decide what runs at 07:15. After changing code, run
`uv tool install --force .` to upgrade the scheduled copy; until then the
schedule keeps running the previously installed build.

These use the legacy `com.aryan.*` labels. `install-agents` writes
`io.github.canvas-calendar.*`, so running it here would schedule everything
twice — see `deploy/README.md`. Plist templates live in
`src/canvas_calendar/data/` so they ship in the wheel.

Exit codes: 0 clean, 1 apply errors, 2 Canvas token expired, 3 Outlook auth
failed.

## Recurring chore

The Canvas token expires roughly monthly (Illinois caps it). Canvas OAuth2
would fix this, but developer keys are admin-issued and Illinois 403s both
token create and regenerate via API. `canvas-calendar token` prints the steps.

## Design principle

Every failure mode here is silent omission — work that exists but never
surfaces. Extra events are cheap; a missing one is not. Hence 0-point items
are calendared by default, undatable items are reprinted in every digest,
filtered email is counted rather than dropped, and completed work is named as
it is cleared. When extending this, ask what a
change could make *invisible*, not just what it adds.

## Session Log

### 2026-09-01
- Completed: Made the project shareable. Machine isolation (LaunchAgents now
  run `~/.local/bin/canvas-calendar`, not the git working tree; `main` merged
  forward from 30 commits behind). Completion tracking — assignments leave the
  calendar once Canvas reports them submitted/graded/excused, with the digest
  naming what it cleared. EventKit adapter so iCloud, Google and Exchange all
  work with no OAuth of our own; adapter factory; pure `terms.py`; portable
  Canvas credentials. Onboarding: `doctor`, `setup`, `install-agents`,
  `AGENTS.md` as the single agent instruction file (CLAUDE.md and GEMINI.md
  symlink to it), `install.sh`, README. Repo made public and the install
  verified end to end from the public git URL.
  335 tests + 4 live, ruff clean, 66 commits.
- Next: nothing outstanding from the spec. Open ideas: `canvas-calendar done
  <uid>` so the MCB 320 quizzes and SubHeader exams (no Canvas submission, so
  they can never auto-clear) can be marked done by hand; a portable debrief
  email (today needs Graph Mail.Send, so Outlook-only). Watch the first friend
  through the Google path — creating the calendar by hand is the one rough
  edge. **Canvas token expires 2026-09-24.**

## Installing for a new user

Run these in order. Steps 2 and 5 must not be swapped — the Calendar
permission prompt has to be answered by a human who is present, and a
LaunchAgent installed before that grant will stall on a prompt nobody sees.

0. **Get a Canvas API token first — it can take a day or two.** Try
   canvas.illinois.edu > Account > Settings > + New Access Token; if that is
   unavailable, request one at https://help.uillinois.edu/TDClient/42/UIUC/Requests/TicketRequests/NewForm?ID=4AZBjiZfXWs_&RequestorType=Service
   The tool is read-only against Canvas (every call is a GET), which is what
   the request form's "use" field should say.
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
Accounts. The tool cannot see a Google account that only exists in a browser
tab.

**Google refuses calendar creation** (`EKErrorDomain Code=17`), verified
2026-08-31. iCloud and Exchange allow it. On a Google account, create a
calendar named `UIUC Assignments` at calendar.google.com first, then re-run
setup. Writing *events* works on all three.

**Author's machine note:** the original install uses the legacy `com.aryan.*`
LaunchAgent labels. `install-agents` writes `io.github.canvas-calendar.*`;
running it there would schedule every job twice. See `deploy/README.md`.
