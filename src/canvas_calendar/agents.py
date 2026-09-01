"""Render and install the LaunchAgents for the current user.

The binary path is resolved at install time and must point at an installed
copy, never at a path inside a git checkout. The original install ran
`<repo>/.venv/bin/canvas-calendar`, which meant whichever branch happened to
be checked out decided what ran at 07:15 -- and checking out an older branch
broke the scheduled sync entirely while looking like nothing had changed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

LABEL_SYNC = "io.github.canvas-calendar.sync"
LABEL_DEBRIEF = "io.github.canvas-calendar.debrief"
AGENT_DIR = Path.home() / "Library" / "LaunchAgents"

_CHECKOUT_MARKERS = (".venv", "/src/", "site-packages/canvas_calendar")


class DevelopmentCheckout(RuntimeError):
    """The resolved binary lives inside a git checkout, not an install."""


def render_plist(template: str, *, binary: str, label: str, home: str | None = None) -> str:
    return (
        template.replace("{{BINARY}}", binary)
        .replace("{{LABEL}}", label)
        .replace("{{HOME}}", home or str(Path.home()))
    )


def resolve_binary() -> str:
    found = shutil.which("canvas-calendar") or sys.argv[0]
    p = Path(found)
    s = str(p)
    if any(m in s for m in _CHECKOUT_MARKERS):
        raise DevelopmentCheckout(
            f"{s} looks like a development checkout, not an install.\n"
            "The scheduled job must not depend on which branch is checked out.\n"
            "Install it first:\n"
            "  uv tool install git+https://github.com/Arluigi/canvas-calendar"
        )
    return s


def _templates_dir() -> Path:
    """Templates ship inside the package.

    They used to live in deploy/, which is not included in the wheel -- so a
    `uv tool install` from git produced a tool whose install-agents crashed
    with FileNotFoundError. Keeping them in package data is what makes the
    command work for someone who never cloned the repo.
    """
    d = Path(__file__).resolve().parent / "data"
    if not d.is_dir():
        raise FileNotFoundError(f"plist templates missing from the package: {d}")
    return d


def install() -> int:
    try:
        binary = resolve_binary()
    except DevelopmentCheckout as exc:
        print(exc)
        return 1

    root = _templates_dir()
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    uid = os.getuid()
    failed = 0
    for label, name in ((LABEL_SYNC, "canvas-calendar"), (LABEL_DEBRIEF, "canvas-debrief")):
        target = AGENT_DIR / f"{label}.plist"
        target.write_text(
            render_plist((root / f"{name}.plist.template").read_text(),
                         binary=binary, label=label)
        )
        subprocess.run(["launchctl", "bootout", f"gui/{uid}/{label}"],
                       capture_output=True, check=False)
        r = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(target)],
                           capture_output=True, text=True, check=False)
        if r.returncode == 0:
            print(f"installed {label}")
        else:
            failed += 1
            print(f"FAILED   {label}: {r.stderr.strip()}")
    print(f"\nbinary: {binary}")
    return 1 if failed else 0
