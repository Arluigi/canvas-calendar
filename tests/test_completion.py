"""Cases are real submission shapes observed on 2026-08-31, not invented ones."""

import pytest

from canvas_calendar.completion import is_complete


@pytest.mark.parametrize(
    "submission,expected,why",
    [
        (None, False, "assignment carried no submission key at all"),
        ({}, False, "empty submission"),
        ({"workflow_state": "unsubmitted"}, False, "the 175-item majority"),
        # graded with a score but no timestamp: external_tool passback.
        # MCB 244 'Chapter 1', MCB 354 'iClicker Grade', MCB 364 'Wk1'.
        (
            {"workflow_state": "graded", "score": 10.0, "submitted_at": None},
            True,
            "graded by passback, no Canvas submission timestamp",
        ),
        # A zero score is still a grade. Truthiness on score would break this.
        (
            {"workflow_state": "graded", "score": 0.0, "submitted_at": None},
            True,
            "zero is a real score",
        ),
        # THE TRAP: MCB 436 'Class 1 - Poll'. Gradebook placeholder, no work done.
        (
            {"workflow_state": "graded", "score": None, "submitted_at": None},
            False,
            "graded placeholder with no evidence of work",
        ),
        (
            {"workflow_state": "graded", "score": None, "submitted_at": "2026-08-26T15:04:29Z"},
            True,
            "graded, no score yet, but it was turned in",
        ),
        # FSHN 120 'PILLAR A - REFLECTIVE ASSIGNMENT'
        (
            {"workflow_state": "submitted", "score": None, "submitted_at": "2026-08-27T18:13:33Z"},
            True,
            "submitted",
        ),
        # MCB 436 'Lecture 1 - Specific Activity'
        (
            {
                "workflow_state": "pending_review",
                "score": None,
                "submitted_at": "2026-08-31T21:13:46Z",
            },
            True,
            "awaiting instructor review",
        ),
        ({"workflow_state": "unsubmitted", "excused": True}, True, "excused is done"),
        ({"workflow_state": "unsubmitted", "excused": False}, False, "not excused"),
    ],
)
def test_is_complete(submission, expected, why):
    assert is_complete(submission) is expected, why
