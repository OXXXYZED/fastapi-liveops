from collections.abc import AsyncIterator
from typing import Any

import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.config import Settings
from app.main import create_app

ADMIN = {"Authorization": "Bearer test-admin"}


@pytest.fixture
def settings() -> Settings:
    return Settings(admin_token="test-admin", jwt_secret="test-secret-" + "x" * 32)


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    app = create_app(settings)
    async with LifespanManager(app):
        async with app.state.engine.begin() as conn:
            await conn.execute(text("TRUNCATE projects, players RESTART IDENTITY CASCADE"))
        await app.state.redis.flushdb()
        yield app


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def env(client: AsyncClient) -> dict[str, Any]:
    """A project with dev/prod environments and a client key for dev."""
    r = await client.post("/admin/projects", json={"slug": "game", "name": "Game"}, headers=ADMIN)
    assert r.status_code == 201, r.text
    project = r.json()
    dev = next(e for e in project["environments"] if e["name"] == "dev")
    r = await client.post(
        f"/admin/environments/{dev['id']}/api-keys",
        json={"kind": "client", "name": "build"},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    return {"project": project, "env": dev, "key": r.json()}
