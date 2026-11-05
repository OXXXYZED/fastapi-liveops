"""A/B experiment assignment. Deterministic, sticky (first assignment is persisted)."""

import copy
import hashlib
import uuid
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ExperimentExposure
from app.services import events
from app.services.flags import Condition, _match

_SEEN_TTL = 30 * 24 * 3600


def _unit(*parts: str) -> float:
    """Uniform float in [0, 1) from the hash of parts."""
    digest = hashlib.sha256(":".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def choose_variant(exp: dict[str, Any], player_id: str, attrs: dict[str, str]) -> str | None:
    """Variant key for the player, or None if not enrolled (rules or traffic)."""
    for raw in exp["rules"]:
        if not _match(Condition.model_validate(raw), attrs):
            return None
    if _unit("exp-traffic", exp["key"], player_id) * 100 >= exp["traffic_percent"]:
        return None
    variants = exp["variants"]
    total = sum(v["weight"] for v in variants)
    point = _unit("exp-variant", exp["key"], player_id) * total
    for v in variants:
        point -= v["weight"]
        if point < 0:
            return str(v["key"])
    return str(variants[-1]["key"])


def deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in overrides.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


async def assign(
    db: AsyncSession,
    redis: Redis,
    environment_id: uuid.UUID,
    experiments: list[dict[str, Any]],
    player_id: uuid.UUID,
    attrs: dict[str, str],
) -> dict[str, str]:
    """Returns {experiment_key: variant}. Persists first exposures and emits webhook events.

    Previously assigned players keep their variant even if weights or traffic change later.
    """
    if not experiments:
        return {}
    pid = str(player_id)
    keys = [f"exp:{e['id']}:{pid}" for e in experiments]
    cached = await redis.mget(keys)
    result: dict[str, str] = {}
    new: list[tuple[dict[str, Any], str]] = []
    for exp, seen in zip(experiments, cached, strict=True):
        if seen is not None:
            if seen != "-":
                result[exp["key"]] = str(seen)
            continue
        variant = choose_variant(exp, pid, attrs)
        if variant is None:
            # Not enrolled now; don't persist so rule/traffic changes can still enroll them.
            continue
        new.append((exp, variant))

    if new:
        for exp, variant in new:
            stmt = (
                insert(ExperimentExposure)
                .values(experiment_id=uuid.UUID(exp["id"]), player_id=player_id, variant=variant)
                .on_conflict_do_nothing()
                .returning(ExperimentExposure.variant)
            )
            inserted = (await db.execute(stmt)).scalar_one_or_none()
            if inserted is None:  # assigned earlier (Redis was flushed): keep that variant
                variant = (
                    await db.scalar(
                        select(ExperimentExposure.variant).where(
                            ExperimentExposure.experiment_id == uuid.UUID(exp["id"]),
                            ExperimentExposure.player_id == player_id,
                        )
                    )
                ) or variant
            else:
                await events.emit(
                    db,
                    environment_id,
                    "experiment.exposure",
                    {"experiment": exp["key"], "variant": variant, "player_id": pid},
                )
            result[exp["key"]] = variant
        await db.commit()
        async with redis.pipeline(transaction=False) as pipe:
            for exp, _ in new:
                pipe.set(f"exp:{exp['id']}:{pid}", result[exp["key"]], ex=_SEEN_TTL)
            await pipe.execute()
    return result


def apply(
    values: dict[str, Any], experiments: list[dict[str, Any]], assigned: dict[str, str]
) -> dict[str, Any]:
    """Merge each assigned variant's overrides into config values (experiments in key order)."""
    out = values
    for exp in experiments:
        key = assigned.get(exp["key"])
        if key is None:
            continue
        variant = next((v for v in exp["variants"] if v["key"] == key), None)
        if variant and variant.get("overrides"):
            out = deep_merge(out, variant["overrides"])
    return out
