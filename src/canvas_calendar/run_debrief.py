"""Assemble and send the morning debrief.

Each source is fetched independently and its failure is contained. A partial
debrief that names what broke is useful; one that silently drops a section
teaches you to trust something that is lying.
"""

from __future__ import annotations

from datetime import datetime

from canvas_calendar.calendars.graph_auth import GraphAuth
from canvas_calendar.calendars.outlook import OutlookAdapter
from canvas_calendar.canvas.client import CanvasClient
from canvas_calendar.config import (
    load_canvas_credentials,
    load_graph_client_id,
    load_sync_options,
)
from canvas_calendar.daily import notify, token_expiry_status
from canvas_calendar.debrief import (
    canvas_announcements,
    load_last_run,
    outlook_unread,
    save_last_run,
    send_mail,
    todays_events,
)
from canvas_calendar.debrief_render import next_week, render, subject_line
from canvas_calendar.mail_triage import triage
from canvas_calendar.models import Source
from canvas_calendar.pipeline import collect, term_courses
from canvas_calendar.timeutil import CHICAGO


# Recipient comes from config, never a guess: mailing a debrief of
# someone's coursework to the wrong address is not a recoverable mistake.
def _recipient() -> str:
    to = load_sync_options().get("debrief_to", "")
    if not to:
        raise RuntimeError("set debrief_to in ~/.config/canvas-calendar/config.json")
    return to


def _recipient_or_blank() -> str:
    try:
        return _recipient()
    except RuntimeError:
        return ""


def gather() -> dict:
    now = datetime.now(CHICAGO)
    since = load_last_run()
    errors: list[str] = []

    base_url, tok = load_canvas_credentials()
    canvas = CanvasClient(base_url, tok)
    auth = GraphAuth(client_id=load_graph_client_id())

    def attempt(label, fn, fallback):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 -- contain each source separately
            errors.append(f"{label} unavailable: {type(exc).__name__}: {exc}")
            return fallback

    course_cal = attempt(
        "course calendar", lambda: OutlookAdapter(auth=auth).ensure_calendar("UIUC Assignments"), None
    )
    events = attempt("calendar", lambda: todays_events(auth, course_cal), [])
    # None (not []) means the mail source failed, which the renderer shows
    # explicitly rather than as an empty section.
    mail = attempt("email", lambda: outlook_unread(auth), None)

    assignments = attempt("canvas assignments", lambda: collect(), [])
    course_ids = attempt(
        "canvas courses", lambda: [c["id"] for c in term_courses(canvas.list_courses())], []
    )
    announcements = attempt(
        "announcements", lambda: canvas_announcements(canvas, course_ids, since), []
    )
    # Triage rather than list: an inbox dump is your inbox with extra steps.
    highlights, filtered = [], []
    if mail:
        instructors = load_sync_options().get("instructors", [])
        highlights, filtered = triage(mail, _recipient_or_blank(), instructors)

    unresolved: dict[str, list[str]] = {}
    for a in assignments:
        if a.source is Source.UNRESOLVED:
            unresolved.setdefault(a.course, []).append(a.name[:52])

    token_note = attempt(
        "token check",
        lambda: token_expiry_status(canvas.list_tokens(), now)[1],
        "",
    )

    return {
        "now": now,
        "events": events,
        "due": next_week(assignments, now),
        "announcements": announcements,
        "mail": highlights if mail is not None else None,
        "mail_filtered": filtered,
        "unresolved": unresolved,
        "token_note": token_note,
        "errors": errors,
    }


def already_sent_today(now: datetime) -> bool:
    """One debrief per day.

    Two paths can fire the job on the same morning: a pmset scheduled wake at
    the right time, and launchd running the *missed* job when the lid is
    opened later. Without this guard an unlucky morning sends twice.
    """
    return load_last_run().date() == now.date()


def run(send: bool = True, to: str | None = None, force: bool = False) -> int:
    if send and not force and already_sent_today(datetime.now(CHICAGO)):
        print("debrief already sent today; use --force to send another")
        return 0
    data = gather()
    body = render(data)
    subject = subject_line(data)

    if not send:
        print(subject)
        print(body[:1500])
        return 0

    to = to or _recipient()
    auth = GraphAuth(client_id=load_graph_client_id())
    try:
        send_mail(auth, to, subject, body)
    except Exception as exc:
        notify("Debrief failed to send", f"{type(exc).__name__}: {exc}", urgent=True)
        raise
    save_last_run(data["now"])
    print(f"sent to {to}: {subject}")
    return 0
