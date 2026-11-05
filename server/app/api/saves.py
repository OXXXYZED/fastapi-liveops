import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy import select

from app.api.deps import DbDep, PlayerDep, SettingsDep, require_admin
from app.api.schemas import SLOT, SaveMeta, SaveOut, SaveWrite
from app.models import CloudSave, Player
from app.services import saves

client = APIRouter(prefix="/v1/saves", tags=["cloud save"])
admin = APIRouter(
    prefix="/admin/environments/{environment_id}/players/{player_id}/saves",
    tags=["admin: cloud save"],
    dependencies=[Depends(require_admin)],
)

Slot = Annotated[str, Path(pattern=SLOT)]
ExpectedVersion = Annotated[int | None, Query(ge=0)]


def _conflict(exc: saves.SaveConflict) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={"message": "version mismatch", "current_version": exc.current_version},
    )


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "save not found")


def _etag(row: CloudSave) -> str:
    return f'"{row.version}"'


@client.get("", response_model=list[SaveMeta])
async def list_saves(claims: PlayerDep, db: DbDep) -> list[CloudSave]:
    return await saves.list_meta(db, claims.player_id)


@client.get("/{slot}", response_model=SaveOut)
async def get_save(slot: Slot, claims: PlayerDep, db: DbDep, response: Response) -> CloudSave:
    row = await saves.get(db, claims.player_id, slot)
    if row is None:
        raise _not_found()
    response.headers["ETag"] = _etag(row)
    return row


@client.put("/{slot}", response_model=SaveMeta)
async def put_save(
    slot: Slot,
    body: SaveWrite,
    claims: PlayerDep,
    db: DbDep,
    settings: SettingsDep,
    response: Response,
) -> CloudSave:
    if saves.encoded_size(body.data) > settings.cloud_save_max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"save exceeds {settings.cloud_save_max_bytes} bytes",
        )
    try:
        row = await saves.write(
            db,
            claims.player_id,
            slot,
            body.data,
            body.expected_version,
            settings.cloud_save_max_slots,
        )
    except saves.SaveConflict as exc:
        raise _conflict(exc) from exc
    except saves.SlotLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"message": f"slot limit ({settings.cloud_save_max_slots}) reached"},
        ) from exc
    response.headers["ETag"] = _etag(row)
    return row


@client.delete("/{slot}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_save(
    slot: Slot, claims: PlayerDep, db: DbDep, expected_version: ExpectedVersion = None
) -> None:
    try:
        await saves.remove(db, claims.player_id, slot, expected_version)
    except saves.SaveConflict as exc:
        if exc.current_version == 0:
            raise _not_found() from exc
        raise _conflict(exc) from exc


# --- admin (support tooling) ---


async def _player(db: DbDep, environment_id: uuid.UUID, player_id: uuid.UUID) -> None:
    found = await db.scalar(
        select(Player.id).where(Player.id == player_id, Player.environment_id == environment_id)
    )
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")


@admin.get("", response_model=list[SaveMeta])
async def admin_list(environment_id: uuid.UUID, player_id: uuid.UUID, db: DbDep) -> list[CloudSave]:
    await _player(db, environment_id, player_id)
    return await saves.list_meta(db, player_id)


@admin.get("/{slot}", response_model=SaveOut)
async def admin_get(
    environment_id: uuid.UUID, player_id: uuid.UUID, slot: Slot, db: DbDep
) -> CloudSave:
    await _player(db, environment_id, player_id)
    row = await saves.get(db, player_id, slot)
    if row is None:
        raise _not_found()
    return row


@admin.delete("/{slot}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete(
    environment_id: uuid.UUID, player_id: uuid.UUID, slot: Slot, db: DbDep
) -> None:
    await _player(db, environment_id, player_id)
    try:
        await saves.remove(db, player_id, slot, None)
    except saves.SaveConflict as exc:
        raise _not_found() from exc
