"""Microsoft Graph authentication via device code + refresh token.

The UIUC tenant permits student app registrations and public client flows, and
grants Calendars.ReadWrite by user consent -- verified live 2026-08-25. The
refresh token that flow returns is what makes unattended LaunchAgent runs
possible.

No msal dependency: the refresh-token grant is a single form POST, and owning
the cache ourselves keeps the on-disk format small enough to audit.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

TENANT_ID = "44467e6f-462c-4ea2-823f-7800de5434e3"  # University of Illinois - Urbana
SCOPES = "https://graph.microsoft.com/Calendars.ReadWrite offline_access openid profile"
DEFAULT_TOKEN_PATH = Path.home() / ".config" / "canvas-calendar" / "token.json"

# Refresh a little early so a long sync cannot straddle an expiry.
_EXPIRY_MARGIN = 300


class AuthError(RuntimeError):
    """Authentication failed in a way the operator must act on."""


class TokenStore:
    """Persists the refresh token, and nothing else, at mode 0600."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path or DEFAULT_TOKEN_PATH)

    def __repr__(self) -> str:  # never leak the credential into logs
        return f"TokenStore(path={self._path})"

    def save(self, refresh_token: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Create with restrictive mode from the outset rather than chmod-ing
        # after writing, which would leave a window where it is world-readable.
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"refresh_token": refresh_token}, fh)
        os.chmod(self._path, 0o600)

    def load(self) -> str | None:
        if not self._path.exists():
            return None
        try:
            return json.loads(self._path.read_text()).get("refresh_token")
        except (json.JSONDecodeError, OSError):
            return None


class GraphAuth:
    def __init__(
        self,
        client_id: str,
        tenant_id: str = TENANT_ID,
        store: TokenStore | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self._client_id = client_id
        self._tenant = tenant_id
        self._store = store or TokenStore()
        self._http = http or httpx.Client(timeout=30)
        self._access: str | None = None
        self._expires_at: float = 0.0

    def __repr__(self) -> str:
        return f"GraphAuth(client_id={self._client_id}, tenant={self._tenant})"

    @property
    def _token_url(self) -> str:
        return f"https://login.microsoftonline.com/{self._tenant}/oauth2/v2.0/token"

    def access_token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        if self._access and time.time() < self._expires_at - _EXPIRY_MARGIN:
            return self._access

        refresh = self._store.load()
        if not refresh:
            raise AuthError(
                "no stored refresh token -- run `canvas-calendar login` to authenticate"
            )

        r = self._http.post(
            self._token_url,
            data={
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": refresh,
                "scope": SCOPES,
            },
        )
        payload = r.json()
        if r.status_code != 200 or "access_token" not in payload:
            raise AuthError(
                f"refresh failed ({payload.get('error', r.status_code)}): "
                f"{payload.get('error_description', '')[:200]} -- "
                "re-run `canvas-calendar login`"
            )

        # Entra rotates refresh tokens. Dropping the new one strands the daemon
        # at the next expiry, which surfaces as a silent failure days later.
        if payload.get("refresh_token"):
            self._store.save(payload["refresh_token"])

        self._access = payload["access_token"]
        self._expires_at = time.time() + float(payload.get("expires_in", 3600))
        return self._access
