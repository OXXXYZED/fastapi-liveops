import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api.deps import AdminDep, DbDep, RedisDep, require_admin, utcnow
from app.api.schemas import (
    FLAG_KEY,
    DeliveryOut,
    ExperimentOut,
    ExperimentResults,
    ExperimentWrite,
    ScheduleCreate,
    ScheduleOut,
    WebhookCreate,
    WebhookCreated,
    WebhookOut,
    WebhookUpdate,
)
from app.models import (
    ConfigSchedule,
    Environment,
    Experiment,
    ExperimentExposure,
    ExperimentStatus,
    ScheduleStatus,
    Webhook,
    WebhookDelivery,
)
from app.services import events, remote_config

router = APIRouter(
    prefix="/admin/environments/{environment_id}",
    tags=["admin: scheduling, experiments, webhooks"],
    dependencies=[Depends(require_admin)],
)
ExpKey = Annotated[str, Path(pattern=FLAG_KEY)]


async def _env(db: DbDep, environment_id: uuid.UUID) -> None:
    if await db.get(Environment, environment_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "environment not found")


# ---------------- scheduled config ----------------


@router.post("/config/schedules", status_code=status.HTTP_201_CREATED, response_model=ScheduleOut)
async def create_schedule(
    environment_id: uuid.UUID, body: ScheduleCreate, db: DbDep, admin: AdminDep
) -> ConfigSchedule:
    await _env(db, environment_id)
    if body.publish_at <= utcnow():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "publish_at must be in the future"
        )
    row = ConfigSchedule(
        environment_id=environment_id,
        values=body.values,
        note=body.note,
        publish_at=body.publish_at,
        created_by=admin.email,
        status=ScheduleStatus.pending,
    )
    db.add(row)
    await db.commit()
    return row


@router.get("/config/schedules", response_model=list[ScheduleOut])
async def list_schedules(environment_id: uuid.UUID, db: DbDep) -> list[ConfigSchedule]:
    rows = await db.scalars(
        select(ConfigSchedule)
        .where(ConfigSchedule.environment_id == environment_id)
        .order_by(ConfigSchedule.publish_at.desc())
        .limit(100)
    )
    return list(rows)


@router.delete("/config/schedules/{schedule_id}", response_model=ScheduleOut)
async def cancel_schedule(
    environment_id: uuid.UUID, schedule_id: uuid.UUID, db: DbDep
) -> ConfigSchedule:
    row = await db.scalar(
        select(ConfigSchedule)
        .where(ConfigSchedule.id == schedule_id, ConfigSchedule.environment_id == environment_id)
        .with_for_update()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "schedule not found")
    if row.status != ScheduleStatus.pending:
        raise HTTPException(status.HTTP_409_CONFLICT, f"schedule is already {row.status}")
    row.status = ScheduleStatus.cancelled
    await db.commit()
    return row


# ---------------- experiments ----------------


async def _exp(db: DbDep, environment_id: uuid.UUID, key: str) -> Experiment:
    exp = await db.scalar(
        select(Experiment).where(Experiment.environment_id == environment_id, Experiment.key == key)
    )
    if exp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "experiment not found")
    return exp


@router.get("/experiments", response_model=list[ExperimentOut])
async def list_experiments(environment_id: uuid.UUID, db: DbDep) -> list[Experiment]:
    rows = await db.scalars(
        select(Experiment)
        .where(Experiment.environment_id == environment_id)
        .order_by(Experiment.created_at.desc())
    )
    return list(rows)


@router.put("/experiments/{key}", response_model=ExperimentOut)
async def upsert_experiment(
    environment_id: uuid.UUID, key: ExpKey, body: ExperimentWrite, db: DbDep, redis: RedisDep
) -> Experiment:
    """Create a draft or edit an experiment. Variants of a running experiment are frozen:
    stop it and create a new one to change the design."""
    await _env(db, environment_id)
    exp = await db.scalar(
        select(Experiment).where(Experiment.environment_id == environment_id, Experiment.key == key)
    )
    data = {
        "description": body.description,
        "traffic_percent": body.traffic_percent,
        "rules": [c.model_dump(mode="json") for c in body.rules],
        "variants": [v.model_dump(mode="json") for v in body.variants],
    }
    if exp is None:
        exp = Experiment(
            environment_id=environment_id, key=key, status=ExperimentStatus.draft, **data
        )
        db.add(exp)
    else:
        if exp.status != ExperimentStatus.draft:
            old = {v["key"] for v in exp.variants}
            if old != {v.key for v in body.variants}:
                raise HTTPException(
                    status.HTTP_409_CONFLICT, "variant keys of a started experiment can't change"
                )
        for k, v in data.items():
            setattr(exp, k, v)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "experiment already exists") from exc
    await remote_config.invalidate(redis, environment_id)
    return exp


@router.post("/experiments/{key}/start", response_model=ExperimentOut)
async def start_experiment(
    environment_id: uuid.UUID, key: ExpKey, db: DbDep, redis: RedisDep
) -> Experiment:
    exp = await _exp(db, environment_id, key)
    if exp.status != ExperimentStatus.draft:
        raise HTTPException(status.HTTP_409_CONFLICT, f"experiment is {exp.status}")
    exp.status, exp.started_at = ExperimentStatus.running, utcnow()
    await db.commit()
    await remote_config.invalidate(redis, environment_id)
    return exp


@router.post("/experiments/{key}/stop", response_model=ExperimentOut)
async def stop_experiment(
    environment_id: uuid.UUID, key: ExpKey, db: DbDep, redis: RedisDep
) -> Experiment:
    exp = await _exp(db, environment_id, key)
    if exp.status != ExperimentStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, f"experiment is {exp.status}")
    exp.status, exp.stopped_at = ExperimentStatus.stopped, utcnow()
    await db.commit()
    await remote_config.invalidate(redis, environment_id)
    return exp


@router.delete("/experiments/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_experiment(
    environment_id: uuid.UUID, key: ExpKey, db: DbDep, redis: RedisDep
) -> None:
    exp = await _exp(db, environment_id, key)
    if exp.status == ExperimentStatus.running:
        raise HTTPException(status.HTTP_409_CONFLICT, "stop the experiment before deleting it")
    await db.delete(exp)
    await db.commit()
    await remote_config.invalidate(redis, environment_id)


@router.get("/experiments/{key}/results", response_model=ExperimentResults)
async def experiment_results(
    environment_id: uuid.UUID, key: ExpKey, db: DbDep
) -> ExperimentResults:
    exp = await _exp(db, environment_id, key)
    rows = await db.execute(
        select(ExperimentExposure.variant, func.count())
        .where(ExperimentExposure.experiment_id == exp.id)
        .group_by(ExperimentExposure.variant)
    )
    counts = {v["key"]: 0 for v in exp.variants} | {v: int(n) for v, n in rows.all()}
    return ExperimentResults(key=key, exposures=counts, total=sum(counts.values()))


# ---------------- webhooks ----------------


async def _hook(db: DbDep, environment_id: uuid.UUID, webhook_id: uuid.UUID) -> Webhook:
    hook = await db.scalar(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.environment_id == environment_id)
    )
    if hook is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "webhook not found")
    return hook


@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(environment_id: uuid.UUID, db: DbDep) -> list[Webhook]:
    rows = await db.scalars(
        select(Webhook).where(Webhook.environment_id == environment_id).order_by(Webhook.created_at)
    )
    return list(rows)


@router.post("/webhooks", status_code=status.HTTP_201_CREATED, response_model=WebhookCreated)
async def create_webhook(
    environment_id: uuid.UUID, body: WebhookCreate, db: DbDep
) -> WebhookCreated:
    await _env(db, environment_id)
    hook = Webhook(
        environment_id=environment_id,
        url=body.url,
        events=sorted(set(body.events)),
        secret="whsec_" + secrets.token_urlsafe(32),
        active=True,
    )
    db.add(hook)
    await db.commit()
    return WebhookCreated(**WebhookOut.model_validate(hook).model_dump(), secret=hook.secret)


@router.patch("/webhooks/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    environment_id: uuid.UUID, webhook_id: uuid.UUID, body: WebhookUpdate, db: DbDep
) -> Webhook:
    hook = await _hook(db, environment_id, webhook_id)
    if body.events is not None:
        WebhookCreate(url=body.url or hook.url, events=body.events)  # validates event names
        hook.events = sorted(set(body.events))
    if body.url is not None:
        hook.url = body.url
    if body.active is not None:
        hook.active = body.active
    await db.commit()
    return hook


@router.delete("/webhooks/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(environment_id: uuid.UUID, webhook_id: uuid.UUID, db: DbDep) -> None:
    await db.delete(await _hook(db, environment_id, webhook_id))
    await db.commit()


@router.post("/webhooks/{webhook_id}/test", status_code=status.HTTP_202_ACCEPTED)
async def test_webhook(
    environment_id: uuid.UUID, webhook_id: uuid.UUID, db: DbDep
) -> dict[str, str]:
    """Queue a `ping` delivery to this webhook only."""
    hook = await _hook(db, environment_id, webhook_id)
    db.add(
        WebhookDelivery(
            webhook_id=hook.id,
            event="ping",
            payload='{"type":"ping","data":{}}',
        )
    )
    await db.commit()
    return {"status": "queued"}


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def list_deliveries(
    environment_id: uuid.UUID,
    webhook_id: uuid.UUID,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[WebhookDelivery]:
    await _hook(db, environment_id, webhook_id)
    rows = await db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(limit)
    )
    return list(rows)


@router.post("/webhooks/{webhook_id}/deliveries/{delivery_id}/retry", status_code=202)
async def retry_delivery(
    environment_id: uuid.UUID, webhook_id: uuid.UUID, delivery_id: uuid.UUID, db: DbDep
) -> dict[str, str]:
    await _hook(db, environment_id, webhook_id)
    if not await events.retry_now(db, delivery_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "delivery not found")
    return {"status": "queued"}
