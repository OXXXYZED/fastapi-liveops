import hashlib
import json

from fastapi import APIRouter, Request, Response, status

from app.api.deps import DbDep, PlayerDep, RedisDep
from app.api.schemas import ClientConfigOut
from app.services import flags as flag_eval
from app.services import remote_config

router = APIRouter(prefix="/v1", tags=["remote config"])

_MAX_ATTRS = 32


@router.get(
    "/config",
    response_model=ClientConfigOut,
    responses={304: {"description": "Not modified (matches If-None-Match)"}},
)
async def get_config(
    request: Request, response: Response, claims: PlayerDep, db: DbDep, redis: RedisDep
) -> ClientConfigOut | Response:
    """Live config + flags evaluated for the calling player.

    Targeting attributes are passed as query params, e.g. `?platform=ios&app_version=1.4.0`.
    """
    attrs = {k: v[:128] for k, v in list(request.query_params.items())[:_MAX_ATTRS]}
    snap = await remote_config.load_snapshot(db, redis, claims.environment_id)
    pid = str(claims.player_id)
    out = ClientConfigOut(
        version=snap["version"],
        values=snap["values"],
        flags={
            f["key"]: flag_eval.evaluate(
                key=f["key"],
                enabled=f["enabled"],
                rollout_percent=f["rollout_percent"],
                rules=f["rules"],
                player_id=pid,
                attrs=attrs,
            )
            for f in snap["flags"]
        },
    )
    body = json.dumps(out.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
    headers = {"ETag": etag, "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=body, media_type="application/json", headers=headers)
