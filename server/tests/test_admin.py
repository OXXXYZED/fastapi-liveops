from typing import Any

from httpx import AsyncClient

from tests.conftest import ADMIN


async def test_admin_requires_token(client: AsyncClient) -> None:
    assert (await client.get("/admin/projects")).status_code == 401
    r = await client.get("/admin/projects", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


async def test_project_crud(client: AsyncClient, env: dict[str, Any]) -> None:
    project = env["project"]
    assert sorted(e["name"] for e in project["environments"]) == ["dev", "prod"]

    r = await client.post("/admin/projects", json={"slug": "game", "name": "x"}, headers=ADMIN)
    assert r.status_code == 409

    r = await client.post(
        f"/admin/projects/{project['id']}/environments", json={"name": "staging"}, headers=ADMIN
    )
    assert r.status_code == 201
    r = await client.post(
        f"/admin/projects/{project['id']}/environments", json={"name": "staging"}, headers=ADMIN
    )
    assert r.status_code == 409

    r = await client.get(f"/admin/projects/{project['id']}", headers=ADMIN)
    assert len(r.json()["environments"]) == 3

    r = await client.delete(f"/admin/projects/{project['id']}", headers=ADMIN)
    assert r.status_code == 204
    assert (await client.get("/admin/projects", headers=ADMIN)).json() == []


async def test_invalid_slug(client: AsyncClient) -> None:
    r = await client.post("/admin/projects", json={"slug": "Bad Slug", "name": "x"}, headers=ADMIN)
    assert r.status_code == 422


async def test_api_key_lifecycle(client: AsyncClient, env: dict[str, Any]) -> None:
    key = env["key"]
    assert key["key"].startswith("lo_client_")
    assert key["key"].startswith(key["prefix"])

    r = await client.get(f"/admin/environments/{env['env']['id']}/api-keys", headers=ADMIN)
    listed = r.json()
    assert len(listed) == 1 and "key" not in listed[0]

    r = await client.delete(f"/admin/api-keys/{key['id']}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["revoked_at"] is not None

    r = await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": key["key"]})
    assert r.status_code == 401
