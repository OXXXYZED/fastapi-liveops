import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ApiKeyKind

SLUG = r"^[a-z0-9][a-z0-9-]{0,63}$"


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    slug: str = Field(pattern=SLUG)
    name: str = Field(min_length=1, max_length=200)
    environments: list[str] = Field(default_factory=lambda: ["dev", "prod"])


class EnvironmentOut(ORM):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    created_at: datetime


class ProjectOut(ORM):
    id: uuid.UUID
    slug: str
    name: str
    created_at: datetime
    environments: list[EnvironmentOut]


class EnvironmentCreate(BaseModel):
    name: str = Field(pattern=SLUG)


class ApiKeyCreate(BaseModel):
    kind: ApiKeyKind
    name: str = Field(min_length=1, max_length=200)


class ApiKeyOut(ORM):
    id: uuid.UUID
    environment_id: uuid.UUID
    kind: ApiKeyKind
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Plaintext key. Shown only once.")


class AnonymousLogin(BaseModel):
    device_id: str | None = Field(default=None, min_length=8, max_length=128)


class TokenOut(BaseModel):
    player_id: uuid.UUID
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    created: bool


class PlayerOut(ORM):
    id: uuid.UUID
    environment_id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
