"""Thin Canvas REST client.

Deliberately does not import the canvas_mcp package: that repo moved 432
commits in six months and this project must not break on an upstream rename.
"""

from __future__ import annotations

import re

import httpx


class TokenExpired(RuntimeError):
    """Raised on 401. Must never be swallowed into an empty result -- a silent
    401 is exactly how the previous setup broke unnoticed for weeks."""


_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')


class CanvasClient:
    def __init__(self, base_url: str, token: str, http: httpx.Client | None = None):
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http or httpx.Client(timeout=30)

    def _get_all(self, path: str, **params) -> list[dict]:
        url = f"{self._base}{path}"
        query = {"per_page": 100, **params}
        out: list[dict] = []
        while url:
            r = self._http.get(url, headers=self._headers, params=query)
            if r.status_code == 401:
                raise TokenExpired(r.text)
            r.raise_for_status()
            out.extend(r.json())
            m = _NEXT.search(r.headers.get("Link", ""))
            url, query = (m.group(1) if m else None), {}
        return out

    def list_courses(self) -> list[dict]:
        return self._get_all("/courses", enrollment_state="active")

    def list_assignments(self, course_id: int) -> list[dict]:
        """`include[]=submission` attaches the current user's submission to
        each assignment. Same endpoint, same pagination, no extra calls --
        and without it every assignment looks unsubmitted."""
        return self._get_all(f"/courses/{course_id}/assignments", **{"include[]": "submission"})

    def list_tokens(self) -> list[dict]:
        """The user's own access tokens, with expiry.

        Illinois blocks creating and regenerating tokens through the API (both
        403), so this read-only view is the only programmatic handle we have on
        expiry. It is enough to guarantee the user is never surprised.
        """
        return self._get_all("/users/self/user_generated_tokens")

    def list_modules(self, course_id: int) -> list[dict]:
        return self._get_all(f"/courses/{course_id}/modules")

    def list_module_items(self, course_id: int, module_id: int) -> list[dict]:
        return self._get_all(f"/courses/{course_id}/modules/{module_id}/items")
