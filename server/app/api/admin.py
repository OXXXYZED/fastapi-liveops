import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbDep, require_admin, require_owner, utcnow
from app.api.schemas import (
    AdminPlayerOut,
    AdminPlayerPage,
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    AuthProviders,
    EnvironmentCreate,
    EnvironmentOut,
    ProjectCreate,
    ProjectOut,
)
from app.core.security import generate_api_key
from app.models import ApiKey, Environment, Player, Project

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


async def _get_project(db: DbDep, project_id: uuid.UUID) -> Project:
    project = await db.scalar(
        select(Project).where(Project.id == project_id).options(selectinload(Project.environments))
    )
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    return project


async def _get_environment(db: DbDep, environment_id: uuid.UUID) -> Environment:
    env = await db.get(Environment, environment_id)
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "environment not found")
    return env


@router.post("/projects", status_code=status.HTTP_201_CREATED, response_model=ProjectOut)
async def create_project(body: ProjectCreate, db: DbDep) -> Project:
    if len(set(body.environments)) != len(body.environments):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate environment names")
    project = Project(slug=body.slug, name=body.name)
    project.environments = [Environment(name=n) for n in body.environments]
    db.add(project)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "project slug already exists") from exc
    return await _get_project(db, project.id)


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(db: DbDep) -> list[Project]:
    rows = await db.scalars(
        select(Project).options(selectinload(Project.environments)).order_by(Project.created_at)
    )
    return list(rows)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: uuid.UUID, db: DbDep) -> Project:
    return await _get_project(db, project_id)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_owner)],
)
async def delete_project(project_id: uuid.UUID, db: DbDep) -> None:
    await db.delete(await _get_project(db, project_id))
    await db.commit()


@router.post(
    "/projects/{project_id}/environments",
    status_code=status.HTTP_201_CREATED,
    response_model=EnvironmentOut,
)
async def create_environment(
    project_id: uuid.UUID, body: EnvironmentCreate, db: DbDep
) -> Environment:
    await _get_project(db, project_id)
    env = Environment(project_id=project_id, name=body.name)
    db.add(env)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "environment already exists") from exc
    return env


@router.post(
    "/environments/{environment_id}/api-keys",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiKeyCreated,
)
async def create_api_key(environment_id: uuid.UUID, body: ApiKeyCreate, db: DbDep) -> ApiKeyCreated:
    await _get_environment(db, environment_id)
    plaintext, prefix, key_hash = generate_api_key(body.kind)
    key = ApiKey(
        environment_id=environment_id,
        kind=body.kind,
        name=body.name,
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreated.model_validate(
        {**ApiKeyOut.model_validate(key).model_dump(), "key": plaintext}
    )


@router.get("/environments/{environment_id}/api-keys", response_model=list[ApiKeyOut])
async def list_api_keys(environment_id: uuid.UUID, db: DbDep) -> list[ApiKey]:
    await _get_environment(db, environment_id)
    rows = await db.scalars(
        select(ApiKey).where(ApiKey.environment_id == environment_id).order_by(ApiKey.created_at)
    )
    return list(rows)


@router.delete("/api-keys/{key_id}", response_model=ApiKeyOut)
async def revoke_api_key(key_id: uuid.UUID, db: DbDep) -> ApiKey:
    key = await db.get(ApiKey, key_id)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "api key not found")
    if key.revoked_at is None:
        key.revoked_at = utcnow()
        await db.commit()
    return key


@router.get("/environments/{environment_id}/players", response_model=AdminPlayerPage)
async def list_players(
    environment_id: uuid.UUID,
    db: DbDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    q: Annotated[str | None, Query(max_length=128, description="player id or device id")] = None,
) -> AdminPlayerPage:
    await _get_environment(db, environment_id)
    where = [Player.environment_id == environment_id]
    if q:
        try:
            where.append(Player.id == uuid.UUID(q))
        except ValueError:
            where.append(Player.device_id == q)
    total = await db.scalar(select(func.count()).select_from(Player).where(*where))
    rows = await db.scalars(
        select(Player).where(*where).order_by(Player.created_at.desc()).offset(offset).limit(limit)
    )
    return AdminPlayerPage(
        total=total or 0, players=[AdminPlayerOut.model_validate(p) for p in rows]
    )


def _masked(raw: dict[str, Any] | None) -> AuthProviders:
    cfg = AuthProviders.model_validate(raw or {})
    if cfg.steam.web_api_key:
        cfg.steam.web_api_key = "••••" + cfg.steam.web_api_key[-4:]
    return cfg


@router.get("/environments/{environment_id}/auth-providers", response_model=AuthProviders)
async def get_auth_providers(environment_id: uuid.UUID, db: DbDep) -> AuthProviders:
    return _masked((await _get_environment(db, environment_id)).auth_providers)


@router.put("/environments/{environment_id}/auth-providers", response_model=AuthProviders)
async def put_auth_providers(
    environment_id: uuid.UUID, body: AuthProviders, db: DbDep
) -> AuthProviders:
    """Replaces provider config. Omit `steam.web_api_key` (or send the masked value) to keep it."""
    env = await _get_environment(db, environment_id)
    old_key = AuthProviders.model_validate(env.auth_providers or {}).steam.web_api_key
    new = body.model_dump()
    key = body.steam.web_api_key
    if key is None or key.startswith("••••"):
        new["steam"]["web_api_key"] = old_key
    env.auth_providers = new
    await db.commit()
    return _masked(env.auth_providers)
