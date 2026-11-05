from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LIVEOPS_", env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://liveops:liveops@localhost:5432/liveops"
    redis_url: str = "redis://localhost:6379/0"
    log_level: str = "INFO"

    # Static admin token guarding /admin/*. Replace with real admin accounts later.
    admin_token: SecretStr = SecretStr("dev-admin-token")
    jwt_secret: SecretStr = SecretStr("dev-jwt-secret-change-me-32-bytes!")
    jwt_ttl_seconds: int = 3600

    # Fixed-window rate limit for client (game) API, per API key + client IP.
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    # Directory with the built admin UI (admin/dist); served at /ui when set.
    admin_ui_dir: str | None = None
    # Comma-separated origins allowed to call the API from browsers (e.g. a separately hosted UI).
    cors_origins: str = ""

    cloud_save_max_bytes: int = 256 * 1024
    cloud_save_max_slots: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
