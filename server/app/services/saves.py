import json
import uuid
from typing import Any, cast

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CloudSave


class SaveConflict(Exception):
    def __init__(self, current_version: int) -> None:
        self.current_version = current_version  # 0 = slot does not exist


class SlotLimitExceeded(Exception):
    pass


def encoded_size(data: Any) -> int:
    return len(json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode())


async def _current_version(db: AsyncSession, player_id: uuid.UUID, slot: str) -> int:
    v = await db.scalar(
        select(CloudSave.version).where(CloudSave.player_id == player_id, CloudSave.slot == slot)
    )
    return int(v or 0)


async def write(
    db: AsyncSession,
    player_id: uuid.UUID,
    slot: str,
    data: Any,
    expected_version: int | None,
    max_slots: int,
) -> CloudSave:
    size = encoded_size(data)
    # Serialize writes per player so the slot-count check can't race.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"save:{player_id}"}
    )

    row: CloudSave | None
    if expected_version is not None and expected_version > 0:
        row = await db.scalar(
            update(CloudSave)
            .where(
                CloudSave.player_id == player_id,
                CloudSave.slot == slot,
                CloudSave.version == expected_version,
            )
            .values(
                data=data, size_bytes=size, version=CloudSave.version + 1, updated_at=func.now()
            )
            .returning(CloudSave),
            execution_options={"populate_existing": True},
        )
    else:
        exists = await _current_version(db, player_id, slot)
        if not exists:
            n = await db.scalar(
                select(func.count()).select_from(CloudSave).where(CloudSave.player_id == player_id)
            )
            if (n or 0) >= max_slots:
                await db.rollback()
                raise SlotLimitExceeded
        stmt = insert(CloudSave).values(
            player_id=player_id, slot=slot, data=data, size_bytes=size, version=1
        )
        if expected_version == 0:
            final = stmt.on_conflict_do_nothing()
        else:
            final = stmt.on_conflict_do_update(
                index_elements=[CloudSave.player_id, CloudSave.slot],
                set_={
                    "data": stmt.excluded.data,
                    "size_bytes": stmt.excluded.size_bytes,
                    "version": CloudSave.version + 1,
                    "updated_at": func.now(),
                },
            )
        row = cast(
            CloudSave | None,
            await db.scalar(
                final.returning(CloudSave), execution_options={"populate_existing": True}
            ),
        )

    if row is None:
        current = await _current_version(db, player_id, slot)
        await db.rollback()
        raise SaveConflict(current)
    await db.commit()
    return row


async def remove(
    db: AsyncSession, player_id: uuid.UUID, slot: str, expected_version: int | None
) -> None:
    stmt = delete(CloudSave).where(CloudSave.player_id == player_id, CloudSave.slot == slot)
    if expected_version is not None:
        stmt = stmt.where(CloudSave.version == expected_version)
    deleted = await db.scalar(stmt.returning(CloudSave.version))
    if deleted is None:
        current = await _current_version(db, player_id, slot)
        await db.rollback()
        raise SaveConflict(current)
    await db.commit()


async def list_meta(db: AsyncSession, player_id: uuid.UUID) -> list[CloudSave]:
    rows = await db.scalars(
        select(CloudSave).where(CloudSave.player_id == player_id).order_by(CloudSave.slot)
    )
    return list(rows)


async def get(db: AsyncSession, player_id: uuid.UUID, slot: str) -> CloudSave | None:
    return await db.get(CloudSave, (player_id, slot))
