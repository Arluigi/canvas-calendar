"""When is a Canvas assignment done?

Measured against live data on 2026-08-31 across 190 assignments. Two facts
drive every rule here.

`graded` with no `submitted_at` is normal, not anomalous: 100 of 219
assignments are `external_tool`, graded by passback, and never record a Canvas
submission timestamp. A test based on `submitted_at` would miss most completed
work.

But bare `graded` is not evidence either. MCB 436's 'Class 1 - Poll' is
`graded` with neither a score nor a timestamp -- a gradebook placeholder for
work that was never started. Treating that as complete would clear it from the
calendar and the student would never see it again.

So `graded` counts only when corroborated by a score or a timestamp. Every
rule fails toward keeping the event: an extra event is untidy, a missing one
loses coursework.
"""

from __future__ import annotations

_DONE_STATES = ("submitted", "pending_review")


def is_complete(submission: dict | None) -> bool:
    """True only on positive evidence that the work was turned in or excused."""
    if not submission:
        return False
    if submission.get("excused"):
        return True
    state = submission.get("workflow_state")
    if state in _DONE_STATES:
        return True
    if state == "graded":
        # `is not None` deliberately, not truthiness: a score of 0.0 is a real
        # grade and must count as complete.
        return submission.get("score") is not None or bool(submission.get("submitted_at"))
    return False
