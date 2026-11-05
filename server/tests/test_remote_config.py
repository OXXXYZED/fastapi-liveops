from typing import Any

from httpx import AsyncClient

from tests.conftest import ADMIN


async def _player_headers(client: AsyncClient, env: dict[str, Any]) -> dict[str, str]:
    r = await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": env["key"]["key"]})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _base(env: dict[str, Any]) -> str:
    return f"/admin/environments/{env['env']['id']}"


async def test_publish_versions_and_rollback(client: AsyncClient, env: dict[str, Any]) -> None:
    base = _base(env)
    assert (await client.get(f"{base}/config", headers=ADMIN)).status_code == 404

    r = await client.post(f"{base}/config", json={"values": {"lives": 3}}, headers=ADMIN)
    assert r.status_code == 201 and r.json()["version"] == 1
    r = await client.post(
        f"{base}/config", json={"values": {"lives": 5}, "base_version": 1}, headers=ADMIN
    )
    assert r.json()["version"] == 2

    # Stale base_version -> conflict.
    r = await client.post(
        f"{base}/config", json={"values": {"lives": 9}, "base_version": 1}, headers=ADMIN
    )
    assert r.status_code == 409

    r = await client.post(f"{base}/config/versions/1/rollback", headers=ADMIN)
    assert r.json()["version"] == 3 and r.json()["values"] == {"lives": 3}

    versions = (await client.get(f"{base}/config/versions", headers=ADMIN)).json()
    assert [v["version"] for v in versions] == [3, 2, 1]
    assert (await client.get(f"{base}/config/versions/9", headers=ADMIN)).status_code == 404


async def test_client_config_and_etag(client: AsyncClient, env: dict[str, Any]) -> None:
    base = _base(env)
    headers = await _player_headers(client, env)

    r = await client.get("/v1/config", headers=headers)
    assert r.json() == {"version": 0, "values": {}, "flags": {}, "experiments": {}}

    await client.post(f"{base}/config", json={"values": {"lives": 3}}, headers=ADMIN)
    r = await client.get("/v1/config", headers=headers)  # cache must be invalidated
    assert r.json()["values"] == {"lives": 3}
    etag = r.headers["ETag"]

    r = await client.get("/v1/config", headers={**headers, "If-None-Match": etag})
    assert r.status_code == 304

    await client.post(f"{base}/config", json={"values": {"lives": 4}}, headers=ADMIN)
    r = await client.get("/v1/config", headers={**headers, "If-None-Match": etag})
    assert r.status_code == 200 and r.json()["version"] == 2


async def test_flags_targeting(client: AsyncClient, env: dict[str, Any]) -> None:
    base = _base(env)
    headers = await _player_headers(client, env)
    flag = {
        "enabled": True,
        "rules": [{"attribute": "platform", "op": "eq", "value": "ios"}],
    }
    r = await client.put(f"{base}/flags/new_shop", json=flag, headers=ADMIN)
    assert r.status_code == 200, r.text
    await client.put(f"{base}/flags/off_flag", json={"enabled": False}, headers=ADMIN)

    r = await client.get("/v1/config?platform=ios", headers=headers)
    assert r.json()["flags"] == {"new_shop": True, "off_flag": False}
    r = await client.get("/v1/config?platform=android", headers=headers)
    assert r.json()["flags"]["new_shop"] is False

    # Update flag -> visible immediately.
    r = await client.put(
        f"{base}/flags/new_shop", json={**flag, "rollout_percent": 0}, headers=ADMIN
    )
    assert r.json()["rollout_percent"] == 0
    r = await client.get("/v1/config?platform=ios", headers=headers)
    assert r.json()["flags"]["new_shop"] is False

    assert len((await client.get(f"{base}/flags", headers=ADMIN)).json()) == 2
    assert (await client.delete(f"{base}/flags/new_shop", headers=ADMIN)).status_code == 204
    assert (await client.delete(f"{base}/flags/new_shop", headers=ADMIN)).status_code == 404
    r = await client.get("/v1/config", headers=headers)
    assert "new_shop" not in r.json()["flags"]


async def test_flag_validation(client: AsyncClient, env: dict[str, Any]) -> None:
    base = _base(env)
    r = await client.put(f"{base}/flags/Bad Key", json={}, headers=ADMIN)
    assert r.status_code == 422
    r = await client.put(f"{base}/flags/ok", json={"rollout_percent": 101}, headers=ADMIN)
    assert r.status_code == 422
    r = await client.put(
        f"{base}/flags/ok",
        json={"rules": [{"attribute": "x", "op": "in", "value": "notalist"}]},
        headers=ADMIN,
    )
    assert r.status_code == 422


async def test_config_isolated_between_environments(
    client: AsyncClient, env: dict[str, Any]
) -> None:
    prod = next(e for e in env["project"]["environments"] if e["name"] == "prod")
    await client.post(
        f"/admin/environments/{prod['id']}/config", json={"values": {"x": 1}}, headers=ADMIN
    )
    headers = await _player_headers(client, env)  # dev player
    assert (await client.get("/v1/config", headers=headers)).json()["version"] == 0
