"""Loads an environment's live config + flags, cached in Redis and invalidated on writes."""

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConfigVersion, FeatureFlag

CACHE_TTL_SECONDS = 300


def _cache_key(environment_id: uuid.UUID) -> str:
    return f"cfg:{environment_id}"


async def invalidate(redis: Redis, environment_id: uuid.UUID) -> None:
    await redis.delete(_cache_key(environment_id))


async def load_snapshot(
    db: AsyncSession, redis: Redis, environment_id: uuid.UUID
) -> dict[str, Any]:
    """{"version": int, "values": {...}, "flags": [{key, enabled, rollout_percent, rules}]}"""
    cached = await redis.get(_cache_key(environment_id))
    if cached is not None:
        result: dict[str, Any] = json.loads(cached)
        return result

    latest = await db.scalar(
        select(ConfigVersion)
        .where(ConfigVersion.environment_id == environment_id)
        .order_by(ConfigVersion.version.desc())
        .limit(1)
    )
    flags = await db.scalars(
        select(FeatureFlag)
        .where(FeatureFlag.environment_id == environment_id)
        .order_by(FeatureFlag.key)
    )
    snapshot = {
        "version": latest.version if latest else 0,
        "values": latest.values if latest else {},
        "flags": [
            {
                "key": f.key,
                "enabled": f.enabled,
                "rollout_percent": f.rollout_percent,
                "rules": f.rules,
            }
            for f in flags
        ],
    }
    await redis.set(_cache_key(environment_id), json.dumps(snapshot), ex=CACHE_TTL_SECONDS)
    return snapshot
