"""Device-code login. Run once, and again after any scope change.

The refresh token it stores is what makes every later run unattended, so this
should be the only interactive step in the whole system.
"""

from __future__ import annotations

import time

import httpx

from canvas_calendar.calendars.graph_auth import SCOPES, TENANT_ID, TokenStore
from canvas_calendar.config import load_graph_client_id

AUTH = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0"


def device_login() -> int:
    client_id = load_graph_client_id()
    r = httpx.post(
        f"{AUTH}/devicecode", data={"client_id": client_id, "scope": SCOPES}, timeout=30
    )
    r.raise_for_status()
    d = r.json()

    print(f"\n  Go to: {d['verification_uri']}")
    print(f"  Code:  {d['user_code']}\n")
    print("  Waiting for sign-in...", flush=True)

    deadline = time.time() + int(d.get("expires_in", 900))
    interval = int(d.get("interval", 5))
    while time.time() < deadline:
        time.sleep(interval)
        t = httpx.post(
            f"{AUTH}/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": d["device_code"],
            },
            timeout=30,
        ).json()
        if t.get("error") == "authorization_pending":
            continue
        if "refresh_token" in t:
            # Persist before printing anything: losing this to a crash means
            # redoing the whole interactive flow.
            TokenStore().save(t["refresh_token"])
            print("  signed in; refresh token stored (0600)")
            print(f"  granted scopes: {t.get('scope', '')}")
            return 0
        print(f"  failed: {t.get('error')}: {t.get('error_description', '')[:220]}")
        return 1

    print("  timed out waiting for sign-in")
    return 1
