# Contributing

Issues and pull requests are welcome.

## Layout

| Path | What |
|---|---|
| `server/` | FastAPI app (`uv`, ruff, mypy `--strict`, pytest against real Postgres + Redis) |
| `admin/` | React admin UI (typed client generated from the server's OpenAPI schema) |
| `sdk/unity/` | Unity package + .NET test project |
| `sdk/dart/` | Dart/Flutter package |
| `loadtest/` | Locust scenario |

## Checks

```bash
docker compose up -d postgres redis
cd server && uv sync && uv run alembic upgrade head
uv run ruff check . && uv run ruff format --check . && uv run mypy app tests && uv run pytest
```

- Changing the server API: run `npm run gen:api` in `admin/` and commit the generated files.
- Adding files to the Unity package: run `python scripts/gen_unity_meta.py`.
- New DB changes need an Alembic migration that also downgrades cleanly.
