import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.core import TimestampMixin, _uuid


class SortOrder(enum.StrEnum):
    desc = "desc"  # higher is better (points)
    asc = "asc"  # lower is better (time)


class UpdateMode(enum.StrEnum):
    best = "best"  # keep the best score according to sort order
    latest = "latest"  # always overwrite
    sum = "sum"  # accumulate


class ResetSchedule(enum.StrEnum):
    never = "never"
    daily = "daily"
    weekly = "weekly"
    monthly = "monthly"


class Leaderboard(TimestampMixin, Base):
    __tablename__ = "leaderboards"
    __table_args__ = (UniqueConstraint("environment_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(200))
    sort_order: Mapped[SortOrder] = mapped_column(Enum(SortOrder, name="lb_sort_order"))
    update_mode: Mapped[UpdateMode] = mapped_column(Enum(UpdateMode, name="lb_update_mode"))
    reset_schedule: Mapped[ResetSchedule] = mapped_column(
        Enum(ResetSchedule, name="lb_reset_schedule")
    )
    # Server-side bounds for submitted scores (basic anti-cheat).
    min_score: Mapped[int | None] = mapped_column(BigInteger)
    max_score: Mapped[int | None] = mapped_column(BigInteger)


class LeaderboardScore(Base):
    """Durable source of truth. Redis sorted sets are a rebuildable index on top of this."""

    __tablename__ = "leaderboard_scores"
    __table_args__ = (Index("ix_lb_scores_board_season", "leaderboard_id", "season"),)

    leaderboard_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leaderboards.id", ondelete="CASCADE"), primary_key=True
    )
    season: Mapped[str] = mapped_column(String(16), primary_key=True)
    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    score: Mapped[int] = mapped_column(BigInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
