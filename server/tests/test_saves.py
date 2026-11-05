import asyncio
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import ADMIN


async def _player(client: AsyncClient, env: dict[str, Any]) -> tuple[str, dict[str, str]]:
    r = await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": env["key"]["key"]})
    body = r.json()
    return body["player_id"], {"Authorization": f"Bearer {body['access_token']}"}


async def test_save_roundtrip_and_versions(client: AsyncClient, env: dict[str, Any]) -> None:
    _, h = await _player(client, env)
    assert (await client.get("/v1/saves/main", headers=h)).status_code == 404

    r = await client.put(
        "/v1/saves/main", json={"data": {"lvl": 1}, "expected_version": 0}, headers=h
    )
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 1 and r.headers["ETag"] == '"1"'

    # Create-only on existing slot conflicts.
    r = await client.put("/v1/saves/main", json={"data": {}, "expected_version": 0}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 1

    r = await client.put(
        "/v1/saves/main", json={"data": {"lvl": 2}, "expected_version": 1}, headers=h
    )
    assert r.json()["version"] == 2

    # Stale write.
    r = await client.put(
        "/v1/saves/main", json={"data": {"lvl": 9}, "expected_version": 1}, headers=h
    )
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 2

    # Unconditional overwrite.
    r = await client.put("/v1/saves/main", json={"data": {"lvl": 3}}, headers=h)
    assert r.json()["version"] == 3

    r = await client.get("/v1/saves/main", headers=h)
    assert r.json()["data"] == {"lvl": 3} and r.json()["version"] == 3

    # Expected version on a missing slot.
    r = await client.put("/v1/saves/other", json={"data": 1, "expected_version": 5}, headers=h)
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 0

    listed = (await client.get("/v1/saves", headers=h)).json()
    assert [s["slot"] for s in listed] == ["main"] and "data" not in listed[0]


async def test_delete(client: AsyncClient, env: dict[str, Any]) -> None:
    _, h = await _player(client, env)
    await client.put("/v1/saves/s", json={"data": [1, 2]}, headers=h)
    assert (await client.delete("/v1/saves/s?expected_version=7", headers=h)).status_code == 409
    assert (await client.delete("/v1/saves/s?expected_version=1", headers=h)).status_code == 204
    assert (await client.delete("/v1/saves/s", headers=h)).status_code == 404


async def test_saves_are_per_player(client: AsyncClient, env: dict[str, Any]) -> None:
    _, a = await _player(client, env)
    _, b = await _player(client, env)
    await client.put("/v1/saves/main", json={"data": "a"}, headers=a)
    assert (await client.get("/v1/saves/main", headers=b)).status_code == 404


async def test_limits(client: AsyncClient, env: dict[str, Any], app: FastAPI) -> None:
    _, h = await _player(client, env)
    app.state.settings.cloud_save_max_bytes = 100
    app.state.settings.cloud_save_max_slots = 2
    r = await client.put("/v1/saves/big", json={"data": "x" * 200}, headers=h)
    assert r.status_code == 413

    assert (await client.put("/v1/saves/a", json={"data": 1}, headers=h)).status_code == 200
    assert (await client.put("/v1/saves/b", json={"data": 1}, headers=h)).status_code == 200
    r = await client.put("/v1/saves/c", json={"data": 1}, headers=h)
    assert r.status_code == 409
    # Updating an existing slot is still allowed at the limit.
    assert (await client.put("/v1/saves/a", json={"data": 2}, headers=h)).status_code == 200

    assert (await client.put("/v1/saves/bad slot", json={"data": 1}, headers=h)).status_code == 422


async def test_concurrent_conditional_writes(client: AsyncClient, env: dict[str, Any]) -> None:
    _, h = await _player(client, env)
    await client.put("/v1/saves/main", json={"data": 0}, headers=h)
    results = await asyncio.gather(
        *[
            client.put("/v1/saves/main", json={"data": i, "expected_version": 1}, headers=h)
            for i in range(10)
        ]
    )
    codes = sorted(r.status_code for r in results)
    assert codes.count(200) == 1 and codes.count(409) == 9


async def test_concurrent_slot_limit(
    client: AsyncClient, env: dict[str, Any], app: FastAPI
) -> None:
    _, h = await _player(client, env)
    app.state.settings.cloud_save_max_slots = 3
    await asyncio.gather(
        *[client.put(f"/v1/saves/s{i}", json={"data": i}, headers=h) for i in range(10)]
    )
    assert len((await client.get("/v1/saves", headers=h)).json()) == 3


async def test_admin_support_endpoints(client: AsyncClient, env: dict[str, Any]) -> None:
    pid, h = await _player(client, env)
    await client.put("/v1/saves/main", json={"data": {"gold": 5}}, headers=h)
    base = f"/admin/environments/{env['env']['id']}/players/{pid}/saves"
    assert len((await client.get(base, headers=ADMIN)).json()) == 1
    assert (await client.get(f"{base}/main", headers=ADMIN)).json()["data"] == {"gold": 5}
    assert (await client.delete(f"{base}/main", headers=ADMIN)).status_code == 204
    assert (await client.get(f"{base}/main", headers=ADMIN)).status_code == 404

    prod = next(e for e in env["project"]["environments"] if e["name"] == "prod")
    r = await client.get(f"/admin/environments/{prod['id']}/players/{pid}/saves", headers=ADMIN)
    assert r.status_code == 404
