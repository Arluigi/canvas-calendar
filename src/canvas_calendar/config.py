"""Configuration. The Canvas token is shared with the canvas-mcp install."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

DEFAULT_ENV = Path.home() / "code" / "canvas-mcp" / ".env"


def load_canvas_credentials(env_path: Path | None = None) -> tuple[str, str]:
    """Return (base_url, token). Environment variables win over the file."""
    values = dotenv_values(env_path or DEFAULT_ENV)
    token = os.environ.get("CANVAS_API_TOKEN") or values.get("CANVAS_API_TOKEN") or ""
    url = os.environ.get("CANVAS_API_URL") or values.get("CANVAS_API_URL") or ""
    if not token:
        raise RuntimeError(
            "CANVAS_API_TOKEN not found. Illinois caps token lifetime near 30 days; "
            "regenerate at canvas.illinois.edu -> Account -> Settings."
        )
    url = url.rstrip("/")
    if not url.endswith("/api/v1"):
        url = f"{url}/api/v1"
    return url, token
