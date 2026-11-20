"""A saved sign-in is renewed when its token has run out, and its renewal token goes nowhere but the service's own site."""
from __future__ import annotations

import asyncio
import json

import httpx

from odysseus.attio import FileTokens, _provider, _same_site, renew_if_due

URL = "https://mcp.vendor.example/mcp"


def saved(tmp_path, age_s, lifetime=7200, issuer="https://vendor.example", **tokens):
    path = tmp_path / "vendor.json"
    path.write_text(json.dumps({"client": {"client_id": "client-1", "issuer": issuer, "redirect_uris": ["http://127.0.0.1:8765/callback"]},
                                "tokens": {"access_token": "old-access", "token_type": "Bearer", "expires_in": lifetime, "refresh_token": "old-renewal", **tokens},
                                "saved_at": 1_000_000.0 - age_s}), encoding="utf-8")
    return FileTokens(path)


def service(token_endpoint="https://vendor.example/oauth/token", status=200, body=None):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.content.decode()))
        if request.url.path.startswith("/.well-known/oauth-authorization-server"):
            return httpx.Response(200, json={"token_endpoint": token_endpoint})
        if request.method == "POST":
            return httpx.Response(status, json=body if body is not None else {"access_token": "new-access", "token_type": "Bearer", "expires_in": 7200, "refresh_token": "new-renewal"})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_a_token_that_has_run_out_is_renewed_at_the_address_the_service_publishes(tmp_path):
    tokens = saved(tmp_path, age_s=3 * 3600)
    assert tokens.expires_at() == 1_000_000.0 - 3 * 3600 + 7200 - 60
    client, seen = service()
    assert await renew_if_due(tokens, URL, client, now=lambda: 1_000_000.0) == "renewed"
    data = json.loads(tokens.path.read_text(encoding="utf-8"))
    assert data["tokens"]["access_token"] == "new-access" and data["tokens"]["refresh_token"] == "new-renewal" and data["saved_at"] == 1_000_000.0
    assert data["token_endpoint"] == "https://vendor.example/oauth/token", "kept, so the next renewal does not have to ask where it is"
    method, where, form = seen[-1]
    assert (method, where) == ("POST", "https://vendor.example/oauth/token")
    assert "grant_type=refresh_token" in form and "refresh_token=old-renewal" in form and "client_id=client-1" in form
    assert await renew_if_due(tokens, URL, client, now=lambda: 1_000_000.0) == "fresh" and len(seen) == 2, "not renewed twice"


async def test_a_token_still_good_is_left_alone_and_one_with_no_renewal_token_cannot_be_renewed(tmp_path):
    client, seen = service()
    assert await renew_if_due(saved(tmp_path, age_s=600), URL, client, now=lambda: 1_000_000.0) == "fresh" and seen == []
    (tmp_path / "b").mkdir()
    lone = saved(tmp_path / "b", age_s=3 * 3600)
    data = json.loads(lone.path.read_text(encoding="utf-8"))
    del data["tokens"]["refresh_token"]
    lone.path.write_text(json.dumps(data), encoding="utf-8")
    assert await renew_if_due(lone, URL, client, now=lambda: 1_000_000.0) == "no renewal token" and seen == []
    (tmp_path / "c").mkdir()
    forever = saved(tmp_path / "c", age_s=10 ** 6, lifetime=None)
    assert forever.expires_at() is None and await renew_if_due(forever, URL, client, now=lambda: 1_000_000.0) == "fresh"


async def test_the_renewal_token_is_never_sent_off_the_services_own_site(tmp_path):
    for bad in ("https://collector.evil.example/oauth/token", "http://vendor.example/oauth/token", "https://vendor.example.evil.example/token"):
        folder = tmp_path / str(abs(hash(bad)))
        folder.mkdir()
        tokens = saved(folder, age_s=3 * 3600)
        client, seen = service(token_endpoint=bad)
        assert (await renew_if_due(tokens, URL, client, now=lambda: 1_000_000.0)).startswith("failed: no token address"), bad
        assert all(m == "GET" for m, _, _ in seen), "nothing was posted anywhere"
        assert "old-renewal" not in str(seen) and json.loads(tokens.path.read_text(encoding="utf-8"))["tokens"]["access_token"] == "old-access"
    assert _same_site("mcp.attio.com", "app.attio.com") and _same_site("docsend.com", "docsend.com")
    assert not _same_site("attio.com.evil.example", "attio.com") and not _same_site("", "attio.com")


async def test_a_refused_renewal_leaves_the_sign_in_as_it_was(tmp_path):
    tokens = saved(tmp_path, age_s=3 * 3600)
    client, _ = service(status=400, body={"error": "invalid_grant"})
    assert await renew_if_due(tokens, URL, client, now=lambda: 1_000_000.0) == "failed: the service answered 400"
    assert json.loads(tokens.path.read_text(encoding="utf-8"))["tokens"]["access_token"] == "old-access"


async def test_two_sessions_opening_at_once_renew_once(tmp_path):
    tokens = saved(tmp_path, age_s=3 * 3600)
    client, seen = service()
    out = await asyncio.gather(*(renew_if_due(tokens, URL, client, now=lambda: 1_000_000.0) for _ in range(3)))
    assert sorted(out) == ["fresh", "fresh", "renewed"] and sum(1 for m, _, _ in seen if m == "POST") == 1, "a renewal token is good for one use"


async def test_the_library_is_told_when_the_saved_token_runs_out_and_a_new_sign_in_is_dated(tmp_path):
    tokens = saved(tmp_path, age_s=600)
    provider = _provider(tokens, None, URL, "read", None)
    assert provider.context.token_expiry_time == tokens.expires_at(), "after a restart it would otherwise take the token to be good for ever"
    from mcp.shared.auth import OAuthToken
    import time
    await tokens.set_tokens(OAuthToken(access_token="a", token_type="Bearer", expires_in=3600, refresh_token="r"))
    assert abs(json.loads(tokens.path.read_text(encoding="utf-8"))["saved_at"] - time.time()) < 5
