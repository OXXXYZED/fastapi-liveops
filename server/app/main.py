import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api import admin, config_admin, config_client, health, leaderboards, players, saves
from app.core.config import Settings, get_settings
from app.db.session import make_engine, make_sessionmaker


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env != "dev":
        for name in ("admin_token", "jwt_secret"):
            if getattr(settings, name).get_secret_value().startswith("dev-"):
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
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="fastapi-liveops", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(players.router)
    app.include_router(config_admin.router)
    app.include_router(config_client.router)
    app.include_router(leaderboards.admin)
    app.include_router(leaderboards.client)
    app.include_router(saves.client)
    app.include_router(saves.admin)
    return app


app = create_app()
