import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    AdminDep,
    DbDep,
    RedisDep,
    SettingsDep,
    require_admin,
    require_owner,
    utcnow,
)
from app.api.schemas import (
    AdminLogin,
    AdminMe,
    AdminSession,
    AdminUserCreate,
    AdminUserOut,
    AdminUserUpdate,
    AuditEntryOut,
    AuditPage,
)
from app.core import rate_limit
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    issue_admin_token,
    verify_password,
)
from app.models import AdminRole, AdminUser, AuditLog

auth = APIRouter(prefix="/admin/auth", tags=["admin: auth"])
users = APIRouter(
    prefix="/admin",
    tags=["admin: users"],
    dependencies=[Depends(require_owner), Depends(require_admin)],
)

_LOGIN_ATTEMPTS = 10
_LOGIN_WINDOW = 300


@auth.post("/login", response_model=AdminSession)
async def login(
    body: AdminLogin, request: Request, db: DbDep, redis: RedisDep, settings: SettingsDep
) -> AdminSession:
    email = body.email.strip().lower()
    ip = request.client.host if request.client else "unknown"
    for bucket in (f"login-ip:{ip}", f"login-email:{email}"):
        allowed, _, retry = await rate_limit.hit(redis, bucket, _LOGIN_ATTEMPTS, _LOGIN_WINDOW)
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many login attempts",
                headers={"Retry-After": str(retry)},
            )
    user = await db.scalar(select(AdminUser).where(AdminUser.email == email))
    ok = verify_password(body.password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not ok or user.disabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")
    user.last_login_at = utcnow()
    await db.commit()
    ttl = settings.admin_session_ttl_seconds
    return AdminSession(
        access_token=issue_admin_token(user.id, settings.jwt_secret.get_secret_value(), ttl),
        expires_in=ttl,
        user=AdminMe(id=user.id, email=user.email, role=user.role),
    )


@auth.get("/me", response_model=AdminMe)
async def me(principal: AdminDep) -> AdminMe:
    return AdminMe(id=principal.user_id, email=principal.email, role=principal.role)


async def _owners_left(db: DbDep, excluding: uuid.UUID) -> int:
    n = await db.scalar(
        select(func.count())
        .select_from(AdminUser)
        .where(
            AdminUser.role == AdminRole.owner,
            AdminUser.disabled.is_(False),
            AdminUser.id != excluding,
        )
    )
    return n or 0


@users.get("/users", response_model=list[AdminUserOut])
async def list_users(db: DbDep) -> list[AdminUser]:
    return list(await db.scalars(select(AdminUser).order_by(AdminUser.created_at)))


@users.post("/users", status_code=status.HTTP_201_CREATED, response_model=AdminUserOut)
async def create_user(body: AdminUserCreate, db: DbDep) -> AdminUser:
    user = AdminUser(
        email=body.email.strip().lower(), password_hash=hash_password(body.password), role=body.role
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "email already exists") from exc
    return user


@users.patch("/users/{user_id}", response_model=AdminUserOut)
async def update_user(
    user_id: uuid.UUID, body: AdminUserUpdate, db: DbDep, principal: AdminDep
) -> AdminUser:
    user = await db.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    demoting = (body.role is not None and body.role != AdminRole.owner) or body.disabled is True
    if (
        user.role == AdminRole.owner
        and demoting
        and principal.user_id is not None  # the static token can always recover
        and await _owners_left(db, user.id) == 0
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot remove the last owner")
    if body.role is not None:
        user.role = body.role
    if body.disabled is not None:
        user.disabled = body.disabled
    if body.password is not None:
        user.password_hash = hash_password(body.password)
    await db.commit()
    return user


@users.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, db: DbDep, principal: AdminDep) -> None:
    user = await db.get(AdminUser, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if user.id == principal.user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot delete yourself")
    if (
        user.role == AdminRole.owner
        and principal.user_id is not None
        and await _owners_left(db, user.id) == 0
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "cannot remove the last owner")
    await db.delete(user)
    await db.commit()


@users.get("/audit-log", response_model=AuditPage)
async def audit_log(
    db: DbDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    total = await db.scalar(select(func.count()).select_from(AuditLog))
    rows = await db.scalars(
        select(AuditLog).order_by(AuditLog.at.desc()).offset(offset).limit(limit)
    )
    return AuditPage(total=total or 0, entries=[AuditEntryOut.model_validate(r) for r in rows])
