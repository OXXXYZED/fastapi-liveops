# fastapi-liveops

Self-hosted LiveOps backend for games — an open-source alternative to hosted game services.

**Status:** early development (stage 6 — Flutter/Dart SDK).

## Planned features

- Projects, environments, API keys, player auth (anonymous / device ID → JWT)
- Remote Config & Feature Flags with segment targeting and percentage rollouts
- Leaderboards (Redis-backed, seasons, top-N / around-me)
- Cloud Save with optimistic concurrency
- Unity and Flutter client SDKs
- React admin panel

## Quickstart

```bash
docker compose up --build
curl localhost:8000/readyz
```

OpenAPI docs: http://localhost:8000/docs

## Core concepts

| Concept | Description |
|---|---|
| Project | A game. Has environments (default `dev`, `prod`). |
| Environment | Isolated data space (players, configs, leaderboards). |
| API key | `client` (shipped in builds, rate-limited per key+IP) or `server` (secret, for game servers). Sent as `X-Api-Key`. Stored as SHA-256; plaintext is shown once. |
| Player | Created via `POST /v1/auth/anonymous` (optionally by `device_id`), receives a JWT for player endpoints. |

Admin endpoints (`/admin/*`) use `Authorization: Bearer $LIVEOPS_ADMIN_TOKEN`.

```bash
# create a project and a client key
curl -XPOST localhost:8000/admin/projects -H 'Authorization: Bearer dev-admin-token' \
  -H 'Content-Type: application/json' -d '{"slug":"my-game","name":"My Game"}'
curl -XPOST localhost:8000/admin/environments/$ENV_ID/api-keys -H 'Authorization: Bearer dev-admin-token' \
  -H 'Content-Type: application/json' -d '{"kind":"client","name":"android build"}'

# log a player in
curl -XPOST localhost:8000/v1/auth/anonymous -H "X-Api-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"device_id":"my-device-0001"}'
```

## Remote config & feature flags

- **Config** is an append-only list of versions per environment; the highest version is live.
  Publish with optional `base_version` for optimistic concurrency; rollback publishes a copy of an old version.
- **Flags** have `enabled`, `rollout_percent` (0–100) and `rules` (all must match).
  Operators: `eq`, `neq`, `in`, `not_in`, `gte`, `lt`, `semver_gte`, `semver_lt`.
  Rollout bucketing is `sha256("{flag_key}:{player_id}") % 100` — stable per player, independent across flags.
- Clients call `GET /v1/config?platform=ios&app_version=1.4.0` with the player JWT and get
  `{version, values, flags: {key: bool}}`. Responses carry an `ETag`; send `If-None-Match` to get `304`.
- Snapshots are cached in Redis (5 min TTL) and invalidated on every admin write.

```bash
curl -XPOST localhost:8000/admin/environments/$ENV_ID/config -H 'Authorization: Bearer dev-admin-token' \
  -H 'Content-Type: application/json' -d '{"values":{"lives":3,"shop":{"discount":0.2}}}'
curl -XPUT localhost:8000/admin/environments/$ENV_ID/flags/new_shop -H 'Authorization: Bearer dev-admin-token' \
  -H 'Content-Type: application/json' \
  -d '{"enabled":true,"rollout_percent":25,"rules":[{"attribute":"app_version","op":"semver_gte","value":"1.4.0"}]}'
```

## Leaderboards

| Setting | Values |
|---|---|
| `sort_order` | `desc` (higher is better) / `asc` (lower is better, e.g. race times) |
| `update_mode` | `best` / `latest` / `sum` |
| `reset_schedule` | `never` / `daily` / `weekly` (ISO weeks) / `monthly`, UTC |
| `min_score`, `max_score` | Optional server-side bounds; scores are integers within ±2^53 |

- Postgres (`leaderboard_scores`) is the source of truth; Redis sorted sets are an index that is
  rebuilt automatically if missing. Old seasons expire from Redis but stay queryable.
- Client: `POST /v1/leaderboards/{key}/scores`, `GET /v1/leaderboards/{key}?offset=&limit=`,
  `GET /v1/leaderboards/{key}/me?radius=5`. `season` accepts `current`, `previous` or an id like `2026-W45`.
- Admin: CRUD under `/admin/environments/{id}/leaderboards`, plus removing a player's score.
- Ties are ordered by player id (Redis lexicographic order).

## Cloud save

- Per-player JSON slots: `GET /v1/saves`, `GET|PUT|DELETE /v1/saves/{slot}`.
- `PUT` body: `{"data": ..., "expected_version": N}` — `0` = create only, `N` = must match the
  current version, omitted = last write wins. Mismatch → `409` with `current_version`.
- Limits: `LIVEOPS_CLOUD_SAVE_MAX_BYTES` (256 KiB, encoded JSON) → `413`;
  `LIVEOPS_CLOUD_SAVE_MAX_SLOTS` (10) → `409`. Put a body-size limit on your reverse proxy too.
- Admin read/delete for support: `/admin/environments/{env}/players/{player}/saves`.

## Client SDKs

- **Unity** — [`sdk/unity/com.oxxxyzed.liveops`](sdk/unity/com.oxxxyzed.liveops/README.md), install via git URL
  `https://github.com/OXXXYZED/fastapi-liveops.git?path=sdk/unity/com.oxxxyzed.liveops`.
- **Flutter / Dart** — [`sdk/dart/liveops_client`](sdk/dart/liveops_client/README.md), pure Dart, git dependency.

Both SDKs are tested end-to-end against a live server in CI.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `LIVEOPS_ENV` | `dev` | Outside `dev`, the server refuses to start with default secrets. |
| `LIVEOPS_DATABASE_URL` | local Postgres | asyncpg URL |
| `LIVEOPS_REDIS_URL` | `redis://localhost:6379/0` | |
| `LIVEOPS_ADMIN_TOKEN` | `dev-admin-token` | |
| `LIVEOPS_JWT_SECRET` | dev value | ≥32 bytes recommended |
| `LIVEOPS_JWT_TTL_SECONDS` | `3600` | |
| `LIVEOPS_RATE_LIMIT_REQUESTS` / `_WINDOW_SECONDS` | `120` / `60` | Fixed window, per API key (+IP) and per player |

## Development

```bash
cd server
uv sync
docker compose up -d postgres redis
uv run alembic upgrade head
uv run pytest
```

Lint & types: `uv run ruff check . && uv run ruff format --check . && uv run mypy app tests`

## License

MIT
