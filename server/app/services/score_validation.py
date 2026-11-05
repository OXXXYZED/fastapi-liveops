"""Synchronous score validation hook.

Before a score is stored, the server POSTs to the leaderboard's `validation_url`::

    {"leaderboard": "main", "environment_id": "...", "player_id": "...",
     "score": 1200, "season": "2026-W45", "metadata": {...}}

signed like webhooks (``X-LiveOps-Signature``). The validator answers ``200`` with
``{"allow": true}``, optionally ``{"allow": true, "score": 1100}`` to adjust the score, or
``{"allow": false, "reason": "..."}``. Timeouts, non-2xx and malformed answers are treated
according to ``validation_fail_open``.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

from app.models import Leaderboard
from app.services.events import sign

log = logging.getLogger("liveops.score_validation")
TIMEOUT_SECONDS = 3.0
MAX_METADATA_BYTES = 4096


@dataclass(frozen=True, slots=True)
class Verdict:
    allow: bool
    score: int
    reason: str | None = None


async def validate(
    http: httpx.AsyncClient,
    board: Leaderboard,
    player_id: uuid.UUID,
    score: int,
    season: str,
    metadata: dict[str, Any] | None,
) -> Verdict:
    if not board.validation_url or not board.validation_secret:
        return Verdict(True, score)
    body = json.dumps(
        {
            "leaderboard": board.key,
            "environment_id": str(board.environment_id),
            "player_id": str(player_id),
            "score": score,
            "season": season,
            "metadata": metadata or {},
        },
        separators=(",", ":"),
    )
    try:
        r = await http.post(
            board.validation_url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-LiveOps-Event": "leaderboard.validate",
                "X-LiveOps-Signature": sign(board.validation_secret, body),
            },
            timeout=TIMEOUT_SECONDS,
        )
        r.raise_for_status()
        answer = r.json()
        allow = answer["allow"]
        if not isinstance(allow, bool):
            raise ValueError("allow must be a boolean")
        adjusted = answer.get("score", score)
        if not isinstance(adjusted, int) or isinstance(adjusted, bool) or abs(adjusted) > 2**53:
            raise ValueError("score must be an integer within ±2^53")
        reason = answer.get("reason")
        return Verdict(allow, adjusted, str(reason)[:200] if reason else None)
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("validator for %s failed: %s", board.key, exc)
        if board.validation_fail_open:
            return Verdict(True, score)
        return Verdict(False, score, "score validation unavailable")
