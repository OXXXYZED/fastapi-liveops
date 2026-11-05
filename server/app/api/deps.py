import uuid
from collections.abc import AsyncIterator
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
    decode_player_token,
    hash_api_key,
)
from app.models import ApiKey, ApiKeyKind

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


async def require_admin(
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    if creds is None or not constant_time_equals(
        creds.credentials, settings.admin_token.get_secret_value()
    ):
        raise _unauthorized("invalid admin token")


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
    bucket = f"{key.id}:{ip}" if key.kind == ApiKeyKind.client else str(key.id)
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
    return key


ApiKeyDep = Annotated[ApiKey, Depends(require_api_key)]


async def require_player(
    settings: SettingsDep,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> PlayerClaims:
    if creds is None:
        raise _unauthorized("missing bearer token")
    try:
        return decode_player_token(creds.credentials, settings.jwt_secret.get_secret_value())
    except jwt.InvalidTokenError as exc:
        raise _unauthorized("invalid token") from exc


PlayerDep = Annotated[PlayerClaims, Depends(require_player)]


def utcnow() -> datetime:
    return datetime.now(UTC)


def parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from exc
