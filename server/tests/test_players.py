from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.core.config import Settings
from app.core.security import PlayerClaims, issue_player_token


def _key(env: dict[str, Any]) -> dict[str, str]:
    return {"X-Api-Key": env["key"]["key"]}


async def test_login_requires_api_key(client: AsyncClient) -> None:
    assert (await client.post("/v1/auth/anonymous", json={})).status_code == 401
    r = await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": "lo_client_x"})
    assert r.status_code == 401


async def test_anonymous_login_without_device(client: AsyncClient, env: dict[str, Any]) -> None:
    a = (await client.post("/v1/auth/anonymous", json={}, headers=_key(env))).json()
    b = (await client.post("/v1/auth/anonymous", json={}, headers=_key(env))).json()
    assert a["created"] and b["created"]
    assert a["player_id"] != b["player_id"]


async def test_device_login_is_idempotent(client: AsyncClient, env: dict[str, Any]) -> None:
    body = {"device_id": "device-12345678"}
    a = (await client.post("/v1/auth/anonymous", json=body, headers=_key(env))).json()
    b = (await client.post("/v1/auth/anonymous", json=body, headers=_key(env))).json()
    assert a["created"] is True and b["created"] is False
    assert a["player_id"] == b["player_id"]

    r = await client.get("/v1/players/me", headers={"Authorization": f"Bearer {b['access_token']}"})
    assert r.status_code == 200
    assert r.json()["id"] == a["player_id"]
    assert r.json()["environment_id"] == env["env"]["id"]


async def test_me_rejects_bad_tokens(
    client: AsyncClient, env: dict[str, Any], settings: Settings
) -> None:
    assert (await client.get("/v1/players/me")).status_code == 401
    r = await client.get("/v1/players/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401

    login = (await client.post("/v1/auth/anonymous", json={}, headers=_key(env))).json()
    forged, _ = issue_player_token(
        PlayerClaims(login["player_id"], env["env"]["id"]), "other-secret-" + "y" * 32, 60
    )
    r = await client.get("/v1/players/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401

    expired, _ = issue_player_token(
        PlayerClaims(login["player_id"], env["env"]["id"]),
        settings.jwt_secret.get_secret_value(),
        -10,
    )
    r = await client.get("/v1/players/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


async def test_rate_limit(client: AsyncClient, env: dict[str, Any], app: FastAPI) -> None:
    limit = 5
    app.state.settings.rate_limit_requests = limit
    for i in range(limit):
        r = await client.post("/v1/auth/anonymous", json={}, headers=_key(env))
        assert r.status_code == 200
        assert r.headers["X-RateLimit-Remaining"] == str(limit - i - 1)
    r = await client.post("/v1/auth/anonymous", json={}, headers=_key(env))
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_prod_requires_secrets() -> None:
    from app.main import create_app

    with pytest.raises(RuntimeError, match="ADMIN_TOKEN"):
        create_app(Settings(env="prod"))
