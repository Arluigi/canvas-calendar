"""Decide which unread mail is worth a line in the morning debrief.

A list of everything unread is just your inbox with extra steps -- a bookstore
receipt and a campus-wide massmail earn the same visual weight as a note from
an instructor. So this scores instead of listing.

The rule that keeps it honest: filtered mail is COUNTED and characterised, never
silently dropped. You always learn that fourteen other things arrived and why
they were judged routine, so a misjudgement is visible rather than invisible.
"""

from __future__ import annotations

import re

# Senders and subjects that are almost never actionable. Deliberately narrow:
# a false "routine" is worse than a false "important", so patterns must be
# specific enough that a real message cannot plausibly match.
_NOISE_SENDER = re.compile(
    r"no[-_.]?reply|donotreply|notifications?@|receipts?@|billing@|"
    r"newsletter|mailer|automated|postmaster|alerts?@",
    re.IGNORECASE,
)
_NOISE_SUBJECT = re.compile(
    r"\breceipt\b|\border confirmation\b|\bmassmail\b|\bnewsletter\b|"
    r"\bunsubscribe\b|\bpromotion\b|\bsurvey\b|\byour statement\b|"
    r"\bshipped\b|\bdelivery\b|\bwebinar\b|\bmarketing\b",
    re.IGNORECASE,
)

# Words that indicate the sender wants something from you specifically.
_ACTION = re.compile(
    r"\bdeadline\b|\bdue\b|\bplease\b|\brespond\b|\breply\b|\brequired\b|"
    r"\bappointment\b|\bmeeting\b|\breschedul|\bcancel|\bexam\b|\bquiz\b|"
    r"\bgrade\b|\bmissing\b|\burgent\b|\basap\b|\baction needed\b|"
    r"\bconfirm\b|\bsign up\b|\bregistration\b|\binterview\b",
    re.IGNORECASE,
)


def _names(instructors: list[str]) -> list[str]:
    """Last names from Course Explorer strings like 'Garcia, M'."""
    out = []
    for raw in instructors or []:
        for part in raw.split(";"):
            last = part.split(",")[0].strip()
            if len(last) > 2:
                out.append(last.lower())
    return out


def score(msg: dict, me: str, instructors: list[str] | None = None) -> tuple[int, str]:
    """Return (score, reason). Anything scoring > 0 is shown."""
    sender = f"{msg.get('from', '')} {msg.get('from_address', '')}"
    subject = msg.get("subject", "") or ""
    to = [t.lower() for t in msg.get("to", [])]
    from_addr = (msg.get("from_address") or "").lower()

    # This tool mails the debrief from the same account it reads, so without
    # this the debrief highlights itself as important every single morning.
    if me and from_addr == me.lower():
        return -1, "sent by you"

    # Hard exclusions first.
    if _NOISE_SENDER.search(sender):
        return -1, "automated sender"
    if _NOISE_SUBJECT.search(subject):
        return -1, "bulk or transactional"

    points, reasons = 0, []

    # Addressed to you by name rather than blasted to a list.
    if me.lower() in to and len(to) <= 5:
        points += 2
        reasons.append("addressed to you")
    elif not to or len(to) > 20:
        points -= 1
        reasons.append("bulk recipients")

    if msg.get("importance") == "high":
        points += 2
        reasons.append("flagged high")

    for last in _names(instructors):
        if last in sender.lower():
            points += 3
            reasons.append("from an instructor")
            break

    if _ACTION.search(subject):
        points += 2
        reasons.append("asks for something")

    # A reply means you are already in the thread.
    if re.match(r"^\s*re:", subject, re.IGNORECASE):
        points += 1
        reasons.append("reply to a thread")

    if sender.lower().endswith("illinois.edu") or "illinois.edu" in sender.lower():
        points += 1
        reasons.append("university sender")

    return points, ", ".join(reasons) or "no strong signal"


def triage(
    messages: list[dict], me: str, instructors: list[str] | None = None, limit: int = 6
) -> tuple[list[dict], list[str]]:
    """Split unread mail into what to show and a summary of what was not.

    Returns (highlights, filtered_reasons). The second value exists so the
    debrief can say what it set aside; hiding that would make a bad filter
    indistinguishable from a quiet inbox.
    """
    scored = []
    filtered: list[str] = []
    for m in messages:
        pts, why = score(m, me, instructors)
        if pts > 0:
            scored.append(({**m, "why": why}, pts))
        else:
            filtered.append(f"{m.get('from', '?')[:28]} — {why}")
    scored.sort(key=lambda x: (-x[1], x[0].get("received", "")))
    return [m for m, _ in scored[:limit]], filtered
