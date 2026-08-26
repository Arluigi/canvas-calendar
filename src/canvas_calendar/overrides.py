"""Manual overrides for work Canvas gets wrong or omits entirely.

Canvas is not always authoritative. The MCB 354 adaptive-quiz schedule the
instructor posted (FA26 post1) disagrees with Canvas in five places and lists
two quizzes -- Ch3 and Ch5 -- that have no Canvas assignment at all. Every
disagreement has the posted schedule EARLIER than Canvas, so honouring it is
also the conservative choice: being early cannot cost points, being late can.

Two mechanisms:

- `additions`   real work with no Canvas assignment behind it. Gets the "man-"
                UID namespace so it can never collide with a Canvas id.
- `date_overrides`  a corrected due date for an existing Canvas assignment,
                keyed by its assignment id.

Both are hand-edited and authoritative. Nothing here is inferred.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from canvas_calendar.models import Assignment, Source
from canvas_calendar.timeutil import CHICAGO, to_local

DEFAULT_OVERRIDES = Path.home() / ".config" / "canvas-calendar" / "overrides.json"


def _parse_local(raw: str) -> datetime:
    """'2026-08-31T09:00' in America/Chicago. Local wall time, never UTC --
    a posted schedule says '9 am', not an instant."""
    return datetime.fromisoformat(raw).replace(tzinfo=CHICAGO)


def load_overrides(path: Path | None = None) -> dict:
    p = Path(path or DEFAULT_OVERRIDES)
    if not p.exists():
        return {"additions": [], "date_overrides": {}}
    data = json.loads(p.read_text())
    return {
        "additions": data.get("additions", []),
        "date_overrides": data.get("date_overrides", {}),
    }


def apply_overrides(
    items: list[Assignment], overrides: dict, applied: list[str] | None = None
) -> list[Assignment]:
    """Correct dates and append manual additions.

    `applied` collects a human-readable line per change so the run can report
    what it altered. A silent correction is indistinguishable from a bug.
    """
    if applied is None:
        applied = []

    by_id = {str(a.canvas_id): a for a in items}
    for raw_id, spec in overrides.get("date_overrides", {}).items():
        target = by_id.get(str(raw_id))
        if target is None:
            applied.append(f"override {raw_id}: no matching assignment (stale entry?)")
            continue
        was = target.due_at
        target.due_at = _parse_local(spec["due_local"])
        target.provenance = spec.get("note", "manual override")
        # Show both sides in local time -- printing the stored UTC instant
        # next to a local wall time invites exactly the confusion this
        # project keeps running into.
        applied.append(
            f"override {target.name[:38]}: "
            f"{to_local(was):%b %d %I:%M %p} -> {to_local(target.due_at):%b %d %I:%M %p}"
        )

    for spec in overrides.get("additions", []):
        items.append(
            Assignment(
                canvas_id=spec["id"],
                name=spec["name"],
                points=float(spec.get("points", 0.0)),
                due_at=_parse_local(spec["due_local"]),
                course=spec["course"],
                source=Source.EXTRACTED,
                provenance=spec.get("note", "manual addition"),
                namespace="man-",
            )
        )
        applied.append(f"added {spec['course']} {spec['name']} ({spec['due_local']})")

    return items
