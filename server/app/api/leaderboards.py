import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DbDep, PlayerDep, RedisDep, require_admin
from app.api.schemas import (
    FLAG_KEY,
    EntryOut,
    LeaderboardCreate,
    LeaderboardOut,
    LeaderboardPage,
    LeaderboardUpdate,
    MyRank,
    ScoreSubmit,
    ScoreSubmitted,
)
from app.models import Environment, Leaderboard
from app.services import leaderboards as lb

admin = APIRouter(
    prefix="/admin/environments/{environment_id}/leaderboards",
    tags=["admin: leaderboards"],
    dependencies=[Depends(require_admin)],
)
client = APIRouter(prefix="/v1/leaderboards", tags=["leaderboards"])

BoardKey = Annotated[str, Path(pattern=FLAG_KEY)]
SeasonQ = Annotated[
    str,
    Query(
        pattern=r"^(current|previous|all|\d{4}-(\d{2}(-\d{2})?|W\d{2}))$",
        description="`current`, `previous`, or an explicit season id like `2026-W45`",
    ),
]


async def _board(db: DbDep, environment_id: uuid.UUID, key: str) -> Leaderboard:
    board = await db.scalar(
        select(Leaderboard).where(
            Leaderboard.environment_id == environment_id, Leaderboard.key == key
        )
    )
    if board is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "leaderboard not found")
    return board


def _season(board: Leaderboard, season: str) -> str:
    if season == "current":
        return lb.season_id(board.reset_schedule)
    if season == "previous":
        return lb.previous_season_id(board.reset_schedule)
    return season


def _validate_bounds(lo: int | None, hi: int | None) -> None:
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "min_score > max_score")


# --- admin ---


@admin.post("", status_code=status.HTTP_201_CREATED, response_model=LeaderboardOut)
async def create_board(
    environment_id: uuid.UUID, body: LeaderboardCreate, db: DbDep
) -> Leaderboard:
    if await db.get(Environment, environment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "environment not found")
    _validate_bounds(body.min_score, body.max_score)
    board = Leaderboard(environment_id=environment_id, **body.model_dump())
    db.add(board)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "leaderboard key already exists") from exc
    return board


@admin.get("", response_model=list[LeaderboardOut])
async def list_boards(environment_id: uuid.UUID, db: DbDep) -> list[Leaderboard]:
    rows = await db.scalars(
        select(Leaderboard)
        .where(Leaderboard.environment_id == environment_id)
        .order_by(Leaderboard.key)
    )
    return list(rows)


@admin.patch("/{key}", response_model=LeaderboardOut)
async def update_board(
    environment_id: uuid.UUID, key: BoardKey, body: LeaderboardUpdate, db: DbDep
) -> Leaderboard:
    """Sort order, update mode and reset schedule are immutable: they'd corrupt existing data."""
    board = await _board(db, environment_id, key)
    patch = body.model_dump(exclude_unset=True)
    _validate_bounds(
        patch.get("min_score", board.min_score), patch.get("max_score", board.max_score)
    )
    for k, v in patch.items():
        setattr(board, k, v)
    await db.commit()
    return board


@admin.delete("/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_board(
    environment_id: uuid.UUID, key: BoardKey, db: DbDep, redis: RedisDep
) -> None:
    board = await _board(db, environment_id, key)
    keys = [k async for k in redis.scan_iter(match=f"lb:{board.id}:*")]
    await db.delete(board)
    await db.commit()
    if keys:
        await redis.delete(*keys)


@admin.delete("/{key}/scores/{player_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_score(
    environment_id: uuid.UUID,
    key: BoardKey,
    player_id: uuid.UUID,
    db: DbDep,
    redis: RedisDep,
    season: SeasonQ = "current",
) -> None:
    """Remove a player's entry (e.g. a cheater) from a season."""
    board = await _board(db, environment_id, key)
    if not await lb.remove_player(db, redis, board, _season(board, season), player_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "score not found")


# --- client ---


@client.post("/{key}/scores", response_model=ScoreSubmitted)
async def submit_score(
    key: BoardKey, body: ScoreSubmit, claims: PlayerDep, db: DbDep, redis: RedisDep
) -> ScoreSubmitted:
    board = await _board(db, claims.environment_id, key)
    if (board.min_score is not None and body.score < board.min_score) or (
        board.max_score is not None and body.score > board.max_score
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "score out of allowed range")
    season, stored = await lb.submit(db, redis, board, claims.player_id, body.score)
    rank = await lb.rank_of(redis, board, season, claims.player_id)
    assert rank is not None
    return ScoreSubmitted(season=season, score=stored, rank=rank)


@client.get("/{key}", response_model=LeaderboardPage)
async def get_top(
    key: BoardKey,
    claims: PlayerDep,
    db: DbDep,
    redis: RedisDep,
    season: SeasonQ = "current",
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 10,
) -> LeaderboardPage:
    board = await _board(db, claims.environment_id, key)
    sid = _season(board, season)
    await lb.ensure_index(db, redis, board, sid)
    entries = await lb.top(redis, board, sid, offset, limit)
    return LeaderboardPage(
        key=key,
        season=sid,
        total=await lb.count(redis, board, sid),
        entries=[EntryOut(rank=e.rank, player_id=e.player_id, score=e.score) for e in entries],
    )


@client.get("/{key}/me", response_model=MyRank)
async def get_me(
    key: BoardKey,
    claims: PlayerDep,
    db: DbDep,
    redis: RedisDep,
    season: SeasonQ = "current",
    radius: Annotated[int, Query(ge=0, le=25)] = 5,
) -> MyRank:
    board = await _board(db, claims.environment_id, key)
    sid = _season(board, season)
    await lb.ensure_index(db, redis, board, sid)
    entries = [
        EntryOut(rank=e.rank, player_id=e.player_id, score=e.score)
        for e in await lb.around(redis, board, sid, claims.player_id, radius)
    ]
    me = next((e for e in entries if e.player_id == claims.player_id), None)
    return MyRank(
        key=key, season=sid, total=await lb.count(redis, board, sid), me=me, around=entries
    )


@admin.get("/{key}/entries", response_model=LeaderboardPage)
async def admin_entries(
    environment_id: uuid.UUID,
    key: BoardKey,
    db: DbDep,
    redis: RedisDep,
    season: SeasonQ = "current",
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> LeaderboardPage:
    board = await _board(db, environment_id, key)
    sid = _season(board, season)
    await lb.ensure_index(db, redis, board, sid)
    entries = await lb.top(redis, board, sid, offset, limit)
    return LeaderboardPage(
        key=key,
        season=sid,
        total=await lb.count(redis, board, sid),
        entries=[EntryOut(rank=e.rank, player_id=e.player_id, score=e.score) for e in entries],
    )
