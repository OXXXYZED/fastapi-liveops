# fastapi-liveops

Self-hosted LiveOps backend for games — an open-source alternative to hosted game services.

**Status:** early development (stage 1 — core: projects, API keys, player auth).

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

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `LIVEOPS_ENV` | `dev` | Outside `dev`, the server refuses to start with default secrets. |
| `LIVEOPS_DATABASE_URL` | local Postgres | asyncpg URL |
| `LIVEOPS_REDIS_URL` | `redis://localhost:6379/0` | |
| `LIVEOPS_ADMIN_TOKEN` | `dev-admin-token` | |
| `LIVEOPS_JWT_SECRET` | dev value | ≥32 bytes recommended |
| `LIVEOPS_JWT_TTL_SECONDS` | `3600` | |
| `LIVEOPS_RATE_LIMIT_REQUESTS` / `_WINDOW_SECONDS` | `120` / `60` | Fixed window |

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
