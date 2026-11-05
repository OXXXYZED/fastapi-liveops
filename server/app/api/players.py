from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, literal_column, update
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import ApiKeyDep, DbDep, PlayerDep, SettingsDep
from app.api.schemas import AnonymousLogin, PlayerOut, TokenOut
from app.core.security import PlayerClaims, issue_player_token
from app.models import Player

router = APIRouter(prefix="/v1", tags=["players"])


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

    token, ttl = issue_player_token(
        PlayerClaims(player_id, env_id),
        settings.jwt_secret.get_secret_value(),
        settings.jwt_ttl_seconds,
    )
    return TokenOut(player_id=player_id, access_token=token, expires_in=ttl, created=created)


@router.get("/players/me", response_model=PlayerOut)
async def get_me(claims: PlayerDep, db: DbDep) -> Player:
    player: Player | None = await db.scalar(
        update(Player)
        .where(Player.id == claims.player_id, Player.environment_id == claims.environment_id)
        .values(last_seen_at=func.now())
        .returning(Player)
    )
    if player is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "player not found")
    await db.commit()
    return player
