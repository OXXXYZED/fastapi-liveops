"""Load test simulating game clients.

    uv run --with locust locust -f loadtest/locustfile.py --host http://localhost:8000 \
        --headless -u 200 -r 50 -t 1m

Requires LIVEOPS_TEST_API_KEY for an environment that has a leaderboard "it-main"
(scripts/seed_integration.sh creates one and prints the key). Raise the server's rate limit
(LIVEOPS_RATE_LIMIT_REQUESTS) or every simulated player shares your IP's client-key bucket.
"""

import os
import random
import uuid

from locust import HttpUser, between, task

API_KEY = os.environ["LIVEOPS_TEST_API_KEY"]
BOARD = os.environ.get("LIVEOPS_TEST_BOARD", "it-main")


class GamePlayer(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        r = self.client.post(
            "/v1/auth/anonymous",
            json={"device_id": f"load-{uuid.uuid4().hex}"},
            headers={"X-Api-Key": API_KEY},
            name="auth",
        )
        r.raise_for_status()
        self.client.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self.etag: str | None = None
        self.save_version = 0

    @task(10)
    def config(self) -> None:
        headers = {"If-None-Match": self.etag} if self.etag else {}
        with self.client.get(
            "/v1/config?platform=android&app_version=1.4.0",
            headers=headers,
            name="config",
            catch_response=True,
        ) as r:
            if r.status_code in (200, 304):
                self.etag = r.headers.get("ETag", self.etag)
                r.success()

    @task(5)
    def submit_score(self) -> None:
        self.client.post(
            f"/v1/leaderboards/{BOARD}/scores",
            json={"score": random.randint(0, 1_000_000)},
            name="score submit",
        )

    @task(3)
    def top(self) -> None:
        self.client.get(f"/v1/leaderboards/{BOARD}?limit=20", name="leaderboard top")

    @task(2)
    def around_me(self) -> None:
        self.client.get(f"/v1/leaderboards/{BOARD}/me?radius=3", name="leaderboard me")

    @task(2)
    def save(self) -> None:
        with self.client.put(
            "/v1/saves/main",
            json={"data": {"gold": random.randint(0, 9999)}, "expected_version": self.save_version},
            name="save write",
            catch_response=True,
        ) as r:
            if r.status_code == 200:
                self.save_version = r.json()["version"]
            elif r.status_code == 409:
                self.save_version = r.json()["detail"]["current_version"]
                r.success()
