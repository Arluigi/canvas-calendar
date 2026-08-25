"""Pure diff between desired assignments and stored state.

Total by construction: every input assignment yields exactly one plan entry, so
nothing can be silently dropped between fetch and write. Deletions are emitted
explicitly for anything in state that the current fetch no longer covers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from canvas_calendar.models import Assignment, Source
from canvas_calendar.state import StateStore


class Action(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    NOOP = "noop"
    SKIP = "skip"  # no usable date; digest only, never calendared


@dataclass(frozen=True)
class PlanEntry:
    action: Action
    uid: str
    assignment: Assignment | None
    title_hash: str = ""
    due_key: str | None = None
    source: str = ""


def _hash(a: Assignment) -> str:
    return hashlib.sha256(a.name.strip().encode()).hexdigest()[:16]


def diff(assignments: list[Assignment], store: StateStore) -> list[PlanEntry]:
    """Compare desired state against stored state.

    The comparison key is (due_at, title_hash, source). Including `source` is
    what makes a Canvas-backfilled due date register as an UPDATE to the same
    UID rather than appearing as a second, duplicate event beside the extracted
    one.
    """
    plan: list[PlanEntry] = []
    seen: set[str] = set()

    for a in assignments:
        if a.due_at is None or a.source is Source.UNRESOLVED:
            plan.append(PlanEntry(Action.SKIP, a.uid, a))
            continue

        seen.add(a.uid)
        due_key = a.due_at.isoformat()
        title_hash = _hash(a)
        prior = store.get(a.uid)

        if prior is None:
            action = Action.CREATE
        elif (prior.due_at, prior.title_hash, prior.source) == (
            due_key,
            title_hash,
            a.source.value,
        ):
            action = Action.NOOP
        else:
            action = Action.UPDATE

        plan.append(PlanEntry(action, a.uid, a, title_hash, due_key, a.source.value))

    for stale in sorted(store.all_uids() - seen):
        plan.append(PlanEntry(Action.DELETE, stale, None))
    return plan
