"""Loads an environment's live config + flags, cached in Redis and invalidated on writes."""

import json
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ConfigVersion, Experiment, ExperimentStatus, FeatureFlag
from app.services import events

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
    experiments = await db.scalars(
        select(Experiment)
        .where(
            Experiment.environment_id == environment_id,
            Experiment.status == ExperimentStatus.running,
        )
        .order_by(Experiment.key)
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
        "experiments": [
            {
                "id": str(e.id),
                "key": e.key,
                "traffic_percent": e.traffic_percent,
                "rules": e.rules,
                "variants": e.variants,
            }
            for e in experiments
        ],
    }
    await redis.set(_cache_key(environment_id), json.dumps(snapshot), ex=CACHE_TTL_SECONDS)
    return snapshot


class PublishConflict(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


async def live_version(db: AsyncSession, environment_id: uuid.UUID) -> int:
    v = await db.scalar(
        select(func.max(ConfigVersion.version)).where(
            ConfigVersion.environment_id == environment_id
        )
    )
    return int(v or 0)


async def publish(
    db: AsyncSession,
    redis: Redis,
    environment_id: uuid.UUID,
    values: dict[str, Any],
    note: str | None,
    base_version: int | None = None,
    actor: str | None = None,
) -> ConfigVersion:
    # Serialize publishes per environment so version numbers never race (manual + scheduled).
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"publish:{environment_id}"},
    )
    live = await live_version(db, environment_id)
    if base_version is not None and base_version != live:
        await db.rollback()
        raise PublishConflict(f"live version is {live}, not {base_version}")
    row = ConfigVersion(environment_id=environment_id, version=live + 1, values=values, note=note)
    db.add(row)
    await events.emit(
        db, environment_id, "config.published", {"version": live + 1, "note": note, "by": actor}
    )
    try:
        await db.commit()
    except IntegrityError as exc:  # concurrent publish took the same version number
        await db.rollback()
        raise PublishConflict("concurrent publish, retry") from exc
    await invalidate(redis, environment_id)
    return row
