"""Leaderboards: Postgres is the source of truth, Redis ZSETs serve rank queries."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Leaderboard, LeaderboardScore, ResetSchedule, SortOrder, UpdateMode
from app.services import events

# ZSET scores are IEEE doubles: integers are exact only up to 2^53.
MAX_ABS_SCORE = 2**53
_SEASON_TTL = {
    ResetSchedule.daily: timedelta(days=8),
    ResetSchedule.weekly: timedelta(weeks=5),
    ResetSchedule.monthly: timedelta(days=95),
}


def season_id(schedule: ResetSchedule, now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    match schedule:
        case ResetSchedule.never:
            return "all"
        case ResetSchedule.daily:
            return now.strftime("%Y-%m-%d")
        case ResetSchedule.weekly:
            year, week, _ = now.isocalendar()
            return f"{year}-W{week:02d}"
        case ResetSchedule.monthly:
            return now.strftime("%Y-%m")
    raise AssertionError(schedule)  # pragma: no cover


def previous_season_id(schedule: ResetSchedule, now: datetime | None = None) -> str:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    match schedule:
        case ResetSchedule.never:
            return "all"
        case ResetSchedule.daily:
            return season_id(schedule, now - timedelta(days=1))
        case ResetSchedule.weekly:
            return season_id(schedule, now - timedelta(weeks=1))
        case ResetSchedule.monthly:
            return season_id(schedule, now.replace(day=1) - timedelta(days=1))
    raise AssertionError(schedule)  # pragma: no cover


def zkey(board: Leaderboard, season: str) -> str:
    return f"lb:{board.id}:{season}"


@dataclass(frozen=True, slots=True)
class Entry:
    rank: int  # 1-based
    player_id: uuid.UUID
    score: int


async def submit(
    db: AsyncSession, redis: Redis, board: Leaderboard, player_id: uuid.UUID, score: int
) -> tuple[str, int]:
    """Apply a score per the board's update mode. Returns (season, stored score).

    The Postgres upsert row lock is held while Redis is updated, so concurrent
    submissions for the same player are applied to Redis in commit order.
    """
    season = season_id(board.reset_schedule)
    col = LeaderboardScore.score
    stmt = insert(LeaderboardScore).values(
        leaderboard_id=board.id, season=season, player_id=player_id, score=score
    )
    new = stmt.excluded.score
    if board.update_mode == UpdateMode.sum:
        merged = col + new
    elif board.update_mode == UpdateMode.latest:
        merged = new
    elif board.sort_order == SortOrder.desc:
        merged = func.greatest(col, new)
    else:
        merged = func.least(col, new)
    changed = case((merged != col, func.now()), else_=LeaderboardScore.updated_at)
    upsert = stmt.on_conflict_do_update(
        index_elements=["leaderboard_id", "season", "player_id"],
        set_={"score": merged, "updated_at": changed},
    ).returning(LeaderboardScore.score)
    stored = int((await db.execute(upsert)).scalar_one())
    await ensure_index(db, redis, board, season)  # don't seed a fresh ZSET with one member
    key = zkey(board, season)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.zadd(key, {str(player_id): stored})
        if ttl := _SEASON_TTL.get(board.reset_schedule):
            pipe.expire(key, int(ttl.total_seconds()))
        await pipe.execute()
    await events.emit(
        db,
        board.environment_id,
        "leaderboard.score",
        {
            "leaderboard": board.key,
            "season": season,
            "player_id": str(player_id),
            "submitted": score,
            "score": stored,
        },
    )
    await db.commit()
    return season, stored


async def ensure_index(db: AsyncSession, redis: Redis, board: Leaderboard, season: str) -> None:
    """Rebuild the Redis ZSET from Postgres if it is missing (e.g. Redis was flushed)."""
    key = zkey(board, season)
    if await redis.exists(key):
        return
    rows = (
        await db.execute(
            select(LeaderboardScore.player_id, LeaderboardScore.score).where(
                LeaderboardScore.leaderboard_id == board.id, LeaderboardScore.season == season
            )
        )
    ).all()
    if not rows:
        return
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(key)
        for i in range(0, len(rows), 5000):
            pipe.zadd(key, {str(pid): s for pid, s in rows[i : i + 5000]})
        if ttl := _SEASON_TTL.get(board.reset_schedule):
            pipe.expire(key, int(ttl.total_seconds()))
        await pipe.execute()


def _desc(board: Leaderboard) -> bool:
    return board.sort_order == SortOrder.desc


async def top(
    redis: Redis, board: Leaderboard, season: str, offset: int, limit: int
) -> list[Entry]:
    raw: list[tuple[str, float]] = await redis.zrange(  # type: ignore[assignment]
        zkey(board, season), offset, offset + limit - 1, desc=_desc(board), withscores=True
    )
    return [Entry(offset + i + 1, uuid.UUID(m), int(s)) for i, (m, s) in enumerate(raw)]


async def rank_of(
    redis: Redis, board: Leaderboard, season: str, player_id: uuid.UUID
) -> int | None:
    key, member = zkey(board, season), str(player_id)
    r: int | None = await (  # type: ignore[assignment]  # list only with withscore=True
        redis.zrevrank(key, member) if _desc(board) else redis.zrank(key, member)
    )
    return None if r is None else r + 1


async def around(
    redis: Redis, board: Leaderboard, season: str, player_id: uuid.UUID, radius: int
) -> list[Entry]:
    rank = await rank_of(redis, board, season, player_id)
    if rank is None:
        return []
    start = max(0, rank - 1 - radius)
    return await top(redis, board, season, start, radius * 2 + 1)


async def count(redis: Redis, board: Leaderboard, season: str) -> int:
    return int(await redis.zcard(zkey(board, season)))


async def remove_player(
    db: AsyncSession, redis: Redis, board: Leaderboard, season: str, player_id: uuid.UUID
) -> bool:
    row = await db.get(LeaderboardScore, (board.id, season, player_id))
    if row is None:
        return False
    await db.delete(row)
    await redis.zrem(zkey(board, season), str(player_id))
    await db.commit()
    return True
