"""Choose a calendar backend from config.

`calendar_backend` has deliberately no default. Defaulting to "outlook" would
be wrong for every new user; defaulting to "eventkit" would silently move an
existing install onto a different backend. Both failures are silent, so the
key is required and its absence names the command that fixes it.
"""

from __future__ import annotations

from canvas_calendar.calendars.base import CalendarAdapter


class UnknownBackend(RuntimeError):
    """calendar_backend is missing or names a backend we do not have."""


def _build_outlook(opts: dict):
    from canvas_calendar.calendars.graph_auth import GraphAuth
    from canvas_calendar.calendars.outlook import OutlookAdapter
    from canvas_calendar.config import load_graph_client_id

    return OutlookAdapter(
        auth=GraphAuth(client_id=load_graph_client_id()),
        reminder_timed=opts.get("reminder_minutes_timed", 15),
        reminder_all_day=opts.get("reminder_minutes_all_day", 1440),
    )


def _build_eventkit(opts: dict):
    from canvas_calendar.calendars.eventkit import EventKitAdapter

    return EventKitAdapter(
        reminder_timed=opts.get("reminder_minutes_timed", 15),
        reminder_all_day=opts.get("reminder_minutes_all_day", 1440),
    )


# Names only. Dispatch resolves the builder at call time rather than holding
# a reference captured at import, so the builders stay patchable in tests.
BACKENDS = ("eventkit", "outlook")


def make_adapter(opts: dict) -> tuple[CalendarAdapter, str]:
    """Return (adapter, calendar_id) for the configured backend."""
    name = opts.get("calendar_backend")
    if name not in BACKENDS:
        raise UnknownBackend(
            f"calendar_backend is {name!r}; expected one of "
            f"{list(BACKENDS)}. Run: canvas-calendar setup"
        )
    adapter = _build_eventkit(opts) if name == "eventkit" else _build_outlook(opts)
    return adapter, adapter.ensure_calendar(opts["assignments_calendar"])
