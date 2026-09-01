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
table{border-collapse:collapse;width:100%;margin:0 0 4px}
td{padding:3px 0;vertical-align:baseline}
td.when{width:92px;color:#555;font-variant-numeric:tabular-nums;white-space:nowrap;
        padding-right:14px}
td.course{width:74px;font-weight:600;white-space:nowrap;padding-right:10px}
.day{font-weight:600;color:#222;margin:14px 0 3px;font-size:13px}
.day:first-of-type{margin-top:4px}
.tag{font-weight:600}
.meta{color:#777;font-size:13px}
.body{color:#555;font-size:13px;margin:1px 0 8px 106px}
.urgent{color:#b3261e;font-weight:600}
.soon{color:#8a5a00}\n
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


def _clock(hhmm: str) -> str:
    """'14:00' -> '2:00 PM'. 24h times in an email read as data, not a schedule."""
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except (ValueError, AttributeError):
        return hhmm
    ampm = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {ampm}"


def _deadline(a) -> str:
    """An 11:59 PM due time is administrative, not a schedule. Printing it on
    every row was most of the visual noise, so end-of-day says so instead."""
    when = to_local(a.due_at)
    if when.hour == 23 and when.minute >= 55:
        return "end of day"
    return when.strftime("%-I:%M %p")


def _by_day(due, now):
    """Group deadlines under one heading per day, in order."""
    groups: dict[str, list] = {}
    for a in due:
        groups.setdefault(to_local(a.due_at).date().isoformat(), []).append(a)
    out = []
    for iso in sorted(groups):
        d = datetime.fromisoformat(iso).date()
        delta = (d - now.date()).days
        if delta <= 0:
            out.append((("Today", "urgent"), groups[iso]))
        elif delta == 1:
            out.append((("Tomorrow", "soon"), groups[iso]))
        else:
            out.append(((d.strftime("%A, %B %-d"), ""), groups[iso]))
    return out


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
        p.append("<table>")
        for e in timed:
            loc = e.get("location") or ""
            meta = f' <span class="meta">· {_e(loc)}</span>' if loc else ""
            p.append(
                f'<tr><td class="when">{_e(_clock(e["time"]))}</td>'
                f'<td><span class="tag">{_e(e["subject"])}</span>{meta}</td></tr>'
            )
        p.append("</table>")
    else:
        p.append('<div class="empty">Nothing scheduled today.</div>')

    # --- due
    p.append("<h2>Due</h2>")
    due = data.get("due", [])
    if due:
        # Grouped by day so the date is stated once as a heading instead of
        # repeated on every row, which is what made this read as a wall.
        for day, items in _by_day(due, now):
            label, cls = day
            p.append(f'<div class="day {cls}">{_e(label)}</div><table>')
            for a in items:
                p.append(
                    f'<tr><td class="when">{_e(_deadline(a))}</td>'
                    f'<td class="course">{_e(a.course)}</td>'
                    f"<td>{_e(a.name[:74])}</td></tr>"
                )
            p.append("</table>")
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

    # --- outlook mail
    mail = data.get("mail")
    if mail is None:
        p.append("<h2>Email</h2>")
        p.append('<div class="err">Could not read your inbox — check Mail.Read is granted.</div>')
    elif mail:
        p.append(f"<h2>Worth a look ({len(mail)})</h2><table>")
        for m in mail:
            p.append(
                f'<tr><td class="course">{_e(m["from"][:22])}</td>'
                f'<td><span class="tag">{_e(m["subject"][:66])}</span>'
                f'<div class="body" style="margin-left:0">{_e(m["preview"][:150])}</div></td></tr>'
            )
        p.append("</table>")
        skipped = data.get("mail_filtered", [])
        if skipped:
            p.append(
                f'<div class="meta">{len(skipped)} other unread message'
                f'{"s" if len(skipped) != 1 else ""} judged routine '
                f"(receipts, bulk mail, automated senders).</div>"
            )
    else:
        n = len(data.get("mail_filtered", []))
        extra = f" {n} routine message{'s' if n != 1 else ''} set aside." if n else ""
        p.append(
            '<h2>Worth a look</h2><div class="empty">Nothing needing your attention.'
            + _e(extra)
            + "</div>"
        )

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
    bits = []
    if due_today:
        bits.append(f"{due_today} due today")
    if events:
        bits.append(f"{len(events)} on your calendar")
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
