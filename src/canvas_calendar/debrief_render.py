"""Render the morning debrief as an email.

Ordered by what changes your next hour, not by what is easiest to fetch:
schedule, then deadlines, then anything new, then the standing gaps. A source
that failed says so in place; it is never quietly missing.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta

from canvas_calendar.timeutil import CHICAGO, parse_canvas_ts, to_local

_CSS = """
body{font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     color:#1a1a1a;max-width:640px;margin:0 auto;padding:20px}
h1{font-size:20px;margin:0 0 2px}
.sub{color:#666;font-size:13px;margin-bottom:22px}
h2{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:#444;
   border-bottom:1px solid #e3e3e3;padding-bottom:5px;margin:26px 0 10px}
.row{margin:0 0 9px}
.when{display:inline-block;min-width:96px;color:#555;font-variant-numeric:tabular-nums}
.tag{font-weight:600}
.meta{color:#777;font-size:13px}
.body{color:#555;font-size:13px;margin:2px 0 0 96px}
.urgent{color:#b3261e;font-weight:600}
.soon{color:#8a5a00}\n.meet{background:#e8f0fb;color:#1a4d8f;font-size:11px;padding:1px 6px;border-radius:3px;margin-right:6px}
.empty{color:#888;font-style:italic}
.gap{background:#fbf7e8;border-left:3px solid #d9b45b;padding:9px 12px;margin:8px 0;
     font-size:13px}
.err{background:#fdeaea;border-left:3px solid #b3261e;padding:9px 12px;margin:8px 0;
     font-size:13px}
footer{margin-top:30px;padding-top:12px;border-top:1px solid #e3e3e3;
       color:#888;font-size:12px}
"""


def _e(s: str) -> str:
    return html.escape(str(s or ""))


def render(data: dict) -> str:
    now = data["now"]
    p: list[str] = [
        f"<style>{_CSS}</style>",
        f"<h1>{now:%A, %B %-d}</h1>",
        f'<div class="sub">Morning debrief · generated {now:%-I:%M %p}</div>',
    ]

    # --- today's schedule, coursework and real life together
    p.append("<h2>Today</h2>")
    timed = [e for e in data.get("events", []) if not e["all_day"]]
    if timed:
        for e in timed:
            bits = [b for b in (e.get("location"), e.get("organizer")) if b]
            meta = f' <span class="meta">· {_e(" · ".join(bits))}</span>' if bits else ""
            # A meeting from the personal calendar is the thing most likely to
            # collide with a class, so make it visually distinct.
            mark = "" if e.get("kind") == "course" else '<span class="meet">meeting</span> '
            p.append(
                f'<div class="row"><span class="when">{_e(e["time"])}</span>'
                f'{mark}<span class="tag">{_e(e["subject"])}</span>{meta}</div>'
            )
    else:
        p.append('<div class="empty">Nothing scheduled today.</div>')

    # --- due
    p.append("<h2>Due</h2>")
    due = data.get("due", [])
    if due:
        for a in due:
            when = to_local(a.due_at)
            days = (when.date() - now.date()).days
            if days <= 0:
                cls, label = "urgent", "TODAY"
            elif days == 1:
                cls, label = "soon", "tomorrow"
            else:
                cls, label = "", when.strftime("%a %b %-d")
            t = "" if a.due_at.hour == 23 else when.strftime(" %-I:%M %p")
            p.append(
                f'<div class="row"><span class="when {cls}">{label}{t}</span>'
                f'<span class="tag">{_e(a.course)}</span> {_e(a.name[:70])}</div>'
            )
    else:
        p.append('<div class="empty">Nothing due in the next 7 days.</div>')

    # --- announcements
    anns = data.get("announcements", [])
    p.append(f"<h2>New announcements ({len(anns)})</h2>")
    if anns:
        for a in anns:
            when = to_local(parse_canvas_ts(a["posted"])) if a.get("posted") else None
            stamp = when.strftime("%a %b %-d") if when else ""
            p.append(
                f'<div class="row"><span class="when">{stamp}</span>'
                f'<span class="tag">{_e(a["title"][:70])}</span></div>'
                f'<div class="body">{_e(a["body"])}</div>'
            )
    else:
        p.append('<div class="empty">Nothing new since the last debrief.</div>')

    # --- canvas messages
    convos = data.get("conversations", [])
    if convos:
        p.append(f"<h2>Canvas inbox ({len(convos)} unread)</h2>")
        for c in convos:
            p.append(
                f'<div class="row"><span class="tag">{_e(c["from"])}</span> '
                f'— {_e(c["subject"][:64])}</div>'
                f'<div class="body">{_e(c["preview"])}</div>'
            )

    # --- outlook mail
    mail = data.get("mail")
    if mail is None:
        p.append("<h2>Email</h2>")
        p.append('<div class="err">Could not read your inbox — check Mail.Read is granted.</div>')
    elif mail:
        p.append(f"<h2>Unread email ({len(mail)})</h2>")
        for m in mail:
            stamp = m["received"][5:10] if m.get("received") else ""
            p.append(
                f'<div class="row"><span class="when">{_e(stamp)}</span>'
                f'<span class="tag">{_e(m["from"][:32])}</span> — {_e(m["subject"][:60])}</div>'
                f'<div class="body">{_e(m["preview"])}</div>'
            )
    else:
        p.append("<h2>Email</h2><div class=\"empty\">No unread mail in the last 48 hours.</div>")

    # --- standing gaps
    gaps = data.get("unresolved", {})
    if gaps:
        total = sum(len(v) for v in gaps.values())
        p.append(f"<h2>Not on your calendar ({total})</h2>")
        p.append(
            '<div class="gap">Real assignments with no due date in Canvas and no date in '
            "their module. Listed every day so they cannot quietly disappear.<br><br>"
            + "<br>".join(
                f"<b>{_e(c)}</b> ({len(v)}): {_e(', '.join(v[:5]))}"
                + (f" +{len(v) - 5} more" if len(v) > 5 else "")
                for c, v in sorted(gaps.items())
            )
            + "</div>"
        )

    for err in data.get("errors", []):
        p.append(f'<div class="err">{_e(err)}</div>')

    note = data.get("token_note", "")
    p.append(f"<footer>canvas-calendar · {_e(note)}</footer>")
    return "\n".join(p)


def subject_line(data: dict) -> str:
    """Front-load the two numbers that decide whether this gets opened."""
    now: datetime = data["now"]
    due_today = sum(
        1
        for a in data.get("due", [])
        if a.due_at and to_local(a.due_at).date() == now.date()
    )
    events = [e for e in data.get("events", []) if not e["all_day"]]
    meetings = [e for e in events if e.get("kind") != "course"]
    bits = []
    if due_today:
        bits.append(f"{due_today} due today")
    if events:
        bits.append(f"{len(events)} on your calendar")
    if meetings:
        bits.append(f"{len(meetings)} meeting{'s' if len(meetings) != 1 else ''}")
    anns = len(data.get("announcements", []))
    if anns:
        bits.append(f"{anns} new announcement{'s' if anns != 1 else ''}")
    tail = " · ".join(bits) if bits else "nothing due"
    return f"{now:%a %b %-d} — {tail}"


def next_week(assignments, now: datetime):
    horizon = now + timedelta(days=7)
    return sorted(
        (
            a
            for a in assignments
            if a.due_at and not a.digest_only and now - timedelta(hours=12) <= a.due_at <= horizon
        ),
        key=lambda a: a.due_at,
    )


__all__ = ["CHICAGO", "next_week", "render", "subject_line"]
