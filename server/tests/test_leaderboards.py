import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.models import ResetSchedule
from app.services.leaderboards import previous_season_id, season_id
from tests.conftest import ADMIN


async def _player(client: AsyncClient, env: dict[str, Any]) -> tuple[str, dict[str, str]]:
    r = await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": env["key"]["key"]})
    body = r.json()
    return body["player_id"], {"Authorization": f"Bearer {body['access_token']}"}


async def _board(client: AsyncClient, env: dict[str, Any], **kw: Any) -> str:
    body = {"key": "main", "name": "Main", **kw}
    r = await client.post(
        f"/admin/environments/{env['env']['id']}/leaderboards", json=body, headers=ADMIN
    )
    assert r.status_code == 201, r.text
    return str(body["key"])


async def _submit(client: AsyncClient, h: dict[str, str], key: str, score: int) -> Any:
    return await client.post(f"/v1/leaderboards/{key}/scores", json={"score": score}, headers=h)


def test_season_ids() -> None:
    t = datetime(2026, 1, 1, 12, tzinfo=UTC)  # Thursday, ISO week 2026-W01
    assert season_id(ResetSchedule.never, t) == "all"
    assert season_id(ResetSchedule.daily, t) == "2026-01-01"
    assert season_id(ResetSchedule.weekly, t) == "2026-W01"
    assert season_id(ResetSchedule.monthly, t) == "2026-01"
    assert previous_season_id(ResetSchedule.daily, t) == "2025-12-31"
    assert previous_season_id(ResetSchedule.weekly, t) == "2025-W52"
    assert previous_season_id(ResetSchedule.monthly, t) == "2025-12"


async def test_best_mode_desc_ranking(client: AsyncClient, env: dict[str, Any]) -> None:
    key = await _board(client, env)
    players = [await _player(client, env) for _ in range(5)]
    for i, (_, h) in enumerate(players):
        r = await _submit(client, h, key, (i + 1) * 100)
        assert r.status_code == 200, r.text

    # Lower score in best mode doesn't overwrite.
    r = await _submit(client, players[4][1], key, 1)
    assert r.json()["score"] == 500 and r.json()["rank"] == 1

    page = (await client.get(f"/v1/leaderboards/{key}?limit=3", headers=players[0][1])).json()
    assert page["total"] == 5
    assert [e["score"] for e in page["entries"]] == [500, 400, 300]
    assert [e["rank"] for e in page["entries"]] == [1, 2, 3]

    page = (
        await client.get(f"/v1/leaderboards/{key}?offset=3&limit=10", headers=players[0][1])
    ).json()
    assert [e["rank"] for e in page["entries"]] == [4, 5]

    me = (await client.get(f"/v1/leaderboards/{key}/me?radius=1", headers=players[2][1])).json()
    assert me["me"]["rank"] == 3 and me["me"]["player_id"] == players[2][0]
    assert [e["score"] for e in me["around"]] == [400, 300, 200]


async def test_asc_sum_latest_modes(client: AsyncClient, env: dict[str, Any]) -> None:
    _, h = await _player(client, env)
    await _board(client, env, key="time", sort_order="asc")
    await _submit(client, h, "time", 90)
    assert (await _submit(client, h, "time", 60)).json()["score"] == 60
    assert (await _submit(client, h, "time", 80)).json()["score"] == 60

    await _board(client, env, key="xp", update_mode="sum")
    await _submit(client, h, "xp", 10)
    assert (await _submit(client, h, "xp", 15)).json()["score"] == 25

    await _board(client, env, key="last", update_mode="latest")
    await _submit(client, h, "last", 10)
    assert (await _submit(client, h, "last", 3)).json()["score"] == 3


async def test_asc_ordering(client: AsyncClient, env: dict[str, Any]) -> None:
    key = await _board(client, env, sort_order="asc")
    hs = [await _player(client, env) for _ in range(3)]
    for (_, h), s in zip(hs, [30, 10, 20], strict=True):
        await _submit(client, h, key, s)
    page = (await client.get(f"/v1/leaderboards/{key}", headers=hs[0][1])).json()
    assert [e["score"] for e in page["entries"]] == [10, 20, 30]


async def test_concurrent_sum_is_consistent(client: AsyncClient, env: dict[str, Any]) -> None:
    key = await _board(client, env, update_mode="sum")
    pid, h = await _player(client, env)
    await asyncio.gather(*[_submit(client, h, key, 1) for _ in range(20)])
    page = (await client.get(f"/v1/leaderboards/{key}", headers=h)).json()
    assert page["entries"] == [{"rank": 1, "player_id": pid, "score": 20}]


async def test_score_bounds(client: AsyncClient, env: dict[str, Any]) -> None:
    key = await _board(client, env, min_score=0, max_score=1000)
    _, h = await _player(client, env)
    assert (await _submit(client, h, key, 1001)).status_code == 422
    assert (await _submit(client, h, key, -1)).status_code == 422
    assert (await _submit(client, h, key, 2**60)).status_code == 422
    assert (await _submit(client, h, key, 1000)).status_code == 200

    r = await client.post(
        f"/admin/environments/{env['env']['id']}/leaderboards",
        json={"key": "bad", "name": "x", "min_score": 5, "max_score": 1},
        headers=ADMIN,
    )
    assert r.status_code == 422


async def test_rebuild_after_redis_loss(
    client: AsyncClient, env: dict[str, Any], app: FastAPI
) -> None:
    key = await _board(client, env)
    hs = [await _player(client, env) for _ in range(3)]
    for (_, h), s in zip(hs, [1, 2, 3], strict=True):
        await _submit(client, h, key, s)
    await app.state.redis.flushdb()

    # A new submit after the flush must not leave a one-member index.
    await _submit(client, hs[0][1], key, 5)
    page = (await client.get(f"/v1/leaderboards/{key}", headers=hs[0][1])).json()
    assert [e["score"] for e in page["entries"]] == [5, 3, 2]

    await app.state.redis.flushdb()
    page = (await client.get(f"/v1/leaderboards/{key}", headers=hs[0][1])).json()
    assert page["total"] == 3


async def test_admin_remove_and_delete(client: AsyncClient, env: dict[str, Any]) -> None:
    key = await _board(client, env)
    pid, h = await _player(client, env)
    await _submit(client, h, key, 10)
    base = f"/admin/environments/{env['env']['id']}/leaderboards"

    assert (await client.delete(f"{base}/{key}/scores/{pid}", headers=ADMIN)).status_code == 204
    assert (await client.delete(f"{base}/{key}/scores/{pid}", headers=ADMIN)).status_code == 404
    page = (await client.get(f"/v1/leaderboards/{key}", headers=h)).json()
    assert page["total"] == 0
    me = (await client.get(f"/v1/leaderboards/{key}/me", headers=h)).json()
    assert me["me"] is None and me["around"] == []

    r = await client.patch(f"{base}/{key}", json={"max_score": 50}, headers=ADMIN)
    assert r.json()["max_score"] == 50
    assert (await client.delete(f"{base}/{key}", headers=ADMIN)).status_code == 204
    assert (await client.get(f"/v1/leaderboards/{key}", headers=h)).status_code == 404


async def test_isolation_and_validation(client: AsyncClient, env: dict[str, Any]) -> None:
    prod = next(e for e in env["project"]["environments"] if e["name"] == "prod")
    await client.post(
        f"/admin/environments/{prod['id']}/leaderboards",
        json={"key": "main", "name": "Prod"},
        headers=ADMIN,
    )
    _, h = await _player(client, env)  # dev player
    assert (await _submit(client, h, "main", 1)).status_code == 404
    r = await client.get("/v1/leaderboards/main?season=bogus", headers=h)
    assert r.status_code == 422


@pytest.mark.parametrize("season", ["previous", "2025-W01"])
async def test_previous_season_empty(client: AsyncClient, env: dict[str, Any], season: str) -> None:
    key = await _board(client, env, reset_schedule="weekly")
    _, h = await _player(client, env)
    await _submit(client, h, key, 10)
    page = (await client.get(f"/v1/leaderboards/{key}?season={season}", headers=h)).json()
    assert page["total"] == 0


async def test_admin_entries(client: AsyncClient, env: dict[str, Any]) -> None:
    key = await _board(client, env)
    for s in (5, 9):
        _, h = await _player(client, env)
        await _submit(client, h, key, s)
    r = await client.get(
        f"/admin/environments/{env['env']['id']}/leaderboards/{key}/entries", headers=ADMIN
    )
    assert [e["score"] for e in r.json()["entries"]] == [9, 5]
