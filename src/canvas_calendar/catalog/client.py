"""HTTP client for the UIUC Course Explorer API."""

from __future__ import annotations

import httpx

from canvas_calendar.models import CourseRef

BASE = "https://courses.illinois.edu/cisapp/explorer/schedule"

# Required. The API returns 403 to some default user agents -- observed live
# on 2026-08-25, where a generic fetcher got 403 and curl with a UA got 200.
USER_AGENT = "canvas-calendar/0.1 (personal course schedule sync)"

# Canvas course codes embed the Course Explorer term id. Extend as terms roll.
TERM_SLUGS: dict[str, tuple[int, str]] = {
    "120268": (2026, "fall"),
}


class CatalogClient:
    def __init__(self, http: httpx.Client | None = None) -> None:
        self._http = http or httpx.Client(timeout=30)

    def url_for(self, ref: CourseRef) -> str:
        year, season = TERM_SLUGS[ref.term_id]
        return f"{BASE}/{year}/{season}/{ref.subject}/{ref.number}.xml"

    def _get(self, url: str) -> str:
        r = self._http.get(url, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        return r.text

    def fetch_course(self, ref: CourseRef) -> str:
        return self._get(self.url_for(ref))

    def fetch_section(self, ref: CourseRef, section_id: str) -> str:
        year, season = TERM_SLUGS[ref.term_id]
        return self._get(f"{BASE}/{year}/{season}/{ref.subject}/{ref.number}/{section_id}.xml")
