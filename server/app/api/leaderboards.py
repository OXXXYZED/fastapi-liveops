import json
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
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
    ValidationSecret,
)
from app.core import rate_limit
from app.models import Environment, Leaderboard, Player
from app.services import leaderboards as lb
from app.services import score_validation
from app.services.score_validation import MAX_METADATA_BYTES

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


def _in_bounds(board: Leaderboard, score: int) -> bool:
    return (board.min_score is None or score >= board.min_score) and (
        board.max_score is None or score <= board.max_score
    )


def _validate_bounds(lo: int | None, hi: int | None) -> None:
    if lo is not None and hi is not None and lo > hi:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "min_score > max_score")


async def _named(db: DbDep, entries: list[lb.Entry]) -> list[EntryOut]:
    ids = [e.player_id for e in entries]
    names: dict[uuid.UUID, str | None] = {}
    if ids:
        rows = await db.execute(select(Player.id, Player.display_name).where(Player.id.in_(ids)))
        names = {pid: name for pid, name in rows.all()}
    return [
        EntryOut(
            rank=e.rank, player_id=e.player_id, score=e.score, display_name=names.get(e.player_id)
        )
        for e in entries
    ]


def _new_secret() -> str:
    return "vsec_" + secrets.token_urlsafe(32)


# --- admin ---


@admin.post("", status_code=status.HTTP_201_CREATED, response_model=LeaderboardOut)
async def create_board(
    environment_id: uuid.UUID, body: LeaderboardCreate, db: DbDep
) -> Leaderboard:
    if await db.get(Environment, environment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "environment not found")
    _validate_bounds(body.min_score, body.max_score)
    board = Leaderboard(environment_id=environment_id, **body.model_dump())
    if board.validation_url:
        board.validation_secret = _new_secret()
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
    if board.validation_url and not board.validation_secret:
        board.validation_secret = _new_secret()
    await db.commit()
    return board


@admin.post("/{key}/validation-secret", response_model=ValidationSecret)
async def rotate_validation_secret(
    environment_id: uuid.UUID, key: BoardKey, db: DbDep
) -> ValidationSecret:
    """Generate a new signing secret for the validation hook and return it (shown only here)."""
    board = await _board(db, environment_id, key)
    board.validation_secret = _new_secret()
    await db.commit()
    return ValidationSecret(secret=board.validation_secret)


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
    key: BoardKey,
    body: ScoreSubmit,
    request: Request,
    claims: PlayerDep,
    db: DbDep,
    redis: RedisDep,
) -> ScoreSubmitted:
    board = await _board(db, claims.environment_id, key)
    if body.metadata is not None and len(json.dumps(body.metadata)) > MAX_METADATA_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "metadata too large")
    if board.max_submits_per_minute:
        allowed, _, retry = await rate_limit.hit(
            redis, f"lb:{board.id}:{claims.player_id}", board.max_submits_per_minute, 60
        )
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many score submissions for this leaderboard",
                headers={"Retry-After": str(retry)},
            )
    if not _in_bounds(board, body.score):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "score out of allowed range")
    score = body.score
    if board.validation_url:
        verdict = await score_validation.validate(
            request.app.state.http,
            board,
            claims.player_id,
            score,
            lb.season_id(board.reset_schedule),
            body.metadata,
        )
        if not verdict.allow:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                {"message": "score rejected", "reason": verdict.reason},
            )
        score = verdict.score
        if not _in_bounds(board, score):  # validator adjustments must respect bounds too
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "score out of allowed range")
    season, stored = await lb.submit(db, redis, board, claims.player_id, score)
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
        entries=await _named(db, entries),
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
    entries = await _named(db, await lb.around(redis, board, sid, claims.player_id, radius))
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
        entries=await _named(db, entries),
    )
