import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from tests.conftest import ADMIN


async def _player(client: AsyncClient, env: dict[str, Any]) -> tuple[str, dict[str, str]]:
    r = (
        await client.post("/v1/auth/anonymous", json={}, headers={"X-Api-Key": env["key"]["key"]})
    ).json()
    return r["player_id"], {"Authorization": f"Bearer {r['access_token']}"}


class Validator:
    """Fake validation service; `respond` decides the answer per request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.respond: Any = lambda body: httpx.Response(200, json={"allow": True})

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        result: httpx.Response = self.respond(json.loads(req.content))
        return result


@pytest.fixture
def validator(app: FastAPI) -> Validator:
    v = Validator()
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(v))
    return v


async def _board(client: AsyncClient, env: dict[str, Any], **kw: Any) -> tuple[str, str]:
    base = f"/admin/environments/{env['env']['id']}/leaderboards"
    body = {
        "key": "ranked",
        "name": "Ranked",
        "validation_url": "https://anticheat.example/check",
        **kw,
    }
    r = await client.post(base, json=body, headers=ADMIN)
    assert r.status_code == 201, r.text
    assert "validation_secret" not in r.json()
    secret = (await client.post(f"{base}/ranked/validation-secret", headers=ADMIN)).json()["secret"]
    return "ranked", secret


async def _submit(client: AsyncClient, h: dict[str, str], score: int, **kw: Any) -> httpx.Response:
    return await client.post(
        "/v1/leaderboards/ranked/scores", json={"score": score, **kw}, headers=h
    )


async def test_allow_and_signed_request(
    client: AsyncClient, env: dict[str, Any], validator: Validator
) -> None:
    _, secret = await _board(client, env)
    pid, h = await _player(client, env)
    r = await _submit(client, h, 500, metadata={"match_id": "m1", "duration_s": 312})
    assert r.status_code == 200 and r.json()["score"] == 500

    req = validator.requests[0]
    body = json.loads(req.content)
    assert body["player_id"] == pid and body["score"] == 500 and body["leaderboard"] == "ranked"
    assert body["metadata"] == {"match_id": "m1", "duration_s": 312}
    t, v1 = (p.split("=", 1)[1] for p in req.headers["X-LiveOps-Signature"].split(","))
    expected = hmac.new(secret.encode(), f"{t}.{req.content.decode()}".encode(), hashlib.sha256)
    assert hmac.compare_digest(expected.hexdigest(), v1)


async def test_reject_and_adjust(
    client: AsyncClient, env: dict[str, Any], validator: Validator
) -> None:
    await _board(client, env, max_score=10_000)
    _, h = await _player(client, env)

    validator.respond = lambda b: httpx.Response(
        200, json={"allow": False, "reason": "impossible time"}
    )
    r = await _submit(client, h, 900)
    assert r.status_code == 422 and r.json()["detail"] == {
        "message": "score rejected",
        "reason": "impossible time",
    }
    assert (await client.get("/v1/leaderboards/ranked", headers=h)).json()["total"] == 0

    validator.respond = lambda b: httpx.Response(
        200, json={"allow": True, "score": b["score"] // 2}
    )
    r = await _submit(client, h, 900)
    assert r.json()["score"] == 450

    # Adjusted scores must still respect the board bounds.
    validator.respond = lambda b: httpx.Response(200, json={"allow": True, "score": 99_999})
    assert (await _submit(client, h, 1)).status_code == 422


@pytest.mark.parametrize(
    "response",
    [
        lambda b: httpx.Response(500),
        lambda b: httpx.Response(200, text="not json"),
        lambda b: httpx.Response(200, json={"allow": "yes"}),
        lambda b: httpx.Response(200, json={"allow": True, "score": 1.5}),
        lambda b: (_ for _ in ()).throw(httpx.ConnectTimeout("slow")),
    ],
)
async def test_failure_modes(
    client: AsyncClient, env: dict[str, Any], validator: Validator, response: Any
) -> None:
    base = f"/admin/environments/{env['env']['id']}/leaderboards"
    await _board(client, env)
    _, h = await _player(client, env)
    validator.respond = response

    r = await _submit(client, h, 5)  # fail closed (default)
    assert r.status_code == 422 and r.json()["detail"]["reason"] == "score validation unavailable"

    await client.patch(f"{base}/ranked", json={"validation_fail_open": True}, headers=ADMIN)
    r = await _submit(client, h, 5)
    assert r.status_code == 200 and r.json()["score"] == 5


async def test_no_hook_means_no_call(
    client: AsyncClient, env: dict[str, Any], validator: Validator
) -> None:
    base = f"/admin/environments/{env['env']['id']}/leaderboards"
    await client.post(base, json={"key": "ranked", "name": "R"}, headers=ADMIN)
    _, h = await _player(client, env)
    assert (await _submit(client, h, 5)).status_code == 200
    assert validator.requests == []
    # Turning a hook on later generates a secret automatically.
    r = await client.patch(
        f"{base}/ranked", json={"validation_url": "https://x.example/v"}, headers=ADMIN
    )
    assert r.json()["validation_url"] == "https://x.example/v"
    assert (await _submit(client, h, 6)).status_code == 200
    assert len(validator.requests) == 1 and "X-LiveOps-Signature" in validator.requests[0].headers


async def test_submit_rate_limit_and_metadata_size(
    client: AsyncClient, env: dict[str, Any], validator: Validator
) -> None:
    base = f"/admin/environments/{env['env']['id']}/leaderboards"
    await client.post(
        base, json={"key": "ranked", "name": "R", "max_submits_per_minute": 2}, headers=ADMIN
    )
    _, h = await _player(client, env)
    assert (await _submit(client, h, 1)).status_code == 200
    assert (await _submit(client, h, 2)).status_code == 200
    r = await _submit(client, h, 3)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    _, other = await _player(client, env)
    assert (await _submit(client, other, 1)).status_code == 200  # per player

    r = await _submit(client, other, 1, metadata={"blob": "x" * 5000})
    assert r.status_code == 413
