# canvas-calendar

Syncs UIUC class meetings and Canvas assignment deadlines into one Outlook
calendar, and emails a morning debrief. Runs unattended on this Mac.

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

Plists in `deploy/`. Exit codes: 0 clean, 1 apply errors, 2 Canvas token
expired, 3 Outlook auth failed.

## Recurring chore

The Canvas token expires roughly monthly (Illinois caps it). Canvas OAuth2
would fix this, but developer keys are admin-issued and Illinois 403s both
token create and regenerate via API. `canvas-calendar token` prints the steps.

## Design principle

Every failure mode here is silent omission — work that exists but never
surfaces. Extra events are cheap; a missing one is not. Hence 0-point items
are calendared by default, undatable items are reprinted in every digest, and
filtered email is counted rather than dropped. When extending this, ask what a
change could make *invisible*, not just what it adds.

## Session Log

### 2026-08-25
- Completed: Built the whole project — read path (Course Explorer + Canvas,
  DST-safe), module date extraction, SQLite diff engine, Outlook adapter via
  Graph device-code auth, 157 events live, recurring class meetings with
  holiday exclusions, manual overrides layer, daily LaunchAgent + digest,
  morning debrief email with mail triage, hardware wake at 06:55.
  247 tests, ruff clean, 34 commits, PR #1 open.
- Next: 33 assignments still have no date anywhere (MCB 436 polls, MCB 364
  `Wk1`–`Wk11`) — need a posted schedule like the MCB 354 one to resolve.
  Canvas token expires 2026-09-24.

## Installing for a new user

Run these in order. Steps 2 and 5 must not be swapped — the Calendar
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
Accounts. The tool cannot see a Google account that only exists in a browser
tab.

**Google refuses calendar creation** (`EKErrorDomain Code=17`), verified
2026-08-31. iCloud and Exchange allow it. On a Google account, create a
calendar named `UIUC Assignments` at calendar.google.com first, then re-run
setup. Writing *events* works on all three.

**Author's machine note:** the original install uses the legacy `com.aryan.*`
LaunchAgent labels. `install-agents` writes `io.github.canvas-calendar.*`;
running it there would schedule every job twice. See `deploy/README.md`.
