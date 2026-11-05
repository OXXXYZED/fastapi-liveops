"""Outbound webhooks: events are written to an outbox in the caller's transaction and
delivered by the worker with retries and an HMAC signature.

Signature header: ``X-LiveOps-Signature: t=<unix>,v1=<hex hmac_sha256(secret, f"{t}.{body}")>``
"""

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DeliveryStatus, Webhook, WebhookDelivery

EVENT_TYPES = (
    "player.created",
    "player.event",  # custom analytics events sent by game clients
    "experiment.exposure",
    "config.published",
    "leaderboard.score",
)
MAX_ATTEMPTS = 8


def sign(secret: str, body: str, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


async def emit(
    db: AsyncSession, environment_id: uuid.UUID, event: str, data: dict[str, Any]
) -> int:
    """Queue `event` for every active webhook in the environment subscribed to it.

    Does not commit: the delivery rows become visible only if the caller's transaction commits.
    """
    hooks = (
        await db.scalars(
            select(Webhook).where(
                Webhook.environment_id == environment_id,
                Webhook.active.is_(True),
                Webhook.events.overlap([event, "*"]),
            )
        )
    ).all()
    for hook in hooks:
        body = json.dumps(
            {
                "id": str(uuid.uuid4()),
                "type": event,
                "environment_id": str(environment_id),
                "created_at": datetime.now(UTC).isoformat(),
                "data": data,
            },
            separators=(",", ":"),
            default=str,
        )
        db.add(WebhookDelivery(webhook_id=hook.id, event=event, payload=body))
    return len(hooks)


async def deliver_due(db: AsyncSession, http: httpx.AsyncClient, batch: int = 50) -> int:
    """Send due deliveries. Safe to run concurrently on several replicas (SKIP LOCKED)."""
    rows = (
        await db.execute(
            select(WebhookDelivery, Webhook)
            .join(Webhook, Webhook.id == WebhookDelivery.webhook_id)
            .where(
                WebhookDelivery.status == DeliveryStatus.pending,
                WebhookDelivery.next_attempt_at <= datetime.now(UTC),
            )
            .order_by(WebhookDelivery.next_attempt_at)
            .limit(batch)
            .with_for_update(skip_locked=True, of=WebhookDelivery)
        )
    ).all()
    for delivery, hook in rows:
        delivery.attempts += 1
        try:
            r = await http.post(
                hook.url,
                content=delivery.payload,
                headers={
                    "Content-Type": "application/json",
                    "X-LiveOps-Event": delivery.event,
                    "X-LiveOps-Delivery": str(delivery.id),
                    "X-LiveOps-Signature": sign(hook.secret, delivery.payload),
                },
                timeout=10,
            )
            ok, error = r.is_success, None if r.is_success else f"HTTP {r.status_code}"
        except httpx.HTTPError as exc:
            ok, error = False, f"{type(exc).__name__}: {exc}"[:500]
        if ok:
            delivery.status, delivery.last_error = DeliveryStatus.delivered, None
        else:
            delivery.last_error = error
            if delivery.attempts >= MAX_ATTEMPTS:
                delivery.status = DeliveryStatus.failed
            else:  # 30s, 1m, 2m, 4m ... ~1h
                delay = timedelta(seconds=30 * 2 ** (delivery.attempts - 1))
                delivery.next_attempt_at = datetime.now(UTC) + delay
    await db.commit()
    return len(rows)


async def retry_now(db: AsyncSession, delivery_id: uuid.UUID) -> bool:
    res = await db.execute(
        update(WebhookDelivery)
        .where(WebhookDelivery.id == delivery_id)
        .values(status=DeliveryStatus.pending, next_attempt_at=datetime.now(UTC), attempts=0)
        .returning(WebhookDelivery.id)
    )
    await db.commit()
    return res.first() is not None
