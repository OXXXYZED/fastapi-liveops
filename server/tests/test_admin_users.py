from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from app.cli import create_admin
from app.core.security import hash_password, verify_password
from app.models import AdminRole
from tests.conftest import ADMIN

PW = "correct horse battery"


async def _user(client: AsyncClient, email: str, role: str) -> dict[str, str]:
    r = await client.post(
        "/admin/users", json={"email": email, "password": PW, "role": role}, headers=ADMIN
    )
    assert r.status_code == 201, r.text
    r = await client.post("/admin/auth/login", json={"email": email.upper(), "password": PW})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "id": r.json()["user"]["id"]}


def test_password_hashing() -> None:
    h = hash_password("secret-password")
    assert verify_password("secret-password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")


async def test_login_and_me(client: AsyncClient) -> None:
    h = await _user(client, "Ed@Example.com", "editor")
    me = (await client.get("/admin/auth/me", headers={"Authorization": h["Authorization"]})).json()
    assert me["email"] == "ed@example.com" and me["role"] == "editor"
    assert (await client.get("/admin/auth/me", headers=ADMIN)).json()["role"] == "owner"

    r = await client.post("/admin/auth/login", json={"email": "ed@example.com", "password": "nope"})
    assert r.status_code == 401
    r = await client.post("/admin/auth/login", json={"email": "ghost@x.io", "password": PW})
    assert r.status_code == 401


async def test_roles(client: AsyncClient, env: dict[str, Any]) -> None:
    viewer = await _user(client, "v@x.io", "viewer")
    editor = await _user(client, "e@x.io", "editor")
    v = {"Authorization": viewer["Authorization"]}
    e = {"Authorization": editor["Authorization"]}
    base = f"/admin/environments/{env['env']['id']}"

    assert (await client.get("/admin/projects", headers=v)).status_code == 200
    r = await client.post(f"{base}/config", json={"values": {}}, headers=v)
    assert r.status_code == 403
    assert (await client.post(f"{base}/config", json={"values": {}}, headers=e)).status_code == 201

    # Owner-only.
    assert (await client.get("/admin/users", headers=e)).status_code == 403
    r = await client.delete(f"/admin/projects/{env['project']['id']}", headers=e)
    assert r.status_code == 403

    # Player tokens can't be used as admin tokens.
    login = await client.post(
        "/v1/auth/anonymous", json={}, headers={"X-Api-Key": env["key"]["key"]}
    )
    p = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/admin/projects", headers=p)).status_code == 401


async def test_disable_and_role_change_apply_immediately(client: AsyncClient) -> None:
    u = await _user(client, "d@x.io", "editor")
    h = {"Authorization": u["Authorization"]}
    await client.patch(f"/admin/users/{u['id']}", json={"role": "viewer"}, headers=ADMIN)
    assert (await client.get("/admin/auth/me", headers=h)).json()["role"] == "viewer"
    await client.patch(f"/admin/users/{u['id']}", json={"disabled": True}, headers=ADMIN)
    assert (await client.get("/admin/auth/me", headers=h)).status_code == 401
    r = await client.post("/admin/auth/login", json={"email": "d@x.io", "password": PW})
    assert r.status_code == 401


async def test_last_owner_protection(client: AsyncClient) -> None:
    o = await _user(client, "o@x.io", "owner")
    h = {"Authorization": o["Authorization"]}
    r = await client.patch(f"/admin/users/{o['id']}", json={"role": "editor"}, headers=h)
    assert r.status_code == 409
    assert (await client.delete(f"/admin/users/{o['id']}", headers=h)).status_code == 409
    o2 = await _user(client, "o2@x.io", "owner")
    r = await client.patch(f"/admin/users/{o2['id']}", json={"role": "editor"}, headers=h)
    assert r.status_code == 200
    assert (await client.delete(f"/admin/users/{o2['id']}", headers=h)).status_code == 204
    r = await client.post(
        "/admin/users", json={"email": "o@x.io", "password": PW, "role": "viewer"}, headers=h
    )
    assert r.status_code == 409


async def test_audit_log(client: AsyncClient, env: dict[str, Any]) -> None:
    e = await _user(client, "audit@x.io", "editor")
    base = f"/admin/environments/{env['env']['id']}"
    await client.put(f"{base}/flags/f1", json={}, headers={"Authorization": e["Authorization"]})
    await client.put(f"{base}/flags/BAD KEY", json={}, headers=ADMIN)  # failed: not logged
    log = (await client.get("/admin/audit-log", headers=ADMIN)).json()
    latest = log["entries"][0]
    assert latest["actor"] == "audit@x.io" and latest["method"] == "PUT"
    assert latest["detail"]["key"] == "f1"
    assert all("BAD" not in x["path"] for x in log["entries"])


async def test_login_rate_limit(client: AsyncClient) -> None:
    for _ in range(10):
        await client.post("/admin/auth/login", json={"email": "rl@x.io", "password": "x"})
    r = await client.post("/admin/auth/login", json={"email": "rl@x.io", "password": "x"})
    assert r.status_code == 429


async def test_static_token_can_be_disabled(
    settings: Any, app: FastAPI, client: AsyncClient
) -> None:
    app.state.settings.admin_token = type(settings.admin_token)("")
    assert (await client.get("/admin/projects", headers=ADMIN)).status_code == 401


async def test_cli_create_admin(client: AsyncClient) -> None:
    await create_admin("Cli@X.io", PW, AdminRole.owner)
    r = await client.post("/admin/auth/login", json={"email": "cli@x.io", "password": PW})
    assert r.json()["user"]["role"] == "owner"
    await create_admin("cli@x.io", "another-password", AdminRole.viewer)  # reset
    r = await client.post("/admin/auth/login", json={"email": "cli@x.io", "password": PW})
    assert r.status_code == 401
