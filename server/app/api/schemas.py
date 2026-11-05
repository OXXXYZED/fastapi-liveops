import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models import AdminRole, ApiKeyKind, ResetSchedule, SortOrder, UpdateMode
from app.services.flags import Condition

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


# --- Remote config & feature flags ---

FLAG_KEY = r"^[a-z0-9][a-z0-9_.-]{0,127}$"


class ConfigPublish(BaseModel):
    values: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)
    base_version: int | None = Field(
        default=None,
        description="If set, publish fails with 409 unless the live version equals this.",
    )


class ConfigVersionOut(ORM):
    version: int
    values: dict[str, Any]
    note: str | None
    created_at: datetime


class ConfigVersionSummary(ORM):
    version: int
    note: str | None
    created_at: datetime


class FlagUpsert(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    enabled: bool = False
    rollout_percent: int = Field(default=100, ge=0, le=100)
    rules: list[Condition] = Field(default_factory=list, max_length=20)


class FlagOut(ORM):
    key: str
    description: str | None
    enabled: bool
    rollout_percent: int
    rules: list[Condition]
    created_at: datetime
    updated_at: datetime


class ClientConfigOut(BaseModel):
    version: int
    values: dict[str, Any]
    flags: dict[str, bool]


# --- Leaderboards ---


class LeaderboardCreate(BaseModel):
    key: str = Field(pattern=FLAG_KEY)
    name: str = Field(min_length=1, max_length=200)
    sort_order: SortOrder = SortOrder.desc
    update_mode: UpdateMode = UpdateMode.best
    reset_schedule: ResetSchedule = ResetSchedule.never
    min_score: int | None = None
    max_score: int | None = None


class LeaderboardUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    min_score: int | None = None
    max_score: int | None = None


class LeaderboardOut(ORM):
    id: uuid.UUID
    key: str
    name: str
    sort_order: SortOrder
    update_mode: UpdateMode
    reset_schedule: ResetSchedule
    min_score: int | None
    max_score: int | None
    created_at: datetime


class ScoreSubmit(BaseModel):
    score: int = Field(ge=-(2**53), le=2**53)


class ScoreSubmitted(BaseModel):
    season: str
    score: int
    rank: int


class EntryOut(BaseModel):
    rank: int
    player_id: uuid.UUID
    score: int
    display_name: str | None = None


class LeaderboardPage(BaseModel):
    key: str
    season: str
    total: int
    entries: list[EntryOut]


class MyRank(BaseModel):
    key: str
    season: str
    total: int
    me: EntryOut | None
    around: list[EntryOut]


# --- Cloud save ---

SLOT = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$"


class SaveWrite(BaseModel):
    data: Any
    expected_version: int | None = Field(
        default=None,
        ge=0,
        description="Optimistic concurrency: 0 = slot must not exist, "
        "N = current version must be N, omitted = overwrite unconditionally.",
    )


class SaveMeta(ORM):
    slot: str
    version: int
    size_bytes: int
    created_at: datetime
    updated_at: datetime


class SaveOut(SaveMeta):
    data: Any


class AdminPlayerOut(ORM):
    id: uuid.UUID
    device_id: str | None
    display_name: str | None
    created_at: datetime
    last_seen_at: datetime


class AdminPlayerPage(BaseModel):
    total: int
    players: list[AdminPlayerOut]


# --- Admin users ---

EMAIL = r"^[^@\s]{1,64}@[^@\s]{1,255}$"


class AdminLogin(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


class AdminMe(BaseModel):
    id: uuid.UUID | None
    email: str
    role: AdminRole


class AdminSession(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: AdminMe


class AdminUserCreate(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    role: AdminRole = AdminRole.viewer


class AdminUserUpdate(BaseModel):
    role: AdminRole | None = None
    disabled: bool | None = None
    password: str | None = Field(default=None, min_length=10, max_length=256)


class AdminUserOut(ORM):
    id: uuid.UUID
    email: str
    role: AdminRole
    disabled: bool
    created_at: datetime
    last_login_at: datetime | None


class AuditEntryOut(ORM):
    id: uuid.UUID
    at: datetime
    actor: str
    method: str
    path: str
    detail: dict[str, Any] | None


class AuditPage(BaseModel):
    total: int
    entries: list[AuditEntryOut]


# --- Player profile & identities ---

DISPLAY_NAME = r"^[\w][\w .\-]{1,22}[\w]$"
PROVIDER = r"^(google|apple|steam)$"


class ProviderLogin(BaseModel):
    token: str = Field(min_length=1, max_length=8192, description="ID token or Steam ticket")


class IdentityOut(ORM):
    provider: str
    email: str | None
    created_at: datetime


class PlayerProfileOut(PlayerOut):
    display_name: str | None
    identities: list[IdentityOut]


class PlayerUpdate(BaseModel):
    display_name: str | None = Field(
        default=None, pattern=DISPLAY_NAME, description="3–24 letters, digits, space, . _ -"
    )


class GoogleConfig(BaseModel):
    client_ids: list[str] = Field(default_factory=list, max_length=20)


class AppleConfig(BaseModel):
    audiences: list[str] = Field(default_factory=list, max_length=20)


class SteamConfig(BaseModel):
    app_id: int | None = None
    web_api_key: str | None = Field(default=None, description="Write-only; returned masked")


class AuthProviders(BaseModel):
    google: GoogleConfig = Field(default_factory=GoogleConfig)
    apple: AppleConfig = Field(default_factory=AppleConfig)
    steam: SteamConfig = Field(default_factory=SteamConfig)
