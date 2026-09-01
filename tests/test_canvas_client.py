from typing import ClassVar

import httpx
import pytest

from canvas_calendar.canvas.client import CanvasClient, TokenExpired


def _client(handler, token="tok"):
    return CanvasClient(
        base_url="https://canvas.illinois.edu/api/v1",
        token=token,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_sends_bearer_token():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=[])

    _client(handler).list_assignments(1)
    assert seen["auth"] == "Bearer tok"


def test_expired_token_raises_dedicated_error():
    """A 401 must be loud and specific -- never a silent empty result.
    This is the failure mode that silently broke the previous setup for weeks."""

    def handler(request):
        return httpx.Response(401, json={"errors": [{"message": "Expired access token"}]})

    with pytest.raises(TokenExpired, match="Expired"):
        _client(handler).list_assignments(1)


def test_401_never_returns_empty_list():
    """Explicitly guards against degrading a 401 into 'no assignments'."""

    def handler(request):
        return httpx.Response(401, json={"errors": [{"message": "Invalid access token"}]})

    with pytest.raises(TokenExpired):
        _client(handler).list_courses()


def test_follows_link_header_pagination():
    calls = {"n": 0}
    pages = {
        1: httpx.Response(
            200,
            json=[{"id": 1}],
            headers={"Link": '<https://canvas.illinois.edu/api/v1/c?page=2>; rel="next"'},
        ),
        2: httpx.Response(200, json=[{"id": 2}]),
    }

    def handler(request):
        calls["n"] += 1
        return pages[calls["n"]]

    out = _client(handler).list_assignments(1)
    assert [a["id"] for a in out] == [1, 2]


def test_stops_when_no_next_link():
    def handler(request):
        return httpx.Response(200, json=[{"id": 1}], headers={"Link": '<https://x>; rel="prev"'})

    assert len(_client(handler).list_assignments(1)) == 1


def test_module_items_path():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json=[])

    _client(handler).list_module_items(73446, 679183)
    assert seen["path"].endswith("/courses/73446/modules/679183/items")


def test_list_assignments_requests_submission():
    """Without include[]=submission every assignment looks unsubmitted."""
    seen = {}

    class FakeResp:
        status_code = 200
        headers: ClassVar[dict] = {}

        def json(self):
            return []

        def raise_for_status(self):
            return None

    class FakeHTTP:
        def get(self, url, headers=None, params=None):
            seen.update(params or {})
            return FakeResp()

    CanvasClient("https://x/api/v1", "tok", http=FakeHTTP()).list_assignments(1)
    assert seen.get("include[]") == "submission"
