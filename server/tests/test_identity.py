import json
import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from httpx import AsyncClient
from jwt.algorithms import RSAAlgorithm

from app.services.identity import (
    IdentityError,
    JwksCache,
    OidcVerifier,
    ProviderNotConfigured,
    SteamVerifier,
    VerifiedIdentity,
)
from tests.conftest import ADMIN

# ---------- verifier unit tests ----------

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_JWK = {**json.loads(RSAAlgorithm.to_jwk(_KEY.public_key())), "kid": "k1", "alg": "RS256"}


def _jwks_client(counter: list[int]) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        counter.append(1)
        return httpx.Response(200, json={"keys": [_JWK]})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _id_token(**over: Any) -> str:
    claims = {
        "iss": "https://accounts.google.com",
        "sub": "g-123",
        "aud": "client-1",
        "exp": int(time.time()) + 600,
        "email": "p@example.com",
        "email_verified": True,
        **over,
    }
    return jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": over.pop("kid", "k1")})


def _google(http: httpx.AsyncClient) -> OidcVerifier:
    return OidcVerifier(
        "google", JwksCache(http, "https://x/jwks"), {"https://accounts.google.com"}, "client_ids"
    )


async def test_oidc_valid_and_cached() -> None:
    calls: list[int] = []
    v = _google(_jwks_client(calls))
    ident = await v.verify(_id_token(), {"client_ids": ["client-1"]})
    assert ident == VerifiedIdentity("google", "g-123", "p@example.com")
    await v.verify(_id_token(), {"client_ids": ["client-1"]})
    assert len(calls) == 1


@pytest.mark.parametrize(
    "over",
    [
        {"aud": "other-client"},
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 3600},
    ],
)
async def test_oidc_rejects(over: dict[str, Any]) -> None:
    v = _google(_jwks_client([]))
    with pytest.raises(IdentityError):
        await v.verify(_id_token(**over), {"client_ids": ["client-1"]})


async def test_oidc_rejects_foreign_key_and_unknown_kid() -> None:
    v = _google(_jwks_client([]))
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": "https://accounts.google.com", "sub": "x", "aud": "client-1", "exp": 2**31},
        other,
        algorithm="RS256",
        headers={"kid": "k1"},
    )
    with pytest.raises(IdentityError):
        await v.verify(forged, {"client_ids": ["client-1"]})
    unknown = jwt.encode({"sub": "x"}, _KEY, algorithm="RS256", headers={"kid": "nope"})
    with pytest.raises(IdentityError):
        await v.verify(unknown, {"client_ids": ["client-1"]})
    with pytest.raises(IdentityError):
        await v.verify("not-a-jwt", {"client_ids": ["client-1"]})


async def test_unverified_email_is_dropped() -> None:
    v = _google(_jwks_client([]))
    ident = await v.verify(_id_token(email_verified=False), {"client_ids": ["client-1"]})
    assert ident.email is None


async def test_not_configured() -> None:
    with pytest.raises(ProviderNotConfigured):
        await _google(_jwks_client([])).verify(_id_token(), {})


async def test_steam() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        ok = req.url.params["ticket"] == "good"
        params = {"result": "OK", "steamid": "7656"} if ok else {"result": "FAIL"}
        return httpx.Response(200, json={"response": {"params": params}})

    v = SteamVerifier(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    cfg = {"app_id": 480, "web_api_key": "k"}
    assert (await v.verify("good", cfg)).subject == "7656"
    assert seen[0].url.params["appid"] == "480"
    with pytest.raises(IdentityError):
        await v.verify("bad", cfg)
    with pytest.raises(ProviderNotConfigured):
        await v.verify("good", {})


# ---------- API tests with a fake verifier ----------


class FakeVerifier:
    async def verify(self, token: str, config: dict[str, Any]) -> VerifiedIdentity:
        if not config.get("client_ids"):
            raise ProviderNotConfigured("google")
        if not token.startswith("ok:"):
            raise IdentityError("bad")
        return VerifiedIdentity("google", token[3:], f"{token[3:]}@example.com")


@pytest.fixture
async def google(app: FastAPI, client: AsyncClient, env: dict[str, Any]) -> dict[str, Any]:
    app.state.identity_verifiers = {**app.state.identity_verifiers, "google": FakeVerifier()}
    r = await client.put(
        f"/admin/environments/{env['env']['id']}/auth-providers",
        json={
            "google": {"client_ids": ["c1"]},
            "steam": {"app_id": 480, "web_api_key": "secret-1234"},
        },
        headers=ADMIN,
    )
    assert r.status_code == 200, r.text
    return env


def _key(env: dict[str, Any]) -> dict[str, str]:
    return {"X-Api-Key": env["key"]["key"]}


async def _bearer(
    client: AsyncClient, env: dict[str, Any], body: dict[str, Any] | None = None
) -> tuple[str, dict[str, str]]:
    r = (await client.post("/v1/auth/anonymous", json=body or {}, headers=_key(env))).json()
    return r["player_id"], {"Authorization": f"Bearer {r['access_token']}"}


async def test_provider_config_is_masked(client: AsyncClient, google: dict[str, Any]) -> None:
    url = f"/admin/environments/{google['env']['id']}/auth-providers"
    cfg = (await client.get(url, headers=ADMIN)).json()
    assert cfg["steam"]["web_api_key"] == "••••1234"
    # Re-sending the masked value keeps the secret.
    await client.put(url, json=cfg, headers=ADMIN)
    env = google["env"]["id"]
    async with client._transport.app.state.sessionmaker() as db:  # type: ignore[attr-defined]
        from app.models import Environment

        row = await db.get(Environment, env)
        assert row.auth_providers["steam"]["web_api_key"] == "secret-1234"


async def test_sign_in_creates_then_resumes(client: AsyncClient, google: dict[str, Any]) -> None:
    a = (await client.post("/v1/auth/google", json={"token": "ok:u1"}, headers=_key(google))).json()
    b = (await client.post("/v1/auth/google", json={"token": "ok:u1"}, headers=_key(google))).json()
    assert a["created"] and not b["created"] and a["player_id"] == b["player_id"]
    r = await client.post("/v1/auth/google", json={"token": "bad"}, headers=_key(google))
    assert r.status_code == 401
    r = await client.post("/v1/auth/apple", json={"token": "x"}, headers=_key(google))
    assert r.status_code == 400  # not configured
    r = await client.post("/v1/auth/facebook", json={"token": "x"}, headers=_key(google))
    assert r.status_code == 422


async def test_link_flow_moves_progress_across_devices(
    client: AsyncClient, google: dict[str, Any]
) -> None:
    pid, h = await _bearer(client, google, {"device_id": "phone-0001"})
    await client.put("/v1/saves/main", json={"data": {"lvl": 7}}, headers=h)
    r = await client.post("/v1/players/me/identities/google", json={"token": "ok:u2"}, headers=h)
    assert r.status_code == 200
    assert r.json()["identities"][0]["email"] == "u2@example.com"

    # New device signs in with Google and gets the same player + save.
    r = (await client.post("/v1/auth/google", json={"token": "ok:u2"}, headers=_key(google))).json()
    assert r["player_id"] == pid and not r["created"]
    h2 = {"Authorization": f"Bearer {r['access_token']}"}
    assert (await client.get("/v1/saves/main", headers=h2)).json()["data"] == {"lvl": 7}

    # Idempotent relink; a second google account for the same player conflicts.
    assert (
        await client.post("/v1/players/me/identities/google", json={"token": "ok:u2"}, headers=h)
    ).status_code == 200
    r = await client.post("/v1/players/me/identities/google", json={"token": "ok:u3"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "provider_linked"

    # Another player can't take an account that's already linked.
    _, other = await _bearer(client, google)
    r = await client.post(
        "/v1/players/me/identities/google", json={"token": "ok:u2"}, headers=other
    )
    assert r.status_code == 409 and r.json()["detail"]["code"] == "identity_in_use"

    r = await client.delete("/v1/players/me/identities/google", headers=h)
    assert r.json()["identities"] == []
    assert (await client.delete("/v1/players/me/identities/google", headers=h)).status_code == 404


async def test_display_name(client: AsyncClient, env: dict[str, Any]) -> None:
    pid, h = await _bearer(client, env)
    r = await client.patch("/v1/players/me", json={"display_name": "Neo  the  One"}, headers=h)
    assert r.status_code == 200 and r.json()["display_name"] == "Neo the One"
    for bad in ["ab", "x" * 30, "<script>", " lead"]:
        r = await client.patch("/v1/players/me", json={"display_name": bad}, headers=h)
        assert r.status_code == 422, bad
    assert (
        await client.patch("/v1/players/me", json={"display_name": "Ünïcødé_1"}, headers=h)
    ).status_code == 200

    await client.post(
        f"/admin/environments/{env['env']['id']}/leaderboards",
        json={"key": "main", "name": "Main"},
        headers=ADMIN,
    )
    await client.post("/v1/leaderboards/main/scores", json={"score": 5}, headers=h)
    top = (await client.get("/v1/leaderboards/main", headers=h)).json()
    assert top["entries"][0]["display_name"] == "Ünïcødé_1"
    me = (await client.get("/v1/leaderboards/main/me", headers=h)).json()
    assert me["me"]["display_name"] == "Ünïcødé_1"

    r = await client.patch("/v1/players/me", json={"display_name": None}, headers=h)
    assert r.json()["display_name"] is None
    assert (await client.get("/v1/players/me", headers=h)).json()["id"] == pid
