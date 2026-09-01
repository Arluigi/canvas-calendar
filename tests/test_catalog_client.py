import httpx
import pytest

from canvas_calendar.catalog.client import TERM_SLUGS, CatalogClient
from canvas_calendar.models import CourseRef


def _client(handler):
    transport = httpx.MockTransport(handler)
    return CatalogClient(http=httpx.Client(transport=transport))


def test_sends_explicit_user_agent():
    """Guards a real 403: the API rejects some default agents. Observed
    2026-08-25 -- WebFetch got 403 where curl with a UA succeeded."""
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text="<course/>")

    _client(handler).fetch_course(CourseRef("MCB", "244", "120268"))
    assert "canvas-calendar" in seen["ua"]


def test_builds_correct_url_from_term_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text="<course/>")

    _client(handler).fetch_course(CourseRef("MCB", "244", "120268"))
    assert seen["url"].endswith("/schedule/2026/fall/MCB/244.xml")


def test_section_url_includes_section_id():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text="<section/>")

    _client(handler).fetch_section(CourseRef("MCB", "244", "120268"), "56301")
    assert seen["url"].endswith("/schedule/2026/fall/MCB/244/56301.xml")


def test_unknown_term_id_raises():
    with pytest.raises(KeyError):
        CatalogClient().url_for(CourseRef("MCB", "244", "999999"))


def test_known_term_maps_to_year_and_season():
    assert TERM_SLUGS["120268"] == (2026, "fall")


def test_http_error_propagates():
    def handler(request):
        return httpx.Response(404, text="nope")

    with pytest.raises(httpx.HTTPStatusError):
        _client(handler).fetch_course(CourseRef("MCB", "999", "120268"))
