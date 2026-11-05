import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import AdminDep, DbDep, RedisDep, require_admin
from app.api.schemas import (
    FLAG_KEY,
    ConfigPublish,
    ConfigVersionOut,
    ConfigVersionSummary,
    FlagOut,
    FlagUpsert,
)
from app.models import ConfigVersion, Environment, FeatureFlag
from app.services import remote_config

router = APIRouter(
    prefix="/admin/environments/{environment_id}",
    tags=["admin: remote config"],
    dependencies=[Depends(require_admin)],
)

FlagKey = Path(pattern=FLAG_KEY)


async def _env(db: DbDep, environment_id: uuid.UUID) -> Environment:
    env = await db.get(Environment, environment_id)
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "environment not found")
    return env


async def _publish(
    db: DbDep, redis: RedisDep, environment_id: uuid.UUID, body: ConfigPublish, actor: str
) -> ConfigVersion:
    try:
        return await remote_config.publish(
            db, redis, environment_id, body.values, body.note, body.base_version, actor
        )
    except remote_config.PublishConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, exc.message) from exc


@router.get("/config", response_model=ConfigVersionOut)
async def get_live_config(environment_id: uuid.UUID, db: DbDep) -> ConfigVersion:
    await _env(db, environment_id)
    row = await db.scalar(
        select(ConfigVersion)
        .where(ConfigVersion.environment_id == environment_id)
        .order_by(ConfigVersion.version.desc())
        .limit(1)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no config published yet")
    return row


@router.post("/config", status_code=status.HTTP_201_CREATED, response_model=ConfigVersionOut)
async def publish_config(
    environment_id: uuid.UUID, body: ConfigPublish, db: DbDep, redis: RedisDep, admin: AdminDep
) -> ConfigVersion:
    await _env(db, environment_id)
    return await _publish(db, redis, environment_id, body, admin.email)


@router.get("/config/versions", response_model=list[ConfigVersionSummary])
async def list_versions(environment_id: uuid.UUID, db: DbDep) -> list[ConfigVersion]:
    await _env(db, environment_id)
    rows = await db.scalars(
        select(ConfigVersion)
        .where(ConfigVersion.environment_id == environment_id)
        .order_by(ConfigVersion.version.desc())
        .limit(100)
    )
    return list(rows)


@router.get("/config/versions/{version}", response_model=ConfigVersionOut)
async def get_version(environment_id: uuid.UUID, version: int, db: DbDep) -> ConfigVersion:
    row = await db.scalar(
        select(ConfigVersion).where(
            ConfigVersion.environment_id == environment_id, ConfigVersion.version == version
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "version not found")
    return row


@router.post(
    "/config/versions/{version}/rollback",
    status_code=status.HTTP_201_CREATED,
    response_model=ConfigVersionOut,
)
async def rollback(
    environment_id: uuid.UUID, version: int, db: DbDep, redis: RedisDep, admin: AdminDep
) -> ConfigVersion:
    """Publishes a copy of an old version as a new version (history stays append-only)."""
    old = await get_version(environment_id, version, db)
    body = ConfigPublish(values=old.values, note=f"rollback to v{version}")
    return await _publish(db, redis, environment_id, body, admin.email)


@router.get("/flags", response_model=list[FlagOut])
async def list_flags(environment_id: uuid.UUID, db: DbDep) -> list[FeatureFlag]:
    await _env(db, environment_id)
    rows = await db.scalars(
        select(FeatureFlag)
        .where(FeatureFlag.environment_id == environment_id)
        .order_by(FeatureFlag.key)
    )
    return list(rows)


@router.put("/flags/{key}", response_model=FlagOut)
async def upsert_flag(
    environment_id: uuid.UUID,
    body: FlagUpsert,
    db: DbDep,
    redis: RedisDep,
    key: str = FlagKey,
) -> FeatureFlag:
    await _env(db, environment_id)
    data = {
        "description": body.description,
        "enabled": body.enabled,
        "rollout_percent": body.rollout_percent,
        "rules": [c.model_dump(mode="json") for c in body.rules],
    }
    stmt = (
        insert(FeatureFlag)
        .values(environment_id=environment_id, key=key, **data)
        .on_conflict_do_update(
            index_elements=[FeatureFlag.environment_id, FeatureFlag.key],
            set_={**data, "updated_at": func.now()},
        )
        .returning(FeatureFlag)
    )
    flag: FeatureFlag | None = await db.scalar(stmt, execution_options={"populate_existing": True})
    assert flag is not None
    await db.commit()
    await remote_config.invalidate(redis, environment_id)
    return flag


@router.delete("/flags/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_flag(
    environment_id: uuid.UUID, db: DbDep, redis: RedisDep, key: str = FlagKey
) -> None:
    flag = await db.scalar(
        select(FeatureFlag).where(
            FeatureFlag.environment_id == environment_id, FeatureFlag.key == key
        )
    )
    if flag is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "flag not found")
    await db.delete(flag)
    await db.commit()
    await remote_config.invalidate(redis, environment_id)
