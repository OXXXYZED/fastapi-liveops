import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, Request, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import rate_limit
from app.core.config import Settings
from app.core.security import (
    PlayerClaims,
    constant_time_equals,
    decode_admin_token,
    decode_player_token,
    hash_api_key,
)
from app.models import AdminRole, AdminUser, ApiKey, ApiKeyKind, AuditLog

_bearer = HTTPBearer(auto_error=False)


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_redis(request: Request) -> Redis:
    redis: Redis = request.app.state.redis
    return redis


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
RedisDep = Annotated[Redis, Depends(get_redis)]
DbDep = Annotated[AsyncSession, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


@dataclass(frozen=True, slots=True)
class AdminPrincipal:
    user_id: uuid.UUID | None  # None = static admin token
    email: str
    role: AdminRole


async def authenticate_admin(
    db: DbDep,
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> AdminPrincipal:
    if creds is None:
        raise _unauthorized("missing admin credentials")
    static = settings.admin_token.get_secret_value()
    if static and constant_time_equals(creds.credentials, static):
        return AdminPrincipal(None, "admin-token", AdminRole.owner)
    try:
        user_id = decode_admin_token(creds.credentials, settings.jwt_secret.get_secret_value())
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("invalid admin token") from exc
    # Looked up on every request so disabling a user or changing a role applies immediately.
    user = await db.get(AdminUser, user_id)
    if user is None or user.disabled:
        raise _unauthorized("invalid admin token")
    return AdminPrincipal(user.id, user.email, user.role)


AdminDep = Annotated[AdminPrincipal, Depends(authenticate_admin)]
_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def require_admin(
    request: Request, principal: AdminDep, db: DbDep
) -> AsyncIterator[AdminPrincipal]:
    """Reads need viewer, writes need editor. Successful writes are recorded in the audit log."""
    write = request.method not in _READ_METHODS
    if write and principal.role.level < AdminRole.editor.level:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "viewer role is read-only")
    yield principal
    if write:
        db.add(
            AuditLog(
                actor_id=principal.user_id,
                actor=principal.email,
                method=request.method,
                path=request.url.path[:500],
                detail=dict(request.path_params) or None,
            )
        )
        await db.commit()


async def require_owner(principal: AdminDep) -> AdminPrincipal:
    if principal.role != AdminRole.owner:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "owner role required")
    return principal


async def _enforce_rate_limit(
    redis: Redis, settings: Settings, response: Response, bucket: str
) -> None:
    allowed, remaining, retry_after = await rate_limit.hit(
        redis, bucket, settings.rate_limit_requests, settings.rate_limit_window_seconds
    )
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_requests)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )


async def require_api_key(
    request: Request,
    response: Response,
    db: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Header()] = None,
) -> ApiKey:
    if not x_api_key:
        raise _unauthorized("missing X-Api-Key")
    key = await db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(x_api_key)))
    if key is None or key.revoked_at is not None:
        raise _unauthorized("invalid api key")

    # Client keys are public (shipped in builds) so limit per key + IP; server keys per key.
    ip = request.client.host if request.client else "unknown"
    bucket = f"k:{key.id}:{ip}" if key.kind == ApiKeyKind.client else f"k:{key.id}"
    await _enforce_rate_limit(redis, settings, response, bucket)
    return key


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]


async def require_player(
    response: Response,
    settings: SettingsDep,
    redis: RedisDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> PlayerClaims:
    if creds is None:
        raise _unauthorized("missing bearer token")
    try:
        claims = decode_player_token(creds.credentials, settings.jwt_secret.get_secret_value())
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("invalid token") from exc
    await _enforce_rate_limit(redis, settings, response, f"p:{claims.player_id}")
    return claims


PlayerDep = Annotated[PlayerClaims, Depends(require_player)]


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
