import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base
from app.models.core import TimestampMixin, _uuid


class ScheduleStatus(enum.StrEnum):
    pending = "pending"
    published = "published"
    cancelled = "cancelled"
    failed = "failed"


class ConfigSchedule(TimestampMixin, Base):
    """A config version to publish automatically at `publish_at`."""

    __tablename__ = "config_schedules"
    __table_args__ = (Index("ix_config_schedules_due", "status", "publish_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(String(500))
    publish_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[ScheduleStatus] = mapped_column(
        Enum(ScheduleStatus, name="schedule_status"), default=ScheduleStatus.pending
    )
    published_version: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str] = mapped_column(String(320))


class ExperimentStatus(enum.StrEnum):
    draft = "draft"
    running = "running"
    stopped = "stopped"


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("environment_id", "key"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[ExperimentStatus] = mapped_column(
        Enum(ExperimentStatus, name="experiment_status"), default=ExperimentStatus.draft
    )
    # % of matching players enrolled; the rest get no variant and plain config.
    traffic_percent: Mapped[int] = mapped_column(Integer, default=100)
    rules: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    # [{"key": "control", "weight": 50, "overrides": {...}}, ...]
    variants: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentExposure(Base):
    """First assignment of a player to an experiment variant; makes assignments sticky."""

    __tablename__ = "experiment_exposures"

    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True
    )
    player_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), primary_key=True
    )
    variant: Mapped[str] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Webhook(TimestampMixin, Base):
    __tablename__ = "webhooks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    environment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(2000))
    secret: Mapped[str] = mapped_column(String(128))
    # Event types, or ["*"] for all.
    events: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DeliveryStatus(enum.StrEnum):
    pending = "pending"
    delivered = "delivered"
    failed = "failed"


class WebhookDelivery(Base):
    """Transactional outbox: rows are written with the change and sent by the worker."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_webhook_deliveries_due", "status", "next_attempt_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=_uuid)
    webhook_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("webhooks.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)  # exact JSON body that gets signed
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status"), default=DeliveryStatus.pending
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
