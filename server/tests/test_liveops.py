import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import update

from app.models import ConfigSchedule, WebhookDelivery
from app.services import events, worker
from app.services.experiments import choose_variant, deep_merge
from tests.conftest import ADMIN


async def _player(client: AsyncClient, env: dict[str, Any]) -> tuple[str, dict[str, str]]:
    r = (
        await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": env["key"]["key"]})
    ).json()
    return r["player_id"], {"Authorization": f"Bearer {r['access_token']}"}


def _base(env: dict[str, Any]) -> str:
    return f"/admin/environments/{env['env']['id']}"


# ---------------- pure logic ----------------


def test_deep_merge() -> None:
    base: dict[str, Any] = {"shop": {"price": 10, "currency": "gold"}, "lives": 3}
    assert deep_merge(base, {"shop": {"price": 7}}) == {
        "shop": {"price": 7, "currency": "gold"},
        "lives": 3,
    }
    assert base["shop"]["price"] == 10  # not mutated


def test_variant_distribution_and_traffic() -> None:
    exp = {
        "key": "e",
        "traffic_percent": 50,
        "rules": [],
        "variants": [{"key": "a", "weight": 1}, {"key": "b", "weight": 3}],
    }
    picks = [choose_variant(exp, str(uuid.UUID(int=i)), {}) for i in range(20_000)]
    enrolled = [p for p in picks if p]
    assert 9_500 < len(enrolled) < 10_500
    assert 0.22 < enrolled.count("a") / len(enrolled) < 0.28
    assert choose_variant(exp, "p", {}) == choose_variant(exp, "p", {})
    exp_rules = {
        **exp,
        "traffic_percent": 100,
        "rules": [{"attribute": "platform", "op": "eq", "value": "ios"}],
    }
    assert choose_variant(exp_rules, "p", {"platform": "android"}) is None
    assert choose_variant(exp_rules, "p", {"platform": "ios"}) in {"a", "b"}


# ---------------- experiments API ----------------


EXP = {
    "description": "Cheaper shop",
    "variants": [
        {"key": "control", "weight": 1, "overrides": {}},
        {"key": "cheap", "weight": 1, "overrides": {"shop": {"price": 5}}},
    ],
}


async def test_experiment_lifecycle(client: AsyncClient, env: dict[str, Any], app: FastAPI) -> None:
    base = _base(env)
    await client.post(
        f"{base}/config",
        json={"values": {"shop": {"price": 10, "currency": "gold"}}},
        headers=ADMIN,
    )
    r = await client.put(f"{base}/experiments/shop_price", json=EXP, headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"] == "draft"

    _, h = await _player(client, env)
    assert (await client.get("/v1/config", headers=h)).json()["experiments"] == {}  # draft

    assert (await client.post(f"{base}/experiments/shop_price/start", headers=ADMIN)).json()[
        "status"
    ] == "running"
    assert (
        await client.post(f"{base}/experiments/shop_price/start", headers=ADMIN)
    ).status_code == 409

    seen: dict[str, int] = {}
    for _ in range(30):
        _, h = await _player(client, env)
        cfg = (await client.get("/v1/config", headers=h)).json()
        variant = cfg["experiments"]["shop_price"]
        seen[variant] = seen.get(variant, 0) + 1
        price = cfg["values"]["shop"]["price"]
        assert price == (5 if variant == "cheap" else 10)
        assert cfg["values"]["shop"]["currency"] == "gold"
        # Sticky: same answer on the next fetch.
        assert (await client.get("/v1/config", headers=h)).json()["experiments"][
            "shop_price"
        ] == variant
    assert set(seen) == {"control", "cheap"}

    res = (await client.get(f"{base}/experiments/shop_price/results", headers=ADMIN)).json()
    assert res["total"] == 30 and res["exposures"] == seen

    # Variant keys are frozen once started; weights may change.
    bad = {**EXP, "variants": [{"key": "x", "weight": 1}, {"key": "y", "weight": 1}]}
    assert (
        await client.put(f"{base}/experiments/shop_price", json=bad, headers=ADMIN)
    ).status_code == 409
    assert (await client.delete(f"{base}/experiments/shop_price", headers=ADMIN)).status_code == 409

    await client.post(f"{base}/experiments/shop_price/stop", headers=ADMIN)
    cfg = (await client.get("/v1/config", headers=h)).json()
    assert cfg["experiments"] == {} and cfg["values"]["shop"]["price"] == 10
    assert (await client.delete(f"{base}/experiments/shop_price", headers=ADMIN)).status_code == 204


async def test_assignment_survives_weight_change_and_redis_flush(
    client: AsyncClient, env: dict[str, Any], app: FastAPI
) -> None:
    base = _base(env)
    await client.put(f"{base}/experiments/e1", json=EXP, headers=ADMIN)
    await client.post(f"{base}/experiments/e1/start", headers=ADMIN)
    players = [await _player(client, env) for _ in range(10)]
    first = {
        pid: (await client.get("/v1/config", headers=h)).json()["experiments"]["e1"]
        for pid, h in players
    }

    skewed = {
        **EXP,
        "variants": [{"key": "control", "weight": 1}, {"key": "cheap", "weight": 1000}],
    }
    await client.put(f"{base}/experiments/e1", json=skewed, headers=ADMIN)
    await app.state.redis.flushdb()
    for pid, h in players:
        assert (await client.get("/v1/config", headers=h)).json()["experiments"]["e1"] == first[pid]


async def test_experiment_validation(client: AsyncClient, env: dict[str, Any]) -> None:
    base = _base(env)
    one = {"variants": [{"key": "a", "weight": 1}]}
    assert (await client.put(f"{base}/experiments/x", json=one, headers=ADMIN)).status_code == 422
    dup = {"variants": [{"key": "a", "weight": 1}, {"key": "a", "weight": 1}]}
    assert (await client.put(f"{base}/experiments/x", json=dup, headers=ADMIN)).status_code == 422
    zero = {"variants": [{"key": "a", "weight": 0}, {"key": "b", "weight": 1}]}
    assert (await client.put(f"{base}/experiments/x", json=zero, headers=ADMIN)).status_code == 422


# ---------------- scheduled publishing ----------------


async def test_scheduled_publish(client: AsyncClient, env: dict[str, Any], app: FastAPI) -> None:
    base = _base(env)
    soon = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    r = await client.post(
        f"{base}/config/schedules",
        json={"values": {"event": "halloween"}, "publish_at": soon},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    later = await client.post(
        f"{base}/config/schedules", json={"values": {"x": 1}, "publish_at": soon}, headers=ADMIN
    )

    past = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    r = await client.post(
        f"{base}/config/schedules", json={"values": {}, "publish_at": past}, headers=ADMIN
    )
    assert r.status_code == 422
    r = await client.post(
        f"{base}/config/schedules",
        json={"values": {}, "publish_at": "2030-01-01T00:00:00"},
        headers=ADMIN,
    )
    assert r.status_code == 422  # naive datetime

    async with app.state.sessionmaker() as db:
        assert await worker.publish_due_schedules(db, app.state.redis) == 0  # not due yet

    assert (
        await client.delete(f"{base}/config/schedules/{later.json()['id']}", headers=ADMIN)
    ).json()["status"] == "cancelled"

    async with app.state.sessionmaker() as db:  # make the first one due
        await db.execute(
            update(ConfigSchedule)
            .where(ConfigSchedule.id == uuid.UUID(sid))
            .values(publish_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()
        assert await worker.publish_due_schedules(db, app.state.redis) == 1
        assert await worker.publish_due_schedules(db, app.state.redis) == 0

    live = (await client.get(f"{base}/config", headers=ADMIN)).json()
    assert live["values"] == {"event": "halloween"} and live["version"] == 1
    sched = {
        s["id"]: s for s in (await client.get(f"{base}/config/schedules", headers=ADMIN)).json()
    }
    assert sched[sid]["status"] == "published" and sched[sid]["published_version"] == 1
    assert sched[later.json()["id"]]["status"] == "cancelled"
    assert (await client.delete(f"{base}/config/schedules/{sid}", headers=ADMIN)).status_code == 409

    _, h = await _player(client, env)
    assert (await client.get("/v1/config", headers=h)).json()["values"] == {"event": "halloween"}


# ---------------- webhooks ----------------


@pytest.fixture
def receiver() -> tuple[list[httpx.Request], httpx.AsyncClient]:
    got: list[httpx.Request] = []
    state = {"fail": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        got.append(req)
        if state["fail"] > 0:
            state["fail"] -= 1
            return httpx.Response(500)
        return httpx.Response(200)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    http.fail = state  # type: ignore[attr-defined]
    return got, http


def _verify(secret: str, req: httpx.Request) -> dict[str, Any]:
    parts = dict(p.split("=", 1) for p in req.headers["X-LiveOps-Signature"].split(","))
    expected = hmac.new(
        secret.encode(), f"{parts['t']}.{req.content.decode()}".encode(), hashlib.sha256
    )
    assert hmac.compare_digest(expected.hexdigest(), parts["v1"])
    body: dict[str, Any] = json.loads(req.content)
    return body


async def test_webhooks_end_to_end(
    client: AsyncClient,
    env: dict[str, Any],
    app: FastAPI,
    receiver: tuple[list[httpx.Request], httpx.AsyncClient],
) -> None:
    got, http = receiver
    base = _base(env)
    r = await client.post(
        f"{base}/webhooks", json={"url": "https://hooks.example/x", "events": ["*"]}, headers=ADMIN
    )
    assert r.status_code == 201
    hook = r.json()
    assert hook["secret"].startswith("whsec_")
    assert "secret" not in (await client.get(f"{base}/webhooks", headers=ADMIN)).json()[0]

    # Generate events of every type.
    await client.post(f"{base}/config", json={"values": {"a": 1}}, headers=ADMIN)
    await client.put(f"{base}/experiments/e", json=EXP, headers=ADMIN)
    await client.post(f"{base}/experiments/e/start", headers=ADMIN)
    pid, h = await _player(client, env)
    await client.get("/v1/config", headers=h)
    await client.post(f"{base}/leaderboards", json={"key": "main", "name": "Main"}, headers=ADMIN)
    await client.post("/v1/leaderboards/main/scores", json={"score": 9}, headers=h)
    r = await client.post(
        "/v1/events",
        json={"events": [{"name": "level_complete", "properties": {"level": 3}}]},
        headers=h,
    )
    assert r.status_code == 202 and r.json() == {"accepted": 1}

    async with app.state.sessionmaker() as db:
        await events.deliver_due(db, http)
    bodies = [_verify(hook["secret"], req) for req in got]
    types = [b["type"] for b in bodies]
    assert types == [
        "config.published",
        "player.created",
        "experiment.exposure",
        "leaderboard.score",
        "player.event",
    ]
    by_type = {b["type"]: b["data"] for b in bodies}
    assert by_type["player.event"] == {
        "player_id": pid,
        "name": "level_complete",
        "properties": {"level": 3},
        "ts": by_type["player.event"]["ts"],
    }
    assert by_type["experiment.exposure"]["experiment"] == "e"
    assert by_type["leaderboard.score"]["score"] == 9
    assert got[0].headers["X-LiveOps-Event"] == "config.published"

    # Exposure is emitted once per player, not on every fetch.
    await client.get("/v1/config", headers=h)
    async with app.state.sessionmaker() as db:
        assert await events.deliver_due(db, http) == 0


async def test_webhook_filtering_retry_and_failure(
    client: AsyncClient,
    env: dict[str, Any],
    app: FastAPI,
    receiver: tuple[list[httpx.Request], httpx.AsyncClient],
) -> None:
    got, http = receiver
    base = _base(env)
    r = await client.post(
        f"{base}/webhooks",
        json={"url": "https://hooks.example/y", "events": ["config.published"]},
        headers=ADMIN,
    )
    hook_id = r.json()["id"]
    assert (
        await client.post(
            f"{base}/webhooks", json={"url": "https://x", "events": ["nope"]}, headers=ADMIN
        )
    ).status_code == 422
    assert (
        await client.post(
            f"{base}/webhooks", json={"url": "ftp://x", "events": ["*"]}, headers=ADMIN
        )
    ).status_code == 422

    await _player(client, env)  # player.created: not subscribed
    http.fail["fail"] = 2  # type: ignore[attr-defined]
    await client.post(f"{base}/config", json={"values": {}}, headers=ADMIN)

    async def run() -> int:
        async with app.state.sessionmaker() as db:
            return await events.deliver_due(db, http)

    async def make_due() -> None:
        async with app.state.sessionmaker() as db:
            await db.execute(update(WebhookDelivery).values(next_attempt_at=datetime.now(UTC)))
            await db.commit()

    assert await run() == 1
    deliveries = (await client.get(f"{base}/webhooks/{hook_id}/deliveries", headers=ADMIN)).json()
    assert deliveries[0]["status"] == "pending" and deliveries[0]["attempts"] == 1
    assert deliveries[0]["last_error"] == "HTTP 500"
    assert await run() == 0  # backoff
    await make_due()
    await run()
    await make_due()
    await run()
    d = (await client.get(f"{base}/webhooks/{hook_id}/deliveries", headers=ADMIN)).json()[0]
    assert d["status"] == "delivered" and d["attempts"] == 3
    assert len(got) == 3

    # Permanent failure after MAX_ATTEMPTS, manual retry resets it.
    http.fail["fail"] = 100  # type: ignore[attr-defined]
    await client.post(f"{base}/webhooks/{hook_id}/test", headers=ADMIN)
    for _ in range(events.MAX_ATTEMPTS):
        await make_due()
        await run()
    d = (await client.get(f"{base}/webhooks/{hook_id}/deliveries", headers=ADMIN)).json()[0]
    assert d["status"] == "failed" and d["attempts"] == events.MAX_ATTEMPTS
    r = await client.post(f"{base}/webhooks/{hook_id}/deliveries/{d['id']}/retry", headers=ADMIN)
    assert r.status_code == 202
    http.fail["fail"] = 0  # type: ignore[attr-defined]
    await run()
    d = (await client.get(f"{base}/webhooks/{hook_id}/deliveries", headers=ADMIN)).json()[0]
    assert d["status"] == "delivered"

    # Inactive hooks get nothing.
    await client.patch(f"{base}/webhooks/{hook_id}", json={"active": False}, headers=ADMIN)
    await client.post(f"{base}/config", json={"values": {"z": 1}}, headers=ADMIN)
    assert await run() == 0


async def test_network_errors_are_retried(
    client: AsyncClient, env: dict[str, Any], app: FastAPI
) -> None:
    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    base = _base(env)
    r = await client.post(
        f"{base}/webhooks", json={"url": "https://down.example", "events": ["*"]}, headers=ADMIN
    )
    await client.post(f"{base}/webhooks/{r.json()['id']}/test", headers=ADMIN)
    async with app.state.sessionmaker() as db:
        await events.deliver_due(db, http)
    d = (await client.get(f"{base}/webhooks/{r.json()['id']}/deliveries", headers=ADMIN)).json()[0]
    assert d["status"] == "pending" and "ConnectError" in d["last_error"]


async def test_events_without_webhooks_are_accepted(
    client: AsyncClient, env: dict[str, Any]
) -> None:
    _, h = await _player(client, env)
    r = await client.post("/v1/events", json={"events": [{"name": "a"}, {"name": "b"}]}, headers=h)
    assert r.json() == {"accepted": 2}
    bad = await client.post("/v1/events", json={"events": [{"name": "has space"}]}, headers=h)
    assert bad.status_code == 422
    too_many = await client.post("/v1/events", json={"events": [{"name": "a"}] * 51}, headers=h)
    assert too_many.status_code == 422


async def test_worker_runs_in_background(settings: Any) -> None:
    """The lifespan starts the worker and stops it cleanly."""
    from asgi_lifespan import LifespanManager

    from app.main import create_app

    settings.run_worker = True
    settings.worker_interval_seconds = 0.05
    app = create_app(settings)
    async with LifespanManager(app):
        pass


async def test_concurrent_workers_publish_once(
    client: AsyncClient, env: dict[str, Any], app: FastAPI
) -> None:
    import asyncio

    base = _base(env)
    soon = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    for i in range(5):
        await client.post(
            f"{base}/config/schedules", json={"values": {"i": i}, "publish_at": soon}, headers=ADMIN
        )
    async with app.state.sessionmaker() as db:
        await db.execute(
            update(ConfigSchedule).values(publish_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await db.commit()

    async def one() -> int:
        async with app.state.sessionmaker() as db:
            return await worker.publish_due_schedules(db, app.state.redis)

    results = await asyncio.gather(*[one() for _ in range(4)])
    versions = (await client.get(f"{base}/config/versions", headers=ADMIN)).json()
    # Every schedule published exactly once (a worker may defer on a version race and retry later).
    async with app.state.sessionmaker() as db:
        while await worker.publish_due_schedules(db, app.state.redis):
            pass
    versions = (await client.get(f"{base}/config/versions", headers=ADMIN)).json()
    assert len(versions) == 5 and sum(results) <= 5
    statuses = {
        s["status"] for s in (await client.get(f"{base}/config/schedules", headers=ADMIN)).json()
    }
    assert statuses == {"published"}
