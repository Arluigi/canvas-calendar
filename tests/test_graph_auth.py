import json
import stat

import httpx
import pytest

from canvas_calendar.calendars.graph_auth import (
    AuthError,
    GraphAuth,
    TokenStore,
)


def test_token_store_roundtrip(tmp_path):
    s = TokenStore(tmp_path / "tok.json")
    s.save("refresh-abc")
    assert s.load() == "refresh-abc"


def test_token_file_is_owner_only(tmp_path):
    """A refresh token is a live credential; it must not be world-readable."""
    p = tmp_path / "tok.json"
    TokenStore(p).save("refresh-abc")
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_missing_token_file_returns_none(tmp_path):
    assert TokenStore(tmp_path / "nope.json").load() is None


def test_exchanges_refresh_token_for_access_token(tmp_path):
    store = TokenStore(tmp_path / "tok.json")
    store.save("refresh-old")

    def handler(request):
        body = request.content.decode()
        assert "grant_type=refresh_token" in body
        assert "refresh-old" in body
        return httpx.Response(
            200, json={"access_token": "at-1", "refresh_token": "refresh-new", "expires_in": 3600}
        )

    auth = GraphAuth(
        client_id="cid",
        tenant_id="tid",
        store=store,
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert auth.access_token() == "at-1"


def test_rotated_refresh_token_is_persisted(tmp_path):
    """Entra rotates refresh tokens. Dropping the new one strands the daemon
    at the next expiry, which is a silent failure days later."""
    store = TokenStore(tmp_path / "tok.json")
    store.save("refresh-old")

    def handler(request):
        return httpx.Response(
            200, json={"access_token": "at-1", "refresh_token": "refresh-new", "expires_in": 3600}
        )

    GraphAuth("cid", "tid", store, http=httpx.Client(transport=httpx.MockTransport(handler)))\
        .access_token()
    assert store.load() == "refresh-new"


def test_access_token_is_cached_in_memory(tmp_path):
    store = TokenStore(tmp_path / "tok.json")
    store.save("r")
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            200, json={"access_token": "at", "refresh_token": "r", "expires_in": 3600}
        )

    auth = GraphAuth("cid", "tid", store, http=httpx.Client(transport=httpx.MockTransport(handler)))
    auth.access_token()
    auth.access_token()
    assert calls["n"] == 1


def test_invalid_grant_raises_actionable_error(tmp_path):
    """A revoked or expired refresh token must say what to do, not just fail."""
    store = TokenStore(tmp_path / "tok.json")
    store.save("dead")

    def handler(request):
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "AADSTS70008: expired"},
        )

    auth = GraphAuth("cid", "tid", store, http=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(AuthError, match="re-run"):
        auth.access_token()


def test_no_stored_token_raises_actionable_error(tmp_path):
    auth = GraphAuth("cid", "tid", TokenStore(tmp_path / "nope.json"))
    with pytest.raises(AuthError, match="login"):
        auth.access_token()


def test_store_does_not_leak_token_in_repr(tmp_path):
    store = TokenStore(tmp_path / "tok.json")
    store.save("super-secret-value")
    assert "super-secret" not in repr(store)
    auth = GraphAuth("cid", "tid", store)
    assert "super-secret" not in repr(auth)


def test_token_file_contains_only_expected_keys(tmp_path):
    p = tmp_path / "tok.json"
    TokenStore(p).save("r")
    assert set(json.loads(p.read_text())) == {"refresh_token"}
