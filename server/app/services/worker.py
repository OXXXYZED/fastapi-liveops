"""Background jobs: scheduled config publishes and webhook delivery.

Runs inside each API process (disable with LIVEOPS_RUN_WORKER=false). Jobs lock rows with
SKIP LOCKED, so several replicas can run the worker concurrently.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import ConfigSchedule, ScheduleStatus
from app.services import events, remote_config

log = logging.getLogger("liveops.worker")


async def publish_due_schedules(db: AsyncSession, redis: Redis, limit: int = 20) -> int:
    """Publish due schedules, one per transaction.

    Each schedule is locked with SKIP LOCKED and published in the same transaction that marks it
    done, so concurrent workers never publish a schedule twice.
    """
    done = 0
    for _ in range(limit):
        sched = await db.scalar(
            select(ConfigSchedule)
            .where(
                ConfigSchedule.status == ScheduleStatus.pending,
                ConfigSchedule.publish_at <= datetime.now(UTC),
            )
            .order_by(ConfigSchedule.publish_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if sched is None:
            break
        sched_id, env_id = sched.id, sched.environment_id
        try:
            note = sched.note or "scheduled publish"
            values, actor = sched.values, sched.created_by
            sched.status = ScheduleStatus.published
            # publish() commits the new version together with the schedule status.
            row = await remote_config.publish(db, redis, env_id, values, note, None, actor)
            await db.execute(
                update(ConfigSchedule)
                .where(ConfigSchedule.id == sched_id)
                .values(published_version=row.version)
            )
            await db.commit()
            done += 1
        except remote_config.PublishConflict:
            await db.rollback()  # concurrent manual publish; stays pending, retried next tick
            break
        except Exception as exc:  # noqa: BLE001 — keep the worker alive, record the failure
            log.exception("scheduled publish %s failed", sched_id)
            await db.rollback()
            failed = await db.get(ConfigSchedule, sched_id)
            if failed is not None:
                failed.status, failed.error = ScheduleStatus.failed, str(exc)[:500]
                await db.commit()
    return done


async def tick(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Redis, http: httpx.AsyncClient
) -> None:
    async with sessionmaker() as db:
        await publish_due_schedules(db, redis)
    async with sessionmaker() as db:
        await events.deliver_due(db, http)


async def run_forever(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    http: httpx.AsyncClient,
    interval: float,
) -> None:
    while True:
        try:
            await tick(sessionmaker, redis, http)
        except Exception:  # noqa: BLE001
            log.exception("worker tick failed")
        await asyncio.sleep(interval)


@contextlib.asynccontextmanager
async def running(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
    http: httpx.AsyncClient,
    interval: float,
    enabled: bool,
) -> AsyncIterator[None]:
    task = (
        asyncio.create_task(run_forever(sessionmaker, redis, http, interval)) if enabled else None
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
