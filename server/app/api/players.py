import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, status
from sqlalchemy import delete, func, literal_column, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from app.api.deps import ApiKeyDep, DbDep, PlayerDep, SettingsDep
from app.api.schemas import (
    PROVIDER,
    AnonymousLogin,
    IdentityOut,
    PlayerProfileOut,
    PlayerUpdate,
    ProviderLogin,
    TokenOut,
)
from app.core.security import PlayerClaims, issue_player_token
from app.models import Environment, Player, PlayerIdentity
from app.services.identity import (
    IdentityError,
    ProviderNotConfigured,
    VerifiedIdentity,
    Verifier,
)

router = APIRouter(prefix="/v1", tags=["players"])
Provider = Annotated[str, Path(pattern=PROVIDER)]


def _token(
    settings: SettingsDep, player_id: uuid.UUID, env_id: uuid.UUID, created: bool
) -> TokenOut:
    token, ttl = issue_player_token(
        PlayerClaims(player_id, env_id),
        settings.jwt_secret.get_secret_value(),
        settings.jwt_ttl_seconds,
    )
    return TokenOut(player_id=player_id, access_token=token, expires_in=ttl, created=created)


@router.post("/auth/anonymous", response_model=TokenOut)
async def login_anonymous(
    body: AnonymousLogin, api_key: ApiKeyDep, db: DbDep, settings: SettingsDep
) -> TokenOut:
    """Create a player (or resume one by device_id) and issue an access token."""
    env_id = api_key.environment_id
    if body.device_id is None:
        player = Player(environment_id=env_id)
        db.add(player)
        await db.flush()
        player_id, created = player.id, True
    else:
        # Atomic upsert; xmax = 0 means the row was freshly inserted.
        stmt: Any = (
            insert(Player)
            .values(environment_id=env_id, device_id=body.device_id)
            .on_conflict_do_update(
                index_elements=[Player.environment_id, Player.device_id],
                set_={"last_seen_at": func.now()},
            )
            .returning(Player.id, literal_column("xmax = 0"))
        )
        row = (await db.execute(stmt)).one()
        player_id, created = row[0], bool(row[1])
    await db.commit()
    return _token(settings, player_id, env_id, created)


async def _verify(
    request: Request, db: DbDep, env_id: uuid.UUID, provider: str, token: str
) -> VerifiedIdentity:
    env = await db.get(Environment, env_id)
    assert env is not None
    verifier: Verifier = request.app.state.identity_verifiers[provider]
    try:
        return await verifier.verify(token, (env.auth_providers or {}).get(provider) or {})
    except ProviderNotConfigured as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{provider} sign-in is not configured"
        ) from exc
    except IdentityError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"invalid {provider} token") from exc


async def _find(db: DbDep, env_id: uuid.UUID, ident: VerifiedIdentity) -> PlayerIdentity | None:
    return await db.scalar(
        select(PlayerIdentity).where(
            PlayerIdentity.environment_id == env_id,
            PlayerIdentity.provider == ident.provider,
            PlayerIdentity.subject == ident.subject,
        )
    )


@router.post("/auth/{provider}", response_model=TokenOut)
async def login_provider(
    provider: Provider,
    body: ProviderLogin,
    request: Request,
    api_key: ApiKeyDep,
    db: DbDep,
    settings: SettingsDep,
) -> TokenOut:
    """Sign in with Google / Apple / Steam. Creates a player on first sign-in."""
    env_id = api_key.environment_id
    ident = await _verify(request, db, env_id, provider, body.token)
    for _ in range(2):
        existing = await _find(db, env_id, ident)
        if existing is not None:
            await db.execute(
                update(Player)
                .where(Player.id == existing.player_id)
                .values(last_seen_at=func.now())
            )
            await db.commit()
            return _token(settings, existing.player_id, env_id, created=False)
        player = Player(environment_id=env_id)
        db.add(player)
        await db.flush()
        db.add(
            PlayerIdentity(
                environment_id=env_id,
                player_id=player.id,
                provider=ident.provider,
                subject=ident.subject,
                email=ident.email,
            )
        )
        try:
            await db.commit()
            return _token(settings, player.id, env_id, created=True)
        except IntegrityError:  # concurrent first sign-in won the race; use its player
            await db.rollback()
    raise HTTPException(status.HTTP_409_CONFLICT, "concurrent sign-in, retry")


async def _profile(db: DbDep, claims: PlayerClaims) -> PlayerProfileOut:
    player = await db.scalar(
        select(Player).where(
            Player.id == claims.player_id, Player.environment_id == claims.environment_id
        )
    )
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")
    idents = await db.scalars(
        select(PlayerIdentity)
        .where(PlayerIdentity.player_id == player.id)
        .order_by(PlayerIdentity.provider)
    )
    return PlayerProfileOut(
        id=player.id,
        environment_id=player.environment_id,
        created_at=player.created_at,
        last_seen_at=player.last_seen_at,
        display_name=player.display_name,
        identities=[IdentityOut.model_validate(i) for i in idents],
    )


@router.get("/players/me", response_model=PlayerProfileOut)
async def get_me(claims: PlayerDep, db: DbDep) -> PlayerProfileOut:
    await db.execute(
        update(Player)
        .where(Player.id == claims.player_id, Player.environment_id == claims.environment_id)
        .values(last_seen_at=func.now())
    )
    await db.commit()
    return await _profile(db, claims)


@router.patch("/players/me", response_model=PlayerProfileOut)
async def update_me(body: PlayerUpdate, claims: PlayerDep, db: DbDep) -> PlayerProfileOut:
    if "display_name" in body.model_fields_set:
        name = " ".join(body.display_name.split()) if body.display_name else None
        await db.execute(
            update(Player)
            .where(Player.id == claims.player_id, Player.environment_id == claims.environment_id)
            .values(display_name=name)
        )
        await db.commit()
    return await _profile(db, claims)


@router.post("/players/me/identities/{provider}", response_model=PlayerProfileOut)
async def link_identity(
    provider: Provider, body: ProviderLogin, request: Request, claims: PlayerDep, db: DbDep
) -> PlayerProfileOut:
    """Link a Google / Apple / Steam account so the player can sign in on other devices."""
    ident = await _verify(request, db, claims.environment_id, provider, body.token)
    existing = await _find(db, claims.environment_id, ident)
    if existing is not None:
        if existing.player_id != claims.player_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                {"message": "account already linked to another player", "code": "identity_in_use"},
            )
        return await _profile(db, claims)
    db.add(
        PlayerIdentity(
            environment_id=claims.environment_id,
            player_id=claims.player_id,
            provider=ident.provider,
            subject=ident.subject,
            email=ident.email,
        )
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {
                "message": f"a different {provider} account is already linked",
                "code": "provider_linked",
            },
        ) from exc
    return await _profile(db, claims)


@router.delete("/players/me/identities/{provider}", response_model=PlayerProfileOut)
async def unlink_identity(provider: Provider, claims: PlayerDep, db: DbDep) -> PlayerProfileOut:
    deleted = await db.scalar(
        delete(PlayerIdentity)
        .where(PlayerIdentity.player_id == claims.player_id, PlayerIdentity.provider == provider)
        .returning(PlayerIdentity.id)
    )
    if deleted is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no {provider} account linked")
    await db.commit()
    return await _profile(db, claims)
