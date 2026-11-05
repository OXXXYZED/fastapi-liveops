import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from redis.asyncio import Redis

from app.api import (
    admin,
    admin_users,
    config_admin,
    config_client,
    health,
    leaderboards,
    liveops_admin,
    players,
    saves,
)
from app.core.config import Settings, get_settings
from app.db.session import make_engine, make_sessionmaker
from app.services import worker
from app.services.identity import default_verifiers


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env != "dev":
        for name in ("admin_token", "jwt_secret"):
            if getattr(settings, name).get_secret_value().startswith("dev-"):  # "" is allowed
                raise RuntimeError(f"LIVEOPS_{name.upper()} must be set outside dev")
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = make_engine(settings.database_url)
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
        app.state.settings = settings
        app.state.engine = engine
        app.state.sessionmaker = make_sessionmaker(engine)
        app.state.redis = redis
        http = httpx.AsyncClient(headers={"User-Agent": "fastapi-liveops"})
        app.state.http = http
        app.state.identity_verifiers = default_verifiers(http)
        try:
            async with worker.running(
                app.state.sessionmaker,
                redis,
                http,
                settings.worker_interval_seconds,
                settings.run_worker,
            ):
                yield
        finally:
            await http.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="fastapi-liveops", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(admin_users.auth)
    app.include_router(admin_users.users)
    app.include_router(admin.router)
    app.include_router(players.router)
    app.include_router(config_admin.router)
    app.include_router(config_client.router)
    app.include_router(liveops_admin.router)
    app.include_router(leaderboards.admin)
    app.include_router(leaderboards.client)
    app.include_router(saves.client)
    app.include_router(saves.admin)
    if origins := [o.strip() for o in settings.cors_origins.split(",") if o.strip()]:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["ETag", "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
        )
    if settings.admin_ui_dir:
        _mount_ui(app, Path(settings.admin_ui_dir))
    return app


def _mount_ui(app: FastAPI, root: Path) -> None:
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"admin UI not found at {root}")
    app.mount("/ui/assets", StaticFiles(directory=root / "assets"), name="ui-assets")

    @app.get("/", include_in_schema=False)
    async def _root() -> RedirectResponse:
        return RedirectResponse("/ui/")

    # Top-level files (favicon etc.) are whitelisted at startup: no path joins on user input.
    top_files = {p.name: p for p in root.iterdir() if p.is_file()}

    @app.get("/ui/{path:path}", include_in_schema=False)
    async def _spa(path: str) -> FileResponse:
        # Client-side routing: unknown paths fall back to index.html.
        return FileResponse(top_files.get(path, index))


app = create_app()
