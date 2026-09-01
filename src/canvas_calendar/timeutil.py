"""Timezone handling for Canvas timestamps.

Canvas returns UTC. Courses run in America/Chicago, which crosses the CDT->CST
boundary on 2026-11-01 -- mid-semester. Never use a fixed UTC offset here: an
11:59PM local deadline arrives as 04:59Z before the transition and 05:59Z after,
and a hardcoded -5 silently shifts every November deadline by an hour.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

CHICAGO = ZoneInfo("America/Chicago")

# A deadline within this window of local midnight is administrative
# ("submit by end of day"), not a real timed deadline.
END_OF_DAY_WINDOW = timedelta(minutes=5)


def parse_canvas_ts(raw: str) -> datetime:
    """Parse a Canvas ISO-8601 UTC timestamp into an aware datetime."""
    # fromisoformat handles the trailing "Z" natively on 3.11+
    return datetime.fromisoformat(raw)


def to_local(dt: datetime) -> datetime:
    """Convert an aware datetime to America/Chicago via the tz database."""
    if dt.tzinfo is None:
        raise ValueError("refusing to localize a naive datetime")
    return dt.astimezone(CHICAGO)


def is_end_of_day(dt: datetime) -> bool:
    """True if the deadline falls in the last few minutes of its local day."""
    local = to_local(dt)
    midnight = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - local <= END_OF_DAY_WINDOW
