# fastapi-liveops

[![CI](https://github.com/OXXXYZED/fastapi-liveops/actions/workflows/ci.yml/badge.svg)](https://github.com/OXXXYZED/fastapi-liveops/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Self-hosted LiveOps backend for games.** Remote config, feature flags, leaderboards and cloud
saves behind one small FastAPI service — with an admin panel and client SDKs for Unity and Flutter.
Runs on Postgres + Redis; one `docker compose up` to start.

| | |
|---|---|
| **Remote config** | Versioned JSON per environment, optimistic publishing, one-click rollback, ETag caching |
| **Feature flags** | Targeting rules (`eq`, `in`, numeric, semver…), stable percentage rollouts |
| **Leaderboards** | Higher/lower-is-better, best/latest/sum, daily/weekly/monthly seasons, around-me, anti-cheat bounds |
| **Cloud save** | JSON slots with version-based conflict detection and size/slot limits |
| **Players** | Anonymous / device-ID login, Google / Apple / Steam sign-in and account linking, display names, JWT, rate limits |
| **Admin UI** | Manage everything above at `/ui`; admin accounts with viewer/editor/owner roles and an audit log |
| **SDKs** | Unity (UPM, all platforms incl. WebGL) and Dart/Flutter, with offline config cache |

## Quickstart

```bash
git clone https://github.com/OXXXYZED/fastapi-liveops && cd fastapi-liveops
docker compose up --build
```

Open **http://localhost:8000/ui**, choose *Use admin token* and enter `dev-admin-token`, then create
a project and a **client** API key in its `dev` environment. Create named admin accounts under
**Admin users**, or from the CLI:

```bash
docker compose exec server python -m app.cli create-admin you@example.com --role owner
``` API docs: http://localhost:8000/docs.

### From a game

**Unity** — Package Manager → *Add package from git URL*:
`https://github.com/OXXXYZED/fastapi-liveops.git?path=sdk/unity/com.oxxxyzed.liveops`

```csharp
var liveops = LiveOpsUnity.Create("http://localhost:8000", "lo_client_...");
await liveops.FetchConfigAsync();
var lives = liveops.Config.Get("lives", 3);
if (liveops.Config.IsEnabled("new_shop")) { /* ... */ }
await liveops.Leaderboards.SubmitScoreAsync("main", 1200);
await liveops.CloudSave.SaveAsync("main", progress, expectedVersion: 0);
```

**Flutter / Dart** — git dependency on `sdk/dart/liveops_client`:

```dart
final liveops = LiveOpsClient(baseUrl: 'http://localhost:8000', apiKey: 'lo_client_...');
await liveops.init();
await liveops.fetchConfig();
final lives = liveops.config.get('lives', 3);
```

SDK docs: [Unity](sdk/unity/com.oxxxyzed.liveops/README.md) · [Dart/Flutter](sdk/dart/liveops_client/README.md)

### Raw HTTP

```bash
ADMIN='Authorization: Bearer dev-admin-token'
curl -XPOST localhost:8000/admin/projects -H "$ADMIN" -H 'Content-Type: application/json' \
  -d '{"slug":"my-game","name":"My Game"}'                      # → environments[].id
curl -XPOST localhost:8000/admin/environments/$ENV_ID/api-keys -H "$ADMIN" \
  -H 'Content-Type: application/json' -d '{"kind":"client","name":"android"}'   # → key

curl -XPOST localhost:8000/v1/auth/anonymous -H "X-Api-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"device_id":"my-device-0001"}'    # → access_token
curl 'localhost:8000/v1/config?platform=ios&app_version=1.4.0' -H "Authorization: Bearer $TOKEN"
```

## Concepts

| Concept | Description |
|---|---|
| Project | A game. Has environments (`dev`, `prod` by default; add `staging` etc.). |
| Environment | Isolated data space: players, config, flags, leaderboards, saves. |
| API key | `client` (shipped in builds, rate-limited per key+IP) or `server` (secret). Sent as `X-Api-Key`. Plaintext shown once. |
| Player | Created/resumed via `POST /v1/auth/anonymous`; receives a JWT for `/v1/*`. |

### Remote config & flags

- Config is an append-only list of versions; the highest is live. `POST` with `base_version` fails
  with `409` if someone published in between. Rollback publishes a copy of an old version.
- A flag is on for a player when it is `enabled`, **all** rules match the client's attributes
  (query params of `GET /v1/config`), and `sha256("{flag}:{player}") % 100 < rollout_percent`.
- Operators: `eq`, `neq`, `in`, `not_in`, `gte`, `lt`, `semver_gte`, `semver_lt`.
- `GET /v1/config` returns `{version, values, flags: {key: bool}}` with an `ETag`; `If-None-Match` → `304`.

### Player accounts

- `POST /v1/auth/anonymous` — device login (always available).
- `POST /v1/auth/{google|apple|steam}` with `{"token": …}` — Google/Apple ID token or Steam session
  ticket; resumes the player that has the account linked, or creates one.
- `POST /v1/players/me/identities/{provider}` links an account to the current (e.g. anonymous) player,
  so progress follows it to new devices; `409` if it already belongs to another player.
- `PATCH /v1/players/me` `{"display_name": …}` — shown on leaderboards.
- Provider config (Google client IDs, Apple bundle/services IDs, Steam app ID + publisher key) is per
  environment: admin UI → *Player sign-in*.

### Leaderboards

`sort_order` `desc`/`asc` · `update_mode` `best`/`latest`/`sum` · `reset_schedule`
`never`/`daily`/`weekly`/`monthly` (UTC, ISO weeks) · optional `min_score`/`max_score`.
Integer scores within ±2^53. `season` = `current`, `previous` or an id like `2026-W45`.
Ties are ordered by player id.

### Cloud save

`PUT /v1/saves/{slot}` with `{"data": …, "expected_version": N}`: `0` = create only, `N` = must match,
omitted = last write wins. Mismatch → `409` with `current_version`. Limits: 256 KiB, 10 slots (configurable).

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `LIVEOPS_ENV` | `dev` | Outside `dev`, the server refuses to start with default secrets |
| `LIVEOPS_DATABASE_URL` | local Postgres | asyncpg URL |
| `LIVEOPS_REDIS_URL` | `redis://localhost:6379/0` | |
| `LIVEOPS_ADMIN_TOKEN` | `dev-admin-token` | Static owner token for automation / break-glass; `""` disables it |
| `LIVEOPS_ADMIN_SESSION_TTL_SECONDS` | `43200` | Admin login session length |
| `LIVEOPS_JWT_SECRET` | dev value | ≥ 32 random bytes |
| `LIVEOPS_JWT_TTL_SECONDS` | `3600` | |
| `LIVEOPS_RATE_LIMIT_REQUESTS` / `_WINDOW_SECONDS` | `120` / `60` | Fixed window, per API key (+IP) and per player |
| `LIVEOPS_CLOUD_SAVE_MAX_BYTES` / `_MAX_SLOTS` | `262144` / `10` | |
| `LIVEOPS_ADMIN_UI_DIR` | set in image | Built admin UI served at `/ui` |
| `LIVEOPS_CORS_ORIGINS` | empty | Comma-separated, if the UI is hosted elsewhere |
| `FORWARDED_ALLOW_IPS` | `127.0.0.1` | Trusted proxy IPs for `X-Forwarded-For` |

Going to production: **[docs/deployment.md](docs/deployment.md)** (checklist, nginx example,
scaling, load-test numbers). Internals: **[docs/architecture.md](docs/architecture.md)**.

## Development

```bash
docker compose up -d postgres redis
cd server && uv sync && uv run alembic upgrade head && uv run pytest
cd admin && npm ci && npm run dev        # http://localhost:5173/ui
```

See [CONTRIBUTING.md](CONTRIBUTING.md). CI runs lint, strict typing, server tests, both SDKs
end-to-end against a live server, an admin UI browser test and a Docker build.

## Roadmap

- Scheduled config changes and A/B experiments with analytics hooks
- Server-side score validation hooks

## License

[MIT](LICENSE) © OXXXYZED
