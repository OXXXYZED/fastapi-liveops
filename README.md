# fastapi-liveops

Self-hosted LiveOps backend for games — an open-source alternative to hosted game services.

**Status:** early development (stage 0 — skeleton).

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
