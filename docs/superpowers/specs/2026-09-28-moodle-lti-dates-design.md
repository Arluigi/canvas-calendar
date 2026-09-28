# Moodle quiz dates through the Canvas LTI launch

**Date:** 2026-09-28
**Status:** approved design; the author waived spec review and asked for it to be built

MCB 364 runs its Assignments and Pre-Lab Quizzes as Learn@Illinois Moodle
quizzes embedded in Canvas. Canvas holds no date for any of them: `due_at`,
`unlock_at` and `lock_at` are all null on all 13. The deadline exists only on
the Moodle page ("Closes: Wednesday, September 30, 2026, 9:00 AM"). Today they
sit in the digest's undated list, and Assignment 3 was not on the calendar
two days before it closed.

## What was verified (2026-09-28)

- **Learn@Illinois directly is a dead end.** The course does not appear when
  the student logs in at learn.illinois.edu; the LTI launch provisions a
  separate account. No calendar export, no web-service token.
- **The Canvas token is enough.** `GET /courses/:id/external_tools/sessionless_launch`
  with `launch_type=assessment`, `assignment_id` and `id` (the tool id, from
  `external_tool_tag_attributes.content_id`) returns a one-time URL. Fetched
  with a browser `User-Agent` and `Accept: text/html` (without them Canvas
  answers 400), it runs the LTI 1.3 flow:
  Canvas assignment page → POST `lti.learn.illinois.edu/enrol/lti/login.php`
  → Canvas `/api/lti/authorize` → POST `/enrol/lti/launch.php` → `/mod/quiz/view.php`.
- **view.php carries both facts we need.** An open quiz shows `Opened:` /
  `Closes:` and an "Attempt quiz" button. A finished one shows `Closed:` and
  an attempt table with `Status | Finished`. Fixtures of both, trimmed and
  scrubbed, are in `tests/fixtures/moodle_quiz_*.html`.
- **Canvas-side completion lags a week.** Grades pass back, but Assignment 1,
  finished Sep 1, was graded in Canvas on Sep 8. Reading `Finished` from
  Moodle clears the event when the work is done, not a week after.

## Design

### Scope

An assignment is a Moodle item when its `external_tool_tag_attributes.url`
host is `lti.learn.illinois.edu`. Matched on the link, not the course, so any
course using the same service is covered. Only items Canvas leaves undated
and not already complete are read — a Canvas due date always wins, and work
Canvas already reports done needs no launch.

### `moodle.py` (new)

- `is_moodle(raw: dict) -> bool` — the link test above.
- `parse_quiz_page(html) -> MoodleQuiz(closes: datetime | None, finished: bool)`.
  Pure. Strips script/style and tags, then reads the first
  `Closes:|Closed:|Due:` followed by a Moodle long date
  (`%A, %B %d, %Y, %I:%M %p`), interpreted in America/Chicago. `finished` is
  `Status Finished` in the attempts table. A page with no such line returns
  `closes=None` — never a guessed date.
- `MoodleReader(client).read(course_id, assignment_id, tool_id) -> MoodleQuiz`.
  Runs the flow above in its own cookie jar. **Only two form actions may ever
  be submitted: `/enrol/lti/login.php` and `/enrol/lti/launch.php`.** The quiz
  page itself contains the `startattempt.php` form; any other form, a missing
  form, or a non-200 raises `MoodleUnavailable` rather than being followed.
  Stops at the first `/mod/<name>/view.php`. A 401 from Canvas raises
  `TokenExpired` as everywhere else. After one transport-level failure
  (connect error, timeout) the reader marks itself down and fails the rest
  immediately, so the Wednesday 05:00–08:00 maintenance window costs one
  timeout, not thirteen.
- `MoodleCache` — JSON at `~/.config/canvas-calendar/moodle_cache.json`:
  `{assignment_id: {closes, finished, read_at}}`.

### Pipeline

`collect()` calls `resolve_moodle(items, raw, course_id, reader, cache, notes)`
after `build_assignments` and before `resolve_undated`, so a Moodle date
outranks a module-title guess. For each Moodle item:

- **Read succeeds with a date:** `due_at = closes`, `source = Source.MOODLE`,
  `provenance = "Moodle quiz page (Learn@Illinois)"`; `completed = finished`.
  Cache updated.
- **Read fails, or the page has no date:** fall back to the cache entry and
  note `Moodle unavailable for <course> <name> (<reason>); using what it said
  on <read_at>`. No cache entry: the item stays UNRESOLVED and the note says
  so.
- `finished` is honoured even without a date.

The fallback is not optional. `diff()` prunes every stored event not seen in a
run; an item that reverts to UNRESOLVED during a Moodle outage would have its
calendar event deleted.

`Source.MOODLE = "moodle"` is a new enum member. Its times are exact, so the
event is timed (not all-day) and untagged; the event body says where the date
came from via `provenance`. `dedupe` needs no change: the item keeps the
Canvas namespace and, now dated, ranks as a Canvas assignment. A summary note
(`read N Moodle dates`) goes to the digest.

### Cost

One launch per unresolved, incomplete Moodle item per run — at most 13 today,
about 3 s each, on the 07:00 debrief and the 07:15 / 19:15 syncs. Read-only:
nothing past view.php is ever requested.

## Testing

- `parse_quiz_page` on both fixtures (open: Sep 30 09:00, not finished;
  finished: Sep 2 09:00, finished), on a `Due:` variant, and on a page with
  no date line (`closes=None`).
- `MoodleReader` against `httpx.MockTransport`: the happy path lands on
  view.php; a page whose only form is `startattempt.php` raises instead of
  posting; a timeout marks the reader down.
- `resolve_moodle`: fills an undated item; leaves Canvas-dated and
  Canvas-completed items untouched and unread; falls back to the cache on
  failure; stays UNRESOLVED with no cache; sets `completed` from `finished`.
- A live test against MCB 364 Assignment 3, skipped unless live tests are
  requested, like the existing `test_eventkit_live.py`.

## Out of scope

Non-quiz Moodle activities (the parser accepts `Due:` but none exist yet to
verify against), and Moodle courses with no Canvas launch.
