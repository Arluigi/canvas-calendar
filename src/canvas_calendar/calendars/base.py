"""Calendar adapter interface and the never-delete-foreign-events guard."""

from __future__ import annotations

from typing import Protocol

from canvas_calendar.models import Assignment

# Every event this tool creates carries this prefix. Changing it orphans
# everything already written to a calendar.
UID_PREFIX = "cc-"


class ForeignEventError(RuntimeError):
    """Raised when an operation targets an event this tool did not create."""


def assert_ours(uid: str | None) -> None:
    """Gate every destructive operation.

    Deliberately strict, and deliberately not clever: no normalizing, no
    trimming, no case folding. An event we did not create must never be
    modified or deleted, whatever else goes wrong upstream. The cost of a
    false negative is a missing event; the cost of a false positive is
    destroying somebody's real calendar.
    """
    if not uid or not isinstance(uid, str):
        raise ForeignEventError(f"refusing to touch non-managed event: {uid!r}")
    if not uid.startswith(UID_PREFIX) or len(uid) <= len(UID_PREFIX):
        raise ForeignEventError(f"refusing to touch non-managed event: {uid!r}")


class CalendarAdapter(Protocol):
    """Backend-agnostic calendar operations.

    Implementations must never delete or modify an event whose UID fails
    assert_ours, even when a caller asks them to.
    """

    def ensure_calendar(self, name: str) -> str:
        """Return the id of a calendar named `name`, creating it if absent."""
        ...

    def upsert(self, calendar_id: str, uid: str, assignment: Assignment) -> None: ...

    def delete(self, calendar_id: str, uid: str) -> None: ...

    def list_uids(self, calendar_id: str) -> set[str]: ...

    def upsert_recurring(self, calendar_id: str, meeting) -> str | None:
        """Create or refresh a weekly class-meeting series.

        Returns the backend's event id, or None when the meeting has no
        usable weekday or start time.
        """
        ...
