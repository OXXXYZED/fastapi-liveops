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


async def test_list_players(client: AsyncClient, env: dict[str, Any]) -> None:
    key = {"X-Api-Key": env["key"]["key"]}
    a = (
        await client.post("/v1/auth/anonymous", json={"device_id": "dev-aaaaaaaa"}, headers=key)
    ).json()
    await client.post("/v1/auth/anonymous", json={}, headers=key)
    base = f"/admin/environments/{env['env']['id']}/players"
    r = (await client.get(base, headers=ADMIN)).json()
    assert r["total"] == 2 and len(r["players"]) == 2
    r = (await client.get(f"{base}?q=dev-aaaaaaaa", headers=ADMIN)).json()
    assert r["total"] == 1 and r["players"][0]["id"] == a["player_id"]
    r = (await client.get(f"{base}?q={a['player_id']}", headers=ADMIN)).json()
    assert r["total"] == 1
    r = (await client.get(f"{base}?limit=1&offset=1", headers=ADMIN)).json()
    assert r["total"] == 2 and len(r["players"]) == 1


async def test_admin_ui_mount(tmp_path: Any, settings: Any) -> None:
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport

    from app.main import create_app

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>ui</html>")
    (tmp_path / "assets" / "app.js").write_text("js")
    (tmp_path.parent / "secret.txt").write_text("nope")
    settings.admin_ui_dir = str(tmp_path)
    app = create_app(settings)
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c,
    ):
        assert (await c.get("/ui/")).text == "<html>ui</html>"
        assert (await c.get("/ui/projects/123")).text == "<html>ui</html>"
        assert (await c.get("/ui/assets/app.js")).text == "js"
        assert (await c.get("/ui/../secret.txt")).text != "nope"
        assert (await c.get("/ui/%2e%2e/secret.txt")).text != "nope"
        assert (await c.get("/", follow_redirects=False)).status_code == 307
