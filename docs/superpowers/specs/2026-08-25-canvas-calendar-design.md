# Canvas → Calendar Sync — Design

**Date:** 2026-08-25
**Status:** Approved for planning
**Author:** Aryan Sachdev (with Claude Code)

## Problem

Six active Fall 2026 courses publish coursework inconsistently. Checking all of
them daily is the manual burden this project removes. Two distinct needs:

1. **Class meeting times and locations** — needed once, then recurring weekly.
2. **Assignment deadlines** — checked daily, written to a calendar.

The initial framing was "every class delivers content differently." An audit
showed the real problem is narrower and more dangerous than that.

## Audit findings (2026-08-25)

All six active courses, via the Canvas API:

| Course | Total | Has due date | Undated | Verdict |
|---|---:|---:|---:|---|
| MCB 244 | 36 | 36 | 0 | Fully reliable |
| FSHN 120 | 68 | 66 | 2 | Reliable |
| MCB 354 | 26 | 25 | 1 | Reliable |
| MCB 436 | 31 | 13 | 18 | Half-blind |
| MCB 364 | 23 | 1 | 22 | Nearly blind |
| MCB 320 | 0 | 0 | — | Invisible |
| **Total** | **184** | **141** | **43** | |

The failure mode is **undated Canvas assignments**, not external platforms. The
work exists in Canvas; it simply carries no due date, so every due-date query
skips it silently.

Specifics:

- **MCB 364** — one dated item (Checkpoint #1, Sep 10). The other 22 are
  `Pre-Lab Quiz Wk1`, `Wk1`–`Wk11`, and `Image submission Wk1`–`Wk11`: a real
  weekly lab cadence, entirely invisible to due-date queries.
- **MCB 436** — 14 `Class N - Poll` items and 4 extra-credit summaries, undated.
  Polls are in-class, so they are anchored to lecture meeting days.
- **MCB 320** — zero assignments, no syllabus body, 7 modules containing only
  Files and SubHeaders. Source of coursework unknown.

**Consequence for design:** Canvas's built-in ICS feed is not a viable shortcut.
It is generated from due dates, so it would surface 141 items, omit 43, and
imply full coverage. Rejected for that reason, not for effort.

## Goals

- Weekly recurring calendar events for class meetings, with building and room.
- Daily unattended sync of assignment deadlines to a calendar.
- Undated assignments get inferred dates, clearly labeled, human-approved once.
- Nothing is ever silently dropped.

## Non-goals

- Scheduling work blocks or planning study time. This records deadlines; it does
  not plan the week.
- Submitting assignments or interacting with Canvas beyond reading.
- Supporting terms beyond Fall 2026 in v1. Term is configuration, not a feature.

## Key decisions

| Decision | Choice | Rationale |
|---|---|---|
| Meeting-time source | UIUC Course Explorer API | Structured days/times/building/room. Canvas has none of this. |
| Undated handling | Infer, then verify before first write | 22 of 43 would otherwise be unreviewed guesses. |
| Calendar backend | Outlook preferred, Google fallback | User preference; adapter keeps it swappable. |
| Runtime | LaunchAgent on the Mac | Can hold real credentials; proven pattern on this machine. |
| Event shape | Hybrid — timed vs all-day | Due times genuinely cluster into two kinds. |
| Filtering | Explicit extra-credit match, not point value | Point value is not a reliable proxy for importance — see Inclusion rules. |
| Architecture | Deterministic core, LLM at the edges | Daily sync is a diff problem; only inference needs judgment. |

### On the meeting-time source

Canvas course codes embed the Course Explorer term ID:
`mcb_244_120268_262964` → term `120268` = Fall 2026. The mapping is therefore
deterministic, not a scrape. Verified live:

```
MCB 244 §A — Lecture, TR 2:00PM–3:20PM
Foellinger Auditorium, room AUD — Garcia, M
08-24-26 → 12-09-26
```

Note: the API rejects some user agents with HTTP 403; curl succeeds. The client
must set an explicit user agent.

### On architecture

The daily path covers 141 of 184 items and requires no judgment, so it is plain
code: fetch, diff, write. The LLM is reserved for the two genuinely fuzzy jobs,
neither of which runs daily:

- **One-time inference** of the 43 undated dates.
- **Monthly drift check** — re-read syllabi, announcements, and MCB 320's module
  files, and report anything resembling a deadline the deterministic path cannot
  see.

The two halves fail independently: a bug in inference cannot corrupt the daily
sync, and the daily sync keeps working if inference is wrong.

## Components

```
canvas-calendar/
  catalog/    UIUC Course Explorer client
  canvas/     thin Canvas REST client
  infer/      one-time date inference for undated assignments
  calendar/   adapter interface + Outlook and Google implementations
  state/      SQLite diff state
  digest/     digest.md writer + macOS notification
```

**`catalog/`** — parses a Canvas course code into `(subject, number, term)`,
fetches section XML, returns meetings: days, start, end, building, room, type,
instructor, start/end date.

**`canvas/`** — our own thin REST client reading the token from
`~/code/canvas-mcp/.env`. Deliberately does **not** import the `canvas_mcp`
package: that repo moved 432 commits in six months, and this project must not
break when an internal function is renamed upstream. Roughly five endpoints are
needed.

**`infer/`** — extracts `Wk(\d+)` / `Week (\d+)` / `Class (\d+)` from assignment
names and maps the parsed number to a calendar date anchored to the section's
real `startDate`. Emits proposals with a per-item rationale. Never writes to a
calendar directly.

**Critical: map the label, not the ordinal.** MCB 364's items run
`Wk1…Wk6, Wk8…Wk11` — week 7 is absent, as is `Class 2`, `12`, and `15` in
MCB 436. Treating these as a sequence and taking the *n*-th occurrence of the
meeting day would place every item after the gap one week early, silently. The
number in the label is the week index and must be used as such:

```
date = first_meeting_day_on_or_after(section.startDate) + (label_number - 1) weeks
```

Then subtract any non-instruction weeks that fall before it. A gap in the
sequence must never shift subsequent dates. This is the single most likely
source of a wrong-but-plausible schedule, so it gets direct unit tests against
the real MCB 364 and MCB 436 label sets, including the gaps.

**`calendar/`** — interface: `upsert(uid, …)`, `delete(uid)`, `list(calendar)`.
Idempotency comes from a deterministic UID derived from the Canvas assignment ID.
Everything upstream of this module is backend-agnostic.

**`state/`** — SQLite table
`(canvas_id, calendar_uid, due_at, title_hash, source, status, last_synced)`.
This is what makes a daily run a diff rather than a re-import.

## Calendars

Two, not one:

- **`UIUC Classes`** — recurring meeting blocks.
- **`UIUC Assignments`** — deadlines.

Separate so either can be collapsed independently, and so a bad inference run is
undone by clearing one calendar rather than untangling a merged one.

## Flows

### Setup (once, interactive)

```
resolve sections (confirm with user)
  → generate recurring class events
  → run inference on the 43 undated items
  → present full proposed schedule for approval
  → on approval: freeze to inferred_dates.yaml, then write
```

`inferred_dates.yaml` is hand-editable and authoritative thereafter. If an
instructor shifts lab week 7, one line changes; inference is not re-run.

### Daily sync (unattended)

```
load config + frozen inferred dates
  → fetch assignments (6 courses)
  → merge dated (Canvas) + inferred (yaml)
  → filter (see Inclusion rules)
  → diff against SQLite
  → apply: create / update / delete
  → write digest + notify
```

### Inclusion rules

An earlier draft filtered on `points > 0`. Live data shows that is unsafe.
Measured 2026-08-25:

| Course | Total | Dated | Undated | 0-point | 0-point *and* undated |
|---|---:|---:|---:|---:|---:|
| FSHN 120 | 68 | 66 | 2 | 20 | 2 |
| MCB 244 | 36 | 36 | 0 | 0 | 0 |
| MCB 320 | 0 | 0 | 0 | 0 | 0 |
| MCB 354 | 26 | 25 | 1 | 0 | 0 |
| MCB 364 | 23 | 1 | 22 | 0 | 0 |
| MCB 436 | 31 | 13 | 18 | 0 | 0 |

Two conclusions:

1. **"Undated" and "0-point" are nearly disjoint problems** — only 2 items are
   both. They need separate handling, not one combined filter.
2. **Point value does not indicate importance.** MCB 364's 22 undated lab items
   are worth 2–3 points each, so a point filter never touches them. But FSHN 120
   carries 20 zero-point items, and seven of those —
   `PILLAR A–D REFLECTIVE ASSIGNMENT (discussion)` and
   `PILLAR B/C/D - Data for Improvement (DI) quiz` — carry no "extra credit"
   marker in their names and read as required coursework. A point filter would
   have silently dropped exactly those seven, reintroducing the failure this
   project exists to eliminate.

Rules, in order:

1. If the name matches `/extra\s*credit/i` → **digest only**, tagged. This is an
   explicit signal from the instructor, unlike a point value.
2. If the assignment ID appears in the config `exclude` list → **skipped**. For
   one-time administrative forms (`DROP YOUR LOA HERE`, `REQUEST FOR STEP
   PROJECT ACCOMMODATION`) that do not apply. User-maintained, never inferred.
3. Otherwise → **calendar**, regardless of point value.

Rule 3 is the important one: a 0-point assignment is calendared by default.
Being wrong in that direction adds an event; being wrong in the other direction
loses coursework.

### Identity and precedence

Every item under management — dated or inferred — is a real Canvas assignment
with a stable numeric ID (verified: MCB 364's `Wk1` is assignment `1605622`).
There is therefore exactly one UID scheme, and no inferred item lacks an anchor:

```
uid = f"cc-{canvas_assignment_id}"
```

The `cc-` prefix is what the never-delete-foreign-events check tests against.

**Canvas always supersedes inference.** Instructors commonly backfill due dates.
When an item that was previously undated acquires a real `due_at`:

- The UID is unchanged, so this is an **update**, never a duplicate event.
- The event's `source` flips from `inferred` to `canvas`, the `[inferred]` label
  is removed, and the change is called out in the digest.
- The corresponding `inferred_dates.yaml` entry is marked superseded rather than
  deleted, so the history of what was guessed remains auditable.

**Hand edits to `inferred_dates.yaml` are first-class changes.** The diff hashes
the merged record — `(due_at, title, source)` — not just the Canvas payload, so
editing a date by hand produces a `changed` verdict and updates the calendar on
the next run, exactly as a Canvas-side change would.

### Event shape

Due times cluster into two kinds, and the calendar reflects that:

- **Timed events** for deadlines during class hours (MCB 244 readings at 2:00PM,
  exactly when lecture begins).
- **All-day banners** for administrative end-of-day deadlines (11:59PM).

Inferred events carry `[inferred]` in the title and a body line explaining the
derivation, so a guess is never mistaken for a fact at a glance.

## Time zones and DST

Canvas returns due dates in UTC. The courses run in `America/Chicago`, which
crosses the CDT→CST boundary on 2026-11-01, mid-semester. This is visible in the
real data — MCB 354's October deadlines arrive as `04:59Z` and its November ones
as `05:59Z`, both representing 11:59PM local.

Requirements:

- Convert every Canvas timestamp to `America/Chicago` using a real tz database
  (`zoneinfo`), never a fixed UTC offset. A hardcoded `-5` silently shifts every
  deadline after Nov 1 by an hour.
- Classify all-day vs timed **after** conversion to local time. An 11:59PM local
  deadline must be recognized as end-of-day in both halves of the semester.
- Write recurring class events with a local time plus timezone ID, not a UTC
  instant, so a 2:00PM lecture stays at 2:00PM across the transition.
- Test fixtures must include at least one pre-transition and one
  post-transition deadline.

## Error handling

Invariants, in priority order:

1. **Never delete an event we did not create.** Every write carries a UID prefix;
   deletion verifies it. The failure mode is wiping a real calendar, so this gets
   a dedicated test.
2. **Canvas 401 is a hard, loud failure** — notification, non-zero exit, state
   untouched. Never a silent no-op. The job additionally warns when the token is
   within 5 days of expiry (current token expires 2026-09-24).
3. **Course Explorer failure degrades per-course** — skip, continue, report in
   the digest.
4. **Calendar write failure does not advance state**, so the next run retries
   rather than losing the change.
5. **Dry-run is the default** for the first run of any newly added course.
6. **The reconcile horizon is bounded** to the term: `term_start` through
   `term_end + 7 days`, read from the section data (Fall 2026:
   2026-08-24 → 2026-12-09). Every run reconciles that whole window rather than
   a delta, so a missed run self-heals — but the window never grows into "all
   history." At 184 items this is a single cheap pass.
7. **MCB 320 is reported as an explicit coverage gap in every digest** from the
   first run: *"MCB 320 — coursework source unknown, 0 assignments visible, not
   synced."* Its resolution is deferred to the drift check, but the gap must be
   visible daily rather than surfacing once a month. A known blind spot that is
   announced is survivable; a silent one is what this project exists to prevent.

## Testing

- Unit: inference logic against real MCB 364 fixtures.
- Unit: diff logic across new / changed / removed.
- Contract: recorded fixtures for both APIs; tests never hit the network.
- Dedicated test for the never-delete-foreign-events invariant.
- Integration: dry-run against a scratch calendar before touching real ones.

## Open questions

1. **Section resolution.** Canvas's trailing id (`262964`) is an SIS id, not the
   CRN (`56301`). Automatic mapping may not be possible, in which case setup
   asks the user to pick a section once per course. Acceptable for a one-time
   step, but setup is then not fully hands-off.
2. **Academic-calendar holidays.** Course Explorer supplies meeting patterns but
   not breaks, so recurring events would place a lecture on Thanksgiving.
   Mitigation: hardcode Fall 2026 non-instruction days as a short config list,
   applied as iCalendar `EXDATE` entries on the recurring event — the standard
   mechanism, not a bespoke one. Accurate but manually maintained.
3. **MCB 320 coursework source** is unresolved until its module files are
   examined. The drift check provides detection, not a guarantee of coverage;
   until then the gap is announced in every digest (error-handling rule 7).
4. **Canvas token renewal has no unattended path.** Illinois caps access-token
   lifetime at roughly 30 days — the current token was issued 2026-08-25 and
   expires 2026-09-24, about four weeks into the semester. An "unattended daily
   sync" therefore has a scheduled hard stop roughly monthly, and renewal is a
   manual browser flow (Canvas shows a token's value exactly once; the API
   cannot bootstrap a replacement from an expired credential). The 5-day warning
   makes this survivable, not solved. Options worth investigating before relying
   on this long-term: a Canvas developer key with OAuth2 refresh tokens, which
   would make renewal genuinely unattended. Tracked as milestone 7.

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| UIUC blocks third-party OAuth to M365 | Outlook unusable | Adapter falls back to Google; test auth first, before building on it |
| Inference wrong for MCB 364 | 22 misplaced events | Human approval gate; `[inferred]` labels; single-calendar rollback |
| Canvas token expires (~monthly) | Sync stops until manual renewal | Hard failure + 5-day warning; milestone 7 pursues OAuth refresh |
| Gap in a week-label sequence | Every later inferred date off by a week | Label-indexed mapping, not ordinal; unit tests over real gapped label sets |
| 0-point item treated as unimportant | Required FSHN coursework dropped | Filter on explicit extra-credit marker; 0-point calendared by default |
| Mac asleep at scheduled time | Missed run | Catch-up logic: each run reconciles full horizon, not just the delta |

## Milestones

1. Verify Outlook/Graph auth works. **Gate — determines the backend.**
2. Course Explorer client + section resolution → class meeting events.
3. Canvas client + deterministic daily sync for the 141 dated items.
4. Inference + approval gate for the 43 undated.
5. LaunchAgent, digest, notifications.
6. Monthly drift check, including MCB 320.
7. Investigate OAuth2 refresh-token auth to remove the ~monthly manual token
   renewal. Not blocking, but the system is not truly unattended until it lands.
