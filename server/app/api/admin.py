import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.api.deps import DbDep, require_admin, utcnow
from app.api.schemas import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyOut,
    EnvironmentCreate,
    EnvironmentOut,
    ProjectCreate,
    ProjectOut,
)
from app.core.security import generate_api_key
from app.models import ApiKey, Environment, Project

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


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
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
