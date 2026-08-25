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
| MCB 320 | 0 | 0 | — | No assignments API; schedule in SubHeaders |
| **Total** | **184** | **141** | **43** | |

The failure mode is **undated Canvas assignments**, not external platforms. The
work exists in Canvas; it simply carries no due date, so every due-date query
skips it silently.

Specifics:

- **MCB 364** — one dated item (Checkpoint #1, Sep 10). The other 22 are
  `Pre-Lab Quiz Wk1`, `Wk1`–`Wk11`, and `Image submission Wk1`–`Wk11`: a real
  weekly lab cadence, entirely invisible to due-date queries.
- **MCB 436** — 14 `Class N - Poll` items and 4 extra-credit summaries, undated.
  Its module titles date all 14 lectures (`8/24 - Lecture 1` … `12/7 - Lecture 14`),
  but the polls are numbered `Class 1, 3, 4, … 16, 17` — running to 17 against
  only 14 lectures. `Class N` is not `Lecture N`; no safe mapping exists from the
  data alone. This is the one genuinely unresolved case.
- **MCB 320** — zero assignments and no syllabus body, but a content survey
  (2026-08-25) found the full schedule published as dated SubHeader text: 28
  lectures, four review sessions, and four exams (Sep 16, Oct 12, Nov 6, Dec 9).
  Recoverable in full. See `modules/`.

### Where schedules actually live

The decisive follow-up finding: **courses publish real dates in module titles
and SubHeaders, not in due-date fields.** Of the 43 undated assignments, 22
(all of MCB 364) resolve exactly through their containing module's stated dates,
and MCB 320's entire assessment schedule is recoverable the same way. Only
MCB 436's 14 polls resist, because their numbering does not match its lecture
numbering.

Net effect: **zero items require date inference.** Every undated assignment is
either extracted from authored module text, routed to the digest as extra
credit, excluded as an administrative artifact, or explicitly surfaced as
unresolved. The approval gate remains, but it now reviews extracted dates rather
than guesses.

**Consequence for design:** Canvas's built-in ICS feed is not a viable shortcut.
It is generated from due dates, so it would surface 141 items, omit 43, and
imply full coverage. Rejected for that reason, not for effort.

## Goals

- Weekly recurring calendar events for class meetings, with building and room.
- Daily unattended sync of assignment deadlines to a calendar.
- Undated assignments get dates extracted from authored module text, clearly
  labeled, human-approved once. What cannot be resolved is surfaced, not guessed.
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
| Undated handling | Extract from module text, then verify | Courses publish real dates in module titles; 0 items need inference. |
| Calendar backend | Outlook preferred, Google fallback | User preference; adapter keeps it swappable. |
| Runtime | LaunchAgent on the Mac | Can hold real credentials; proven pattern on this machine. |
| Event shape | Hybrid — timed vs all-day | Due times genuinely cluster into two kinds. |
| Filtering | Explicit extra-credit match, not point value | Point value is not a reliable proxy for importance — see Inclusion rules. |
| Architecture | Deterministic core, LLM at the edges | Daily sync and date extraction are both parsing; the LLM only does drift detection. |

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

- **One-time review** of the 43 extracted dates before first write.
- **Monthly drift check** — re-read syllabi, announcements, and module structure,
  and report anything resembling a deadline the deterministic path cannot see.
  This is what catches a course restructuring mid-semester.

Note that the survey shrank the LLM's role considerably. Date resolution turned
out to be parsing, not judgment, so it moved into `modules/` as plain code. The
two halves still fail independently: a bad extraction cannot corrupt the daily
sync, and the daily sync keeps working if extraction is wrong.

## Components

```
canvas-calendar/
  catalog/    UIUC Course Explorer client
  canvas/     thin Canvas REST client
  modules/    date extraction from module titles and SubHeader text
  infer/      residual inference for what modules/ cannot date
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

**`modules/`** — the primary resolver for undated work, and the reason
inference is nearly unnecessary. A content survey on 2026-08-25 found that
courses publish their real schedules in module titles and SubHeader text, not
in due-date fields. Two patterns, both deterministic to parse:

*Dated module titles* (MCB 364):

```
Week 1 - Intro to Cell culture and Aseptic Techniques - August 26th/28th
Week 7 - Midterm - October 7th/ 9th
Weeks 12 & 13 - Independent project  November 11th/13th & November 18th/20th
Week 15 - December 9th - NO CLASS
```

An undated assignment resolves through its **containing module**:
`Pre-Lab Quiz -Week 1` sits inside the Week 1 module, so its date comes from
that module's stated dates. This is extraction from an authored source, not
inference, and it is strictly better than any arithmetic scheme — note that
weeks 12 and 13 are merged and week 15 is cancelled, so both ordinal counting
*and* label arithmetic would produce wrong dates here. Reading the title does
not.

*Dated SubHeaders* (MCB 320):

```
September 16: EXAM 1 (Lectures 1-7 of PARTS 1-2)
October 12:   EXAM 2 (Lectures 8-13 of PART 3)
November 6:   EXAM 3 (Lectures 14-21 of PARTS 4-5)
December 9:   EXAM 4 (Lectures 22-28 of PART 6)
```

MCB 320 exposes zero Canvas assignments, but its entire 28-lecture schedule and
all four exams are dated SubHeader text. The course is fully recoverable; it was
never invisible, only absent from the assignments API. Exams and review sessions
are matched by keyword (`exam`, `midterm`, `review for`) and calendared.

Both patterns require a year, which module text omits. The term's `startDate`
and `endDate` from Course Explorer supply it, with a rollover check so
"December 9" and "August 26" resolve into the correct term.

**`infer/`** — the residual case, now much smaller. After module extraction the
only genuinely ambiguous items are MCB 436's 14 `Class N - Poll` entries. Its
module titles date the lectures (`8/24 - Lecture 1` … `12/7 - Lecture 14`), but
the polls are numbered `Class 1, 3, 4, … 16, 17` — fourteen polls whose numbers
run to seventeen, against only fourteen lectures. `Class N` is therefore **not**
`Lecture N`, and no safe mapping exists from the data alone. These are treated
as unresolved: digest-only, never calendared on a guess, and surfaced once for
the user to map or dismiss. Anything the module extractor cannot date follows
the same path.

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

Separate so either can be collapsed independently, and so a bad extraction run is
undone by clearing one calendar rather than untangling a merged one.

## Flows

### Setup (once, interactive)

```
resolve sections (confirm with user)
  → generate recurring class events
  → extract dates from module titles + SubHeaders
  → present full extracted schedule for approval
  → on approval: freeze to resolved_dates.yaml, then write
```

`resolved_dates.yaml` is hand-editable and authoritative thereafter. If an
instructor shifts lab week 7, one line changes; extraction is not re-run.

### Daily sync (unattended)

```
load config + frozen resolved dates
  → fetch assignments (6 courses)
  → merge dated (Canvas) + extracted (yaml)
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

Every item under management — dated or extracted — is a real Canvas assignment
with a stable numeric ID (verified: MCB 364's `Wk1` is assignment `1605622`).
There is therefore exactly one UID scheme, and no extracted item lacks an anchor:

```
uid = f"cc-{canvas_assignment_id}"
```

The `cc-` prefix is what the never-delete-foreign-events check tests against.

**Canvas always supersedes extraction.** Instructors commonly backfill due dates.
When an item that was previously undated acquires a real `due_at`:

- The UID is unchanged, so this is an **update**, never a duplicate event.
- The event's `source` flips from `extracted` to `canvas`, the `[extracted]`
  label is removed, and the change is called out in the digest.
- The corresponding `resolved_dates.yaml` entry is marked superseded rather than
  deleted, so the history of what was derived remains auditable.

**Hand edits to `resolved_dates.yaml` are first-class changes.** The diff hashes
the merged record — `(due_at, title, source)` — not just the Canvas payload, so
editing a date by hand produces a `changed` verdict and updates the calendar on
the next run, exactly as a Canvas-side change would.

### Event shape

Due times cluster into two kinds, and the calendar reflects that:

- **Timed events** for deadlines during class hours (MCB 244 readings at 2:00PM,
  exactly when lecture begins).
- **All-day banners** for administrative end-of-day deadlines (11:59PM).

Extracted events carry `[extracted]` in the title and a body line quoting the
module text they came from (e.g. *"from module: Week 1 … August 26th/28th"*), so
a derived date is never mistaken for an instructor-set due date at a glance.

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
7. **Anything undatable is announced in every digest**, never dropped. This
   currently means MCB 436's 14 `Class N - Poll` items, listed as *"unresolved —
   poll numbering does not match lecture numbering, not calendared."* A known
   blind spot that is announced is survivable; a silent one is what this project
   exists to prevent. If module extraction later fails for a course whose
   structure changed, that course joins this list rather than silently
   contributing nothing.

## Testing

- Unit: module-title and SubHeader date parsing against real MCB 364 and MCB 320
  fixtures, including the merged `Weeks 12 & 13` title, the cancelled `Week 15`,
  and the `November 20, November 30` two-date SubHeader.
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
3. ~~**MCB 320 coursework source.**~~ **Resolved 2026-08-25.** Its 28-lecture
   schedule and four exam dates are dated SubHeader text, extractable
   deterministically. What remains open is narrower: MCB 436's 14 `Class N`
   polls cannot be mapped to its 14 dated lectures, since the poll numbers run
   to 17. Needs one decision from the user — map them by hand, or leave them
   digest-only.
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
| Extraction wrong for MCB 364 | 22 misplaced events | Human approval gate; `[extracted]` labels quoting source text; single-calendar rollback |
| Canvas token expires (~monthly) | Sync stops until manual renewal | Hard failure + 5-day warning; milestone 7 pursues OAuth refresh |
| Course restructures its modules mid-term | Extraction stops dating some items | Undatable items announced in every digest (rule 7), never silently omitted |
| Module title date is ambiguous or malformed | Wrong date, plausible-looking | Parse failures are surfaced, not guessed around; approval gate reviews all extracted dates |
| 0-point item treated as unimportant | Required FSHN coursework dropped | Filter on explicit extra-credit marker; 0-point calendared by default |
| Mac asleep at scheduled time | Missed run | Catch-up logic: each run reconciles full horizon, not just the delta |

## Milestones

1. Verify Outlook/Graph auth works. **Gate — determines the backend.**
2. Course Explorer client + section resolution → class meeting events.
3. Canvas client + deterministic daily sync for the 141 dated items.
4. Module date extraction + approval gate for the 43 undated, including
   MCB 320's exam schedule from SubHeaders.
5. LaunchAgent, digest, notifications.
6. Monthly drift check. Also decide MCB 436's poll mapping — the only item the
   survey could not resolve.
7. Investigate OAuth2 refresh-token auth to remove the ~monthly manual token
   renewal. Not blocking, but the system is not truly unattended until it lands.
