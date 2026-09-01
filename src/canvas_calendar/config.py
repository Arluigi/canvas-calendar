"""Configuration. The Canvas token is shared with the canvas-mcp install."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV = Path.home() / "code" / "canvas-mcp" / ".env"
CREDENTIALS = Path.home() / ".config" / "canvas-calendar" / "credentials.json"


def load_canvas_credentials(env_path: Path | None = None) -> tuple[str, str]:
    """Return (base_url, token).

    Order: environment, then our own credentials file, then the legacy
    canvas-mcp dotenv. The legacy path stays last so the original install
    keeps working without being migrated.
    """
    import json

    token = os.environ.get("CANVAS_API_TOKEN") or ""
    url = os.environ.get("CANVAS_API_URL") or ""

    if not token and CREDENTIALS.exists():
        data = json.loads(CREDENTIALS.read_text())
        token = data.get("CANVAS_API_TOKEN", "") or ""
        url = url or data.get("CANVAS_API_URL", "") or ""

    if not token:
        values = dotenv_values(env_path or DEFAULT_ENV)
        token = values.get("CANVAS_API_TOKEN") or ""
        url = url or values.get("CANVAS_API_URL") or ""

    if not token:
        raise RuntimeError(
            "CANVAS_API_TOKEN not found. Run: canvas-calendar setup\n"
            "Illinois caps token lifetime near 30 days; regenerate at "
            "canvas.illinois.edu -> Account -> Settings."
        )
    url = (url or "https://canvas.illinois.edu").rstrip("/")
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url, token


GRAPH_CONFIG = Path.home() / ".config" / "canvas-calendar" / "config.json"


# Entra application (client) id, registered in the University of Illinois
# tenant. NOT a secret: this is a public client with no client secret, which
# is exactly why device-code auth is safe. Shipping it as a default is what
# lets a new UIUC user pick the Outlook backend without registering their own
# app -- each user consents individually and receives their own tokens.
DEFAULT_CLIENT_ID = "e2fcf5b3-2551-43c6-a844-8b2493c166b3"


def load_graph_client_id() -> str:
    """Entra application (client) id.

    Order: environment, then config.json, then the shipped default. Without
    the default, a fresh install choosing the Outlook backend dead-ends on a
    RuntimeError during setup.
    """
    import json

    env = os.environ.get("CANVAS_CALENDAR_CLIENT_ID")
    if env:
        return env
    if GRAPH_CONFIG.exists():
        cid = json.loads(GRAPH_CONFIG.read_text()).get("client_id")
        if cid:
            return cid
    return DEFAULT_CLIENT_ID


def load_term():
    """Term bounds from config, falling back to the shipped default."""
    import json

    from canvas_calendar.terms import term_from_config

    if GRAPH_CONFIG.exists():
        return term_from_config(json.loads(GRAPH_CONFIG.read_text()).get("term"))
    return term_from_config(None)


def load_sync_options() -> dict:
    """Exclusions and reminder timings from the local config file."""
    import json

    defaults = {
        "exclude_assignment_ids": [],
        "reminder_minutes_timed": 15,
        "reminder_minutes_all_day": 1440,
        "debrief_to": "",
        "debrief_hour": 7,
        "clear_completed": True,
        "assignments_calendar": "UIUC Assignments",
        "calendar_backend": None,
    }
    if GRAPH_CONFIG.exists():
        defaults.update(
            {k: v for k, v in json.loads(GRAPH_CONFIG.read_text()).items() if k in defaults}
        )
    return defaults
