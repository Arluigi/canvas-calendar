"""Apply a diff plan to a calendar.

Two invariants drive the design:

1. State advances only after a successful calendar write. A failed write leaves
   state untouched, so the next run retries it rather than losing the change.
2. Every destructive operation passes through assert_ours first, independently
   of whatever the adapter does. State corruption must never become calendar
   destruction.

Failures are per-event: one bad write does not abort the rest of the run.
"""

from __future__ import annotations

from collections import Counter

from canvas_calendar.calendars.base import CalendarAdapter, ForeignEventError, assert_ours
from canvas_calendar.diff import Action, PlanEntry
from canvas_calendar.state import StateStore

_WRITES = (Action.CREATE, Action.UPDATE)


def apply_plan(
    plan: list[PlanEntry],
    adapter: CalendarAdapter,
    calendar_id: str,
    store: StateStore,
    *,
    dry_run: bool = True,
    errors: list[str] | None = None,
) -> Counter:
    """Execute a plan. Returns counts per action, plus an "error" tally.

    Pass `errors` to collect human-readable failure reasons for the digest. A
    counted error with no explanation is a silent failure wearing a number, and
    this project exists to eliminate those.
    """
    counts: Counter = Counter()
    if errors is None:
        errors = []

    for entry in plan:
        counts[entry.action.value] += 1

        if entry.action is Action.SKIP or entry.action is Action.NOOP:
            continue
        if dry_run:
            continue

        try:
            if entry.action in _WRITES:
                assert_ours(entry.uid)
                adapter.upsert(calendar_id, entry.uid, entry.assignment)
                store.upsert(
                    entry.uid,
                    due_at=entry.due_key,
                    title_hash=entry.title_hash,
                    source=entry.source,
                )
            elif entry.action is Action.DELETE:
                # Guard before the adapter is even consulted. A row in state
                # that does not look like ours means state is corrupt, and the
                # safe response is to leave both the event and the row alone.
                assert_ours(entry.uid)
                adapter.delete(calendar_id, entry.uid)
                store.delete(entry.uid)
        except ForeignEventError as exc:
            counts["error"] += 1
            errors.append(f"{entry.uid}: refused as not ours ({exc})")
        except Exception as exc:  # noqa: BLE001 -- see below
            # Deliberately broad. A single failing event must not abort the
            # run, and its state is left unadvanced so the next run retries it.
            # The reason is recorded rather than swallowed, so the digest can
            # say what went wrong instead of only that something did.
            counts["error"] += 1
            errors.append(f"{entry.uid}: {type(exc).__name__}: {exc}")

    return counts
